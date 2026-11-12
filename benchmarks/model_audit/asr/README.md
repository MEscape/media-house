# ASR benchmark (German, CPU) — audio_intelligence

Measures candidate speech models for `audio_intelligence` on the owner's own German footage,
locally. Context: `docs/model_audit/PIPELINE_AUDIT.md` (ASR row) and
`docs/model_audit/MODEL_RESEARCH.md` §1.

**NDA:** audio, transcripts, results and error examples are client speech. They are written only
to `--out` (default `~/.media_house_scratch/benchmarks/asr/`), never into this repository, and
nothing is sent to any service. Only model weights are downloaded (public Hugging Face hub, no
token).

## What it runs

Each candidate goes through WhisperX the way `infrastructure/whisperx_engine.py` does:
`whisperx.load_model(model, "cpu", compute_type="int8")` with WhisperX's default ASR options
(beam 5, best_of 5, temperature fallback, `condition_on_previous_text=False`) and pyannote VAD
(onset 0.500 / offset 0.363), then `transcribe(samples, batch_size=16, language="de",
chunk_size=30)` on 16 kHz mono PCM. Differences from the pipeline, on purpose:

* threads pinned to 8 (CTranslate2 `cpu_threads` and `torch.set_num_threads`); the pipeline
  passes nothing, so WhisperX uses 4 CTranslate2 threads;
* forced alignment is not run (it does not change the words);
* each candidate runs in its own process, one after another, with a 30 s cool-down.

| Name | Repo @ revision | Licence | Notes |
| --- | --- | --- | --- |
| `large-v3` | `Systran/faster-whisper-large-v3@edaa852` | MIT | pipeline default, same HF cache |
| `large-v3-turbo` | `dropbox-dash/faster-whisper-large-v3-turbo@0a363e9` | MIT | faster-whisper's own `large-v3-turbo` alias (`mobiuslabsgmbh/...` now redirects here) |
| `primeline-turbo-de` | `primeline/whisper-large-v3-turbo-german@9e7012d` | Apache-2.0 | transformers weights, converted locally to CT2 (float16 on disk, int8 at load like the baseline); `tokenizer.json` built from the repo's own vocab |

Skipped: `nvidia/parakeet-tdt-0.6b-v3` — needs `onnx-asr` or NeMo, neither installed (no new
dependencies allowed for this benchmark).

## Data and metrics

Sample (`SAMPLE` in `bench_asr.py`): four number-dense passages from four different
`*talking*` takes, 254.8 s in total, cut with ffmpeg to 16 kHz mono WAV.

There is no verbatim transcript, so the reference is a **proxy**: the spoken text of the
owner's script (`SPRECHTEXT` blocks; stage directions and `{SLOT}`s removed).

* **Script-WER (proxy)** — edits ÷ words of the best-matching contiguous script span
  (semi-global edit distance; text normalised: lower case, no punctuation, `€`→`euro`,
  thousands dot dropped). Two variants: the whole clip against one span (*contiguous*), and
  every hypothesis sentence against its own best span (*sentence*; tolerates retakes and
  reordering). Ad-libs and deviations from the script count as errors for every model alike:
  valid for **ranking**, not as absolute accuracy.
* **Numeric accuracy k/n** — scripted numbers that also appear in the script's fact tables and
  carry a decimal comma or a unit (Euro, Schuss, Meter, Gramm, Sekunden), counted only where
  the script passage is in the sample (inside any model's whole-clip span). A hit needs the
  same digits in German format (decimal comma; thousands dot optional) followed by the unit.
  Spelled-out numbers and `79.99` are misses. The span over-covers: scripted numbers the owner
  did not say in that window count as misses for every model.
* **WER vs large-v3** — word disagreement with the baseline's output (the baseline is compared
  with `large-v3-turbo`). Not accuracy, but it bounds how much any ranking can matter.
* **RTF wall** — transcription wall time ÷ audio time (excludes load). **CPU s/audio s** —
  process CPU time (all threads) ÷ audio time; less sensitive to other load on the machine.
  **Load s** — `load_model` (CT2 model + VAD). **Peak RAM** — peak working set of the run
  process (`GetProcessMemoryInfo`; Windows only, psutil is not installed). **CPU busy
  before** — machine-wide load over 3 s before the run.

## Findings (2026-11-11, numbers only)

The owner paraphrases the script heavily, so script-WER (proxy) is ≈ 1.0 for every model and
moves by ±0.13 between models that disagree on only 2.7 % of words: **it cannot rank these
models**. A verbatim reference (MODEL_RESEARCH §1) is still needed for real WER and for
disfluency recall.

| model | script-WER proxy contig / sentence | numeric k/n | WER vs large-v3 | RTF wall | CPU s/audio s | load s | peak RAM GB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| large-v3 | 1.218 / 0.957 | 8/13 | (0.027 vs turbo) | 1.054 | 6.11 | 38.8 | 4.36 |
| large-v3-turbo | 1.085 / 1.046 | 8/13 | 0.027 | 0.681 | 3.97 | 30.1 | 2.82 |
| primeline-turbo-de | 1.206 / 1.184 | 6/13 | 0.050 | 0.766 | 4.07 | 35.0 | 2.82 |

5 of the 13 numeric entities were not said in the sampled windows (missed by all three). Other
agents' jobs loaded the machine (45-56 % busy before the runs); an earlier set of runs under
heavier load gave RTF 1.26 (large-v3) and 1.39 (turbo), so wall-clock numbers carry about
±30 %. The `all` subcommand once failed in a background shell with WinError 4551 (Windows
application control blocked `libtorchaudio`); running `run <name>` per model in the
foreground worked.

## Run

```powershell
cd benchmarks/model_audit/asr
# weights (once); behind TLS-inspecting antivirus set REQUESTS_CA_BUNDLE to a bundle with its CA
uv run hf download Systran/faster-whisper-large-v3 --revision edaa852ec7e145841d8ffdb056a99866b5f0a478
uv run hf download dropbox-dash/faster-whisper-large-v3-turbo --revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
uv run hf download primeline/whisper-large-v3-turbo-german --revision 9e7012dad9247f07553aac8a99c4d50ad7c3d36d
uv run python bench_asr.py prepare --cam C:/Users/L14/Downloads/yt-video/01_cam
uv run python bench_asr.py convert
uv run python bench_asr.py all --script C:/Users/L14/Downloads/yt-video/00_kontext/lidl-nico-transcript.md
uv run python script_wer.py --selftest   # scorer checks
```

Outputs in `--out`: `audio/*.wav`, `models/` (converted CT2), `runs/<name>.json` (segments,
timings, versions), `results.json`, `results.md` (table + error examples). Standard output
prints only the table unless `score --examples` is given.
