"""Measured German ASR benchmark for audio_intelligence candidates (CPU, CTranslate2 int8).

Every candidate runs through WhisperX exactly like ``infrastructure/whisperx_engine.py``:
``whisperx.load_model(model, "cpu", compute_type="int8")`` (pyannote VAD, onset 0.500 /
offset 0.363, WhisperX default ASR options: beam 5, best_of 5, temperature fallback,
``condition_on_previous_text=False``) and ``model.transcribe(samples, batch_size=16,
language="de", chunk_size=30)``. Deviation from the pipeline: threads are pinned to 8 for
CTranslate2 (pipeline: WhisperX default 4) and torch, so timings are comparable. Forced
alignment is not run (it does not change the words).

Subcommands (all outputs go to ``--out``, outside the repository, because they hold client
speech)::

    cd benchmarks/model_audit/asr
    uv run python bench_asr.py prepare --cam C:/Users/L14/Downloads/yt-video/01_cam
    uv run python bench_asr.py convert      # primeline -> CT2
    uv run python bench_asr.py all --script <script.md>   # every candidate, then score
    uv run python bench_asr.py run large-v3   # one candidate
    uv run python bench_asr.py score --script <script.md> [--examples]

Models are fetched beforehand with ``hf download <repo> --revision <rev>`` (see README); runs
use local files only. Standard output carries only numbers unless ``--examples`` is given.
"""

# ruff: noqa: T201, S101, S603, S607, TID251
# Developer benchmark (same policy as scripts/**): prints, asserts, runs ffmpeg/itself.

import argparse
import ctypes
import json
import subprocess
import sys
import time
import wave
from collections import Counter
from ctypes import wintypes
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import script_wer

OUT = Path.home() / ".media_house_scratch" / "benchmarks" / "asr"
THREADS = 8
#: (camera file, start s, duration s): number-dense passages of four different talking takes.
SAMPLE = [
    ("lidlnico_talking_6.MP4", 0, 80),
    ("lidlnico_talking_9.MP4", 0, 58),
    ("lidlnico_talking_13.MP4", 0, 57),
    ("lidlnico_talking_12.MP4", 110, 60),
]
BASELINE, SECOND = "large-v3", "large-v3-turbo"
CANDIDATES: dict[str, dict[str, str]] = {
    "large-v3": {
        "repo": "Systran/faster-whisper-large-v3",
        "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "license": "MIT",
    },
    "large-v3-turbo": {  # target of faster-whisper's own "large-v3-turbo" alias
        "repo": "dropbox-dash/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "license": "MIT",
    },
    "primeline-turbo-de": {  # transformers weights, converted locally by `convert`
        "repo": "primeline/whisper-large-v3-turbo-german",
        "revision": "9e7012dad9247f07553aac8a99c4d50ad7c3d36d",
        "license": "Apache-2.0",
        "converted": "primeline-whisper-large-v3-turbo-german-9e7012d-ct2-fp16",
    },
}


def clips(out: Path) -> list[Path]:
    return [out / "audio" / f"{Path(name).stem}_{start}s.wav" for name, start, _ in SAMPLE]


def prepare(cam: Path, out: Path) -> None:
    (out / "audio").mkdir(parents=True, exist_ok=True)
    for (name, start, length), wav in zip(SAMPLE, clips(out), strict=True):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(length), "-i",
             str(cam / name), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
             str(wav)],
            check=True,
        )  # fmt: skip
        print(wav.name)


def model_path(name: str, out: Path) -> Path:
    spec = CANDIDATES[name]
    if "converted" in spec:
        return out / "models" / spec["converted"]
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(spec["repo"], revision=spec["revision"], local_files_only=True))


def convert(out: Path) -> None:
    """primeline transformers checkpoint -> CTranslate2 (float16 on disk, loaded as int8 like
    the Systran baseline). Its repo has no tokenizer.json, which faster-whisper needs (else it
    falls back to whisper-tiny's tokenizer), so it is built from the repo's own vocab/merges."""
    from ctranslate2.converters import TransformersConverter
    from huggingface_hub import snapshot_download
    from transformers import WhisperTokenizerFast

    spec = CANDIDATES["primeline-turbo-de"]
    src = snapshot_download(spec["repo"], revision=spec["revision"], local_files_only=True)
    dst = model_path("primeline-turbo-de", out)
    converter = TransformersConverter(
        src, copy_files=["preprocessor_config.json"], low_cpu_mem_usage=True
    )
    converter.convert(str(dst), quantization="float16", force=True)
    WhisperTokenizerFast.from_pretrained(src).backend_tokenizer.save(str(dst / "tokenizer.json"))
    print(dst)


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_ram_gb() -> float:
    """Peak working set of this process (Win32 GetProcessMemoryInfo; psutil is not installed).
    shortcut: Windows only, swap for psutil if the benchmark must run elsewhere."""
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    get_info = ctypes.windll.psapi.GetProcessMemoryInfo  # type: ignore[attr-defined]
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
    ok = get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    assert ok, "GetProcessMemoryInfo failed"
    return float(counters.PeakWorkingSetSize) / 2**30


def cpu_busy(seconds: float = 3.0) -> float:
    """Machine-wide CPU busy share over ``seconds`` (Win32 GetSystemTimes)."""

    def sample() -> tuple[int, int]:
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        value = lambda ft: ft.dwHighDateTime << 32 | ft.dwLowDateTime  # noqa: E731
        return value(idle), value(kernel) + value(user)  # kernel time includes idle time

    idle0, total0 = sample()
    time.sleep(seconds)
    idle1, total1 = sample()
    return 1 - (idle1 - idle0) / max(1, total1 - total0)


def read_wav(path: Path) -> np.ndarray[Any, np.dtype[np.float32]]:
    with wave.open(str(path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def run(name: str, out: Path) -> None:
    busy_before = cpu_busy()
    import torch
    import whisperx

    torch.set_num_threads(THREADS)
    path = model_path(name, out)
    started = time.perf_counter()
    model = whisperx.load_model(
        str(path), "cpu", compute_type="int8", threads=THREADS, local_files_only=True
    )
    load_s = time.perf_counter() - started
    results = []
    for wav in clips(out):
        samples = read_wav(wav)
        started, cpu_started = time.perf_counter(), time.process_time()
        result = model.transcribe(samples, batch_size=16, language="de", chunk_size=30)
        wall = time.perf_counter() - started
        cpu = time.process_time() - cpu_started  # all threads; robust to other machine load
        results.append({
            "clip": wav.stem,
            "audio_s": len(samples) / 16000,
            "wall_s": wall,
            "cpu_s": cpu,
            "segments": [
                {"start": s["start"], "end": s["end"], "text": s["text"]}
                for s in result["segments"]
            ],
        })  # fmt: skip
        print(f"{name} {wav.stem}: RTF {wall / (len(samples) / 16000):.3f}", flush=True)
    record = {
        "name": name,
        **CANDIDATES[name],
        "model_path": str(path),
        "threads": THREADS,
        "compute_type": "int8",
        "cpu_busy_before": busy_before,
        "load_s": load_s,
        "peak_ram_gb": peak_ram_gb(),
        "versions": {
            p: metadata.version(p) for p in ("whisperx", "faster-whisper", "ctranslate2", "torch")
        },
        "clips": results,
    }
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "runs" / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), "utf-8"
    )
    print(f"{name}: load {load_s:.1f}s, peak {record['peak_ram_gb']:.2f} GB", flush=True)


def score(script_md: Path, out: Path, show_examples: bool) -> None:
    markdown = script_md.read_text("utf-8")
    ref = script_wer.words(script_wer.spoken_script(markdown))
    scored_entities = script_wer.entities(ref, script_wer.fact_numbers(markdown))
    runs = {
        p.stem: json.loads(p.read_text("utf-8"))
        for p in sorted((out / "runs").glob("*.json"))
        if p.stem in CANDIDATES
    }
    # per model, per clip: whole-clip span alignment + per-sentence alignment
    aligned: dict[str, dict[str, Any]] = {}
    # script positions inside any model's whole-clip span, per clip (the passage the clip
    # covers; per-sentence matches are too weak because the owner paraphrases the script)
    covered: dict[str, set[int]] = {}
    for name, rec in runs.items():
        for clip in rec["clips"]:
            text = " ".join(s["text"] for s in clip["segments"])
            hyp = script_wer.words(text)
            whole = script_wer.best_span(hyp, ref)
            covered.setdefault(clip["clip"], set()).update(range(whole[1], whole[2]))
            parts = []
            for sentence in script_wer.sentences(text):
                h = script_wer.words(sentence)
                edits, start, end = script_wer.best_span(h, ref)
                parts.append((h, edits, start, end))
            aligned.setdefault(name, {})[clip["clip"]] = (hyp, whole, parts)
    rows, examples = [], {}
    disagreements: dict[str, list[str]] = {}
    for name, rec in runs.items():
        edits = span = sent_edits = sent_span = hits = wanted_n = vs_base = base_n = 0
        snippets = []
        for clip in rec["clips"]:
            hyp, (w_edits, w_start, w_end), parts = aligned[name][clip["clip"]]
            other = BASELINE if name != BASELINE else SECOND
            if other in aligned:  # disagreement with the baseline (for it: 2nd model)
                base = aligned[other][clip["clip"]][0]
                vs_base += script_wer.edit_distance(base, hyp)
                base_n += len(base)
                disagreements.setdefault(name, []).extend(
                    f"{clip['clip']}: {d}" for d in script_wer.diff_snippets(base, hyp)
                )
            edits, span = edits + w_edits, span + (w_end - w_start)
            for h, e, s, t in parts:
                sent_edits, sent_span = sent_edits + e, sent_span + (t - s)
                if e > 0 and t > s and e / (t - s) <= 0.5:  # scripted sentence, recognition slip
                    snippets.append((e, clip["clip"], script_wer.diff_snippets(ref[s:t], h)))
            wanted = Counter(
                (n, u) for p, n, u in scored_entities if p in covered.get(clip["clip"], set())
            )
            hits += script_wer.entity_hits(hyp, wanted)
            wanted_n += sum(wanted.values())
        audio = sum(c["audio_s"] for c in rec["clips"])
        wall = sum(c["wall_s"] for c in rec["clips"])
        cpu = sum(c["cpu_s"] for c in rec["clips"])
        rows.append({
            "model": name,
            "repo": f"{rec['repo']}@{rec['revision'][:7]}",
            "license": rec["license"],
            "script_wer_proxy_contiguous": edits / max(1, span),
            "script_wer_proxy_sentence": sent_edits / max(1, sent_span),
            "numeric": f"{hits}/{wanted_n}",
            "wer_vs_other": vs_base / max(1, base_n),
            "rtf": wall / audio,
            "cpu_s_per_audio_s": cpu / audio,
            "load_s": rec["load_s"],
            "peak_ram_gb": rec["peak_ram_gb"],
            "cpu_busy_before": rec["cpu_busy_before"],
            "audio_s": audio,
        })  # fmt: skip
        snippets.sort(key=lambda x: -x[0])
        examples[name] = [f"{c}: {' | '.join(d[:3])}" for _, c, d in snippets[:8]]
    header = (
        "| model | repo@rev | license | script-WER proxy (contig / sentence) | numeric k/n"
        f" | WER vs {BASELINE} ({SECOND} for it)"
        " | RTF wall | CPU s/audio s | load s | peak RAM GB | CPU busy before |"
    )
    lines = [header, "|" + " --- |" * 11]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['repo']} | {r['license']}"
            f" | {r['script_wer_proxy_contiguous']:.3f}"
            f" / {r['script_wer_proxy_sentence']:.3f} | {r['numeric']}"
            f" | {r['wer_vs_other']:.3f}"
            f" | {r['rtf']:.3f} | {r['cpu_s_per_audio_s']:.2f} |"
            f" {r['load_s']:.1f} | {r['peak_ram_gb']:.2f} | {r['cpu_busy_before']:.0%} |"
        )
    table = "\n".join(lines)
    (out / "results.json").write_text(
        json.dumps(
            {"rows": rows, "examples": examples, "disagreements": disagreements},
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    ex_md = "\n".join(
        f"\n### {n}\n" + "\n".join(f"- {e}" for e in ex) for n, ex in examples.items()
    )
    ex_md += "\n\n## Disagreements (reference model words -> this model's words)\n" + "\n".join(
        f"\n### {n}\n" + "\n".join(f"- {e}" for e in ex) for n, ex in disagreements.items()
    )
    (out / "results.md").write_text(
        f"{table}\n\n## Error examples (client text)\n{ex_md}\n", "utf-8"
    )
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(table)
    if show_examples:
        print(ex_md)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=OUT)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").add_argument("--cam", type=Path, required=True)
    sub.add_parser("convert")
    sub.add_parser("run").add_argument("name", choices=list(CANDIDATES))
    for cmd in ("all", "score"):
        p = sub.add_parser(cmd)
        p.add_argument("--script", type=Path, required=True)
        p.add_argument("--examples", action="store_true")
        if cmd == "all":
            p.add_argument("--cooldown", type=float, default=30.0)
    args = parser.parse_args()
    if args.cmd == "prepare":
        prepare(args.cam, args.out)
    elif args.cmd == "convert":
        convert(args.out)
    elif args.cmd == "run":
        run(args.name, args.out)
    elif args.cmd == "score":
        score(args.script, args.out, args.examples)
    else:  # sequential, one fresh process per model: comparable timings and a clean peak RAM
        for i, name in enumerate(CANDIDATES):
            if i:
                time.sleep(args.cooldown)
            cmd = [sys.executable, __file__, "--out", str(args.out), "run", name]
            subprocess.run(cmd, check=True)
        score(args.script, args.out, args.examples)


if __name__ == "__main__":
    main()
