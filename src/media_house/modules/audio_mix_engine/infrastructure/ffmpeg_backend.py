"""The default audio backend: FFmpeg, driven through the ``ProcessRunner`` port.

Renders stems and the final mix from filter graphs (``ffmpeg_graph.py``), measures EBU R128
loudness, loudness range, true peak and sample peak, finds silences, and compares two files. The
tool's own version is part of the backend identity, so a render made by another FFmpeg is a
different render (its hash changes). A missing filter is reported through ``capabilities`` before
any rendering; nothing is ever swapped for another processor.
"""

import re
from collections.abc import Sequence
from pathlib import Path

from media_house.core.application.ports import ProcessResult, ProcessRunner, ProcessSpec
from media_house.modules.audio_mix_engine.application.ports import (
    BackendCapabilities,
    MixRequest,
    StemRequest,
)
from media_house.modules.audio_mix_engine.domain.errors import BackendFailed
from media_house.modules.audio_mix_engine.domain.result import Measurement
from media_house.modules.audio_mix_engine.infrastructure.ffmpeg_graph import (
    mix_graph,
    source_paths,
    stem_graph,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError

#: Bumped when a graph or parser changes in a way that changes results.
#: 2: the master limiter runs oversampled (true peak).
BACKEND_REVISION = 2

#: Backend-neutral operation -> the FFmpeg filters it needs.
_NEEDS: dict[str, tuple[str, ...]] = {
    "trim": ("atrim", "asetpts"),
    "gain": ("volume",),
    "fade": ("afade",),
    "delay": ("adelay",),
    "sum": ("amix",),
    "resample": ("aresample", "aformat"),
    "pan": ("pan",),
    "tempo": ("atempo",),
    "envelope": ("volume",),
    "compressor": ("acompressor",),
    "limiter": ("alimiter",),
    "eq.highpass": ("highpass",),
    "eq.lowpass": ("lowpass",),
    "eq.peaking": ("equalizer",),
    "eq.lowshelf": ("lowshelf",),
    "eq.highshelf": ("highshelf",),
    "eq.notch": ("bandreject",),
    "measure.loudness": ("ebur128", "astats"),
    "measure.range": ("atrim", "ebur128"),
    "measure.level": ("aselect", "ebur128"),
    "silence.detect": ("silencedetect",),
    "null_test": ("amerge", "pan", "astats"),
}

_FILTER_LINE = re.compile(r"^\s*[A-Z.]{1,3}\s+(\w+)\s+\S+->\S+")
_VERSION = re.compile(r"ffmpeg version (\S+)")
_INTEGRATED = re.compile(r"^\s*I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS", re.MULTILINE)
_RANGE = re.compile(r"^\s*LRA:\s+(-?\d+(?:\.\d+)?)\s+LU", re.MULTILINE)
_TRUE_PEAK = re.compile(r"True peak:\s+Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS", re.MULTILINE)
_SAMPLE_PEAK = re.compile(r"Peak level dB:\s+(-?inf|-?\d+(?:\.\d+)?)")
_SAMPLES = re.compile(r"Number of samples:\s+(\d+)")
_STREAM = re.compile(r"Audio: (\w+)[^,\n]*, (\d+) Hz, ([\w.()]+)")
_MOMENTARY = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_START = re.compile(r"silence_start:\s+(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s+(-?\d+(?:\.\d+)?)")
_CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "5.1": 6, "7.1": 8}
#: ebur128 reports this for programmes that never pass its gate.
_GATED = -70.0


def _number(pattern: re.Pattern[str], text: str) -> float | None:
    matches = pattern.findall(text)
    if not matches or matches[-1] in {"-inf", "inf"}:
        return None
    return float(matches[-1])


class FfmpegAudioBackend:
    def __init__(self, runner: ProcessRunner, executable: str = "ffmpeg") -> None:
        self._runner = runner
        self._exe = executable
        self._capabilities: BackendCapabilities | None = None

    # --- capabilities -----------------------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        if self._capabilities is None:
            version = self._run(["-hide_banner", "-version"], None, "read the version")
            filters = self._run(["-hide_banner", "-filters"], None, "list the filters")
            have = {
                m.group(1)
                for line in filters.stdout.splitlines()
                if (m := _FILTER_LINE.match(line))
            }
            found = _VERSION.search(version.stdout)
            identity = f"ffmpeg {found.group(1) if found else 'unknown'} r{BACKEND_REVISION}"
            ops = frozenset(op for op, needs in _NEEDS.items() if set(needs) <= have)
            self._capabilities = BackendCapabilities(identity, ops)
        return self._capabilities

    # --- rendering --------------------------------------------------------------------------
    def render_stem(self, request: StemRequest, cancellation: CancellationToken) -> None:
        order, graph = stem_graph(request)
        script = request.destination.with_suffix(".filtergraph.txt")
        script.write_text(graph, encoding="utf-8")
        args = ["-nostdin", "-loglevel", "error", "-y"]
        for path in source_paths(order, dict(request.sources)):
            args += ["-i", str(path)]
        args += [
            "-/filter_complex", str(script), "-map", "[out]",
            "-c:a", "pcm_f32le", "-ar", str(request.sample_rate), "-ac", "2",
            str(request.destination),
        ]  # fmt: skip
        self._run(args, cancellation, f"render the {request.bus.bus_id} stem")

    def render_mix(self, request: MixRequest, cancellation: CancellationToken) -> None:
        script = request.destination.with_suffix(".filtergraph.txt")
        script.write_text(mix_graph(request), encoding="utf-8")
        args = ["-nostdin", "-loglevel", "error", "-y"]
        for stem in request.stems:
            args += ["-i", str(stem)]
        args += [
            "-/filter_complex", str(script), "-map", "[out]",
            "-c:a", request.render.codec, "-ar", str(request.render.sample_rate),
            "-ac", str(request.render.channels), str(request.destination),
        ]  # fmt: skip
        self._run(args, cancellation, "render the mix")

    # --- measuring --------------------------------------------------------------------------
    def attach_audio(
        self, picture: Path, audio: Path, destination: Path, cancellation: CancellationToken
    ) -> None:
        self._run(
            ["-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(picture), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
             "-shortest", "-movflags", "+faststart", str(destination)],
            cancellation, "attach the mix",
        )  # fmt: skip

    def measure(self, path: Path, cancellation: CancellationToken) -> Measurement:
        log = self._run(
            ["-nostats", "-hide_banner", "-i", str(path), "-vn", "-af",
             "ebur128=peak=true:framelog=quiet,astats=measure_perchannel=none",
             "-f", "null", "-"],
            cancellation, "measure the loudness",
        ).stderr  # fmt: skip
        stream = _STREAM.search(log)
        samples = _SAMPLES.findall(log)
        if stream is None or not samples or "Integrated loudness" not in log:
            raise BackendFailed("measure the loudness", "the measurement could not be read back")
        integrated = _number(_INTEGRATED, log)
        measurable = integrated is not None and integrated > _GATED
        return Measurement(
            integrated_lufs=integrated if measurable else None,
            loudness_range_lu=_number(_RANGE, log) if measurable else None,
            true_peak_dbtp=_number(_TRUE_PEAK, log),
            sample_peak_dbfs=_number(_SAMPLE_PEAK, log),
            duration_samples=int(samples[-1]),
            sample_rate=int(stream.group(2)),
            channels=_CHANNELS.get(stream.group(3), 0),
            codec=stream.group(1),
        )

    def measure_range(
        self, path: Path, start_seconds: float, end_seconds: float, cancellation: CancellationToken
    ) -> float | None:
        # Seek on the input: a range of a long camera file decodes only that range.
        span = ["-ss", f"{start_seconds:.6f}", "-t", f"{max(0.0, end_seconds - start_seconds):.6f}"]
        return self._integrated(path, "ebur128=framelog=quiet", cancellation, span)

    def peak_loudness(
        self, path: Path, start_seconds: float, end_seconds: float, cancellation: CancellationToken
    ) -> float | None:
        span = ["-ss", f"{start_seconds:.6f}", "-t", f"{max(0.0, end_seconds - start_seconds):.6f}"]
        # padded to one full 400 ms window, so an effect shorter than that is still measured
        log = self._run(
            ["-nostats", "-hide_banner", *span, "-i", str(path), "-vn", "-af",
             "apad=whole_dur=0.5,ebur128=framelog=info", "-f", "null", "-"],
            cancellation, "measure an effect",
        ).stderr  # fmt: skip
        peak = max((float(v) for v in _MOMENTARY.findall(log)), default=None)
        return peak if peak is not None and peak > _GATED else None

    def loudness_over(
        self, path: Path, ranges: Sequence[tuple[float, float]], cancellation: CancellationToken
    ) -> float | None:
        if not ranges:
            return None
        select = "+".join(f"between(t,{a:.6f},{b:.6f})" for a, b in ranges)
        graph = f"aselect='{select}',asetpts=N/SR/TB,ebur128=framelog=quiet"
        return self._integrated(path, graph, cancellation)

    def silences(
        self, path: Path, threshold_db: float, min_seconds: float, cancellation: CancellationToken
    ) -> tuple[tuple[float, float], ...]:
        log = self._run(
            ["-nostats", "-hide_banner", "-i", str(path), "-vn", "-af",
             f"silencedetect=n={threshold_db:g}dB:d={min_seconds:g}", "-f", "null", "-"],
            cancellation, "look for silence",
        ).stderr  # fmt: skip
        starts = [float(x) for x in _SILENCE_START.findall(log)]
        ends = [float(x) for x in _SILENCE_END.findall(log)]
        if len(starts) > len(ends):  # silent until the end of the file
            duration = self.measure(path, cancellation)
            ends.append(duration.duration_samples / max(1, duration.sample_rate))
        return tuple((max(0.0, s), e) for s, e in zip(starts, ends, strict=False))

    def null_test(self, a: Path, b: Path, cancellation: CancellationToken) -> float | None:
        log = self._run(
            ["-nostats", "-hide_banner", "-i", str(a), "-i", str(b), "-filter_complex",
             "[0:a][1:a]amerge=inputs=2,pan=stereo|c0=c0-c2|c1=c1-c3,"
             "astats=measure_perchannel=none",
             "-f", "null", "-"],
            cancellation, "compare two renders",
        ).stderr  # fmt: skip
        matches = _SAMPLE_PEAK.findall(log)
        if not matches:
            return None
        return -200.0 if matches[-1] in {"-inf", "inf"} else float(matches[-1])

    # --- plumbing ---------------------------------------------------------------------------
    def _integrated(
        self, path: Path, graph: str, cancellation: CancellationToken, span: Sequence[str] = ()
    ) -> float | None:
        # -vn: a camera file's picture is never decoded to measure its sound.
        log = self._run(
            ["-nostats", "-hide_banner", *span, "-i", str(path), "-vn", "-af", graph,
             "-f", "null", "-"],
            cancellation, "measure a part",
        ).stderr  # fmt: skip
        value = _number(_INTEGRATED, log)
        return value if value is not None and value > _GATED else None

    def _run(
        self, args: list[str], cancellation: CancellationToken | None, operation: str
    ) -> ProcessResult:
        try:
            result = self._runner.run(
                ProcessSpec(self._exe, args, timeout_seconds=3600.0), cancellation=cancellation
            )
        except OperationCancelledError:
            raise
        except ExternalSystemError as exc:
            raise BackendFailed(operation, exc.user_message) from exc
        if result.exit_code != 0:
            raise BackendFailed(
                operation,
                result.stderr.strip().splitlines()[-1]
                if result.stderr.strip()
                else f"exit {result.exit_code}",
            )
        return result
