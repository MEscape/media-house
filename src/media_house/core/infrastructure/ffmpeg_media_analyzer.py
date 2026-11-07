"""The default :class:`MediaAnalyzer`: FFmpeg's detection filters through ``ProcessRunner`` plus a
little numpy for the statistics of single pictures.

FFmpeg does the finding (``blackdetect``, ``freezedetect``, ``silencedetect``, ``ebur128``, a full
decode into nowhere, one picture per time); this file only reads its report. A report it cannot
read is an error (``ExternalSystemError``), never a quiet "nothing found".
"""

import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from media_house.core.application.ports import (
    AnalyzerCapabilities,
    DecodeReport,
    FrameComparison,
    FrameSample,
    LoudnessReading,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
    TimeSpan,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError

#: Bumped when a command line or parser changes in a way that changes a result.
ADAPTER_REVISION = 1
_TIMEOUT_SECONDS = 6 * 3600.0
#: ebur128 reports this for programmes that never pass its gate.
_GATED = -70.0

_NEEDS: dict[str, tuple[str, ...]] = {
    "black_spans": ("blackdetect",),
    "frozen_spans": ("freezedetect",),
    "silent_spans": ("silencedetect",),
    "loudness": ("ebur128", "astats"),
}
_FILTER_LINE = re.compile(r"^\s*[A-Z.]{1,3}\s+(\w+)\s+\S+->\S+")
_VERSION = re.compile(r"ffmpeg version (\S+)")
_BLACK = re.compile(r"black_start:(\S+)\s+black_end:(\S+)")
_FREEZE = re.compile(r"freeze_(start|duration|end):\s*(\S+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_INTEGRATED = re.compile(r"^\s*I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS", re.MULTILINE)
_RANGE = re.compile(r"^\s*LRA:\s+(-?\d+(?:\.\d+)?)\s+LU", re.MULTILINE)
_TRUE_PEAK = re.compile(r"True peak:\s+Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS", re.MULTILINE)
_SAMPLE_PEAK = re.compile(r"Peak level dB:\s+(-?inf|-?\d+(?:\.\d+)?)")
_SAMPLES = re.compile(r"Number of samples:\s+(\d+)")
_AUDIO = re.compile(r"Audio: (\w+)[^,\n]*, (\d+) Hz, ([\w.()]+)")
_VIDEO = re.compile(r"Stream #\d+:\d+[^\n]*: Video:")
_CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "5.1": 6, "7.1": 8}
_PROGRESS_FRAME = re.compile(r"^frame=(\d+)", re.MULTILINE)
_PROGRESS_TIME = re.compile(r"^out_time_us=(\d+)", re.MULTILINE)
_MAX_ERROR_LINES = 20
#: Luma (0-255) at or below which a pixel counts as black / at or above which as white.
_FLOOR = 4.0
_CEILING = 251.0
_BLACK_PIXEL = 16.0
_BORDER_ROW = 12.0


def _exact(text: str) -> Fraction | None:
    try:
        return Fraction(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _decimal(value: Fraction) -> str:
    return f"{Decimal(value.numerator) / Decimal(value.denominator):.6f}"


def _number(pattern: re.Pattern[str], text: str) -> float | None:
    matches = pattern.findall(text)
    if not matches or matches[-1] in {"-inf", "inf"}:
        return None
    return float(matches[-1])


class FfmpegMediaAnalyzer:
    """Structurally implements ``MediaAnalyzer``."""

    def __init__(self, runner: ProcessRunner, executable: str = "ffmpeg") -> None:
        self._runner = runner
        self._exe = executable
        self._capabilities: AnalyzerCapabilities | None = None

    # --- capabilities -----------------------------------------------------------------------
    def capabilities(self) -> AnalyzerCapabilities:
        if self._capabilities is None:
            version = self._run(["-hide_banner", "-version"], None, "read the version")
            filters = self._run(["-hide_banner", "-filters"], None, "list the filters")
            have = {
                m.group(1)
                for line in filters.stdout.splitlines()
                if (m := _FILTER_LINE.match(line))
            }
            found = _VERSION.search(version.stdout)
            identity = f"ffmpeg {found.group(1) if found else 'unknown'} r{ADAPTER_REVISION}"
            operations = {op for op, needs in _NEEDS.items() if set(needs) <= have}
            operations |= {"decode", "sample_frames", "compare_frames"}
            self._capabilities = AnalyzerCapabilities(identity, frozenset(operations))
        return self._capabilities

    # --- detection --------------------------------------------------------------------------
    def black_spans(
        self,
        path: Path,
        *,
        min_seconds: float,
        picture_threshold: float,
        pixel_threshold: float,
        cancellation: CancellationToken,
    ) -> tuple[TimeSpan, ...]:
        if not self._streams(path, cancellation).has_video:
            return ()
        log = self._run(
            ["-nostdin", "-nostats", "-hide_banner", "-i", str(path), "-vf",
             f"blackdetect=d={min_seconds:g}:pic_th={picture_threshold:g}:pix_th={pixel_threshold:g}",
             "-an", "-f", "null", "-"],
            cancellation, "look for black frames",
        ).stderr  # fmt: skip
        spans: list[TimeSpan] = []
        for start_text, end_text in _BLACK.findall(log):
            start, end = _exact(start_text), _exact(end_text)
            if start is not None and end is not None:
                spans.append(TimeSpan(start, end))
        return tuple(spans)

    def frozen_spans(
        self,
        path: Path,
        *,
        min_seconds: float,
        noise_db: float,
        cancellation: CancellationToken,
    ) -> tuple[TimeSpan, ...]:
        streams = self._streams(path, cancellation)
        if not streams.has_video:
            return ()
        log = self._run(
            ["-nostdin", "-nostats", "-hide_banner", "-i", str(path), "-vf",
             f"freezedetect=n={noise_db:g}dB:d={min_seconds:g}", "-an", "-f", "null", "-"],
            cancellation, "look for frozen frames",
        ).stderr  # fmt: skip
        spans: list[TimeSpan] = []
        start: Fraction | None = None
        for kind, text in _FREEZE.findall(log):
            value = _exact(text)
            if value is None:
                continue
            if kind == "start":
                start = value
            elif start is not None and kind == "duration":
                spans.append(TimeSpan(start, start + value))
                start = None
            elif start is not None and kind == "end":
                spans.append(TimeSpan(start, value))
                start = None
        return tuple(spans)

    def silent_spans(
        self,
        path: Path,
        *,
        min_seconds: float,
        noise_db: float,
        cancellation: CancellationToken,
    ) -> tuple[TimeSpan, ...]:
        streams = self._streams(path, cancellation)
        if not streams.has_audio:
            return ()
        log = self._run(
            ["-nostdin", "-nostats", "-hide_banner", "-i", str(path), "-vn", "-af",
             f"silencedetect=n={noise_db:g}dB:d={min_seconds:g}", "-f", "null", "-"],
            cancellation, "look for silence",
        ).stderr  # fmt: skip
        starts = [_exact(x) for x in _SILENCE_START.findall(log)]
        ends = [_exact(x) for x in _SILENCE_END.findall(log)]
        spans: list[TimeSpan] = []
        for index, start in enumerate(starts):
            if start is None:
                continue
            end = ends[index] if index < len(ends) else None  # silent until the end
            spans.append(TimeSpan(max(Fraction(0), start), end))
        return tuple(spans)

    # --- loudness and decoding --------------------------------------------------------------
    def loudness(
        self, path: Path, *, span: TimeSpan | None, cancellation: CancellationToken
    ) -> LoudnessReading:
        if not self._streams(path, cancellation).has_audio:
            return LoudnessReading(None, None, None, None, None, None, None, "", has_audio=False)
        seek: list[str] = []
        if span is not None:
            seek = ["-ss", _decimal(span.start)]
            if span.end is not None:
                seek += ["-t", _decimal(span.end - span.start)]
        log = self._run(
            ["-nostdin", "-nostats", "-hide_banner", *seek, "-i", str(path), "-vn", "-af",
             "ebur128=peak=true:framelog=quiet,astats=measure_perchannel=none",
             "-f", "null", "-"],
            cancellation, "measure the loudness",
        ).stderr  # fmt: skip
        stream = _AUDIO.search(log)
        samples = _SAMPLES.findall(log)
        if stream is None or not samples or "Integrated loudness" not in log:
            raise ExternalSystemError(
                "The loudness report of FFmpeg could not be read",
                user_message="The loudness of the file could not be measured.",
            )
        integrated = _number(_INTEGRATED, log)
        measurable = integrated is not None and integrated > _GATED
        return LoudnessReading(
            integrated_lufs=integrated if measurable else None,
            loudness_range_lu=_number(_RANGE, log) if measurable else None,
            true_peak_dbtp=_number(_TRUE_PEAK, log),
            sample_peak_dbfs=_number(_SAMPLE_PEAK, log),
            duration_samples=int(samples[-1]),
            sample_rate=int(stream.group(2)),
            channels=_CHANNELS.get(stream.group(3)),
            codec=stream.group(1),
        )

    def decode(self, path: Path, *, cancellation: CancellationToken) -> DecodeReport:
        result = self._run(
            ["-nostdin", "-hide_banner", "-v", "error", "-nostats", "-progress", "pipe:1",
             "-i", str(path), "-f", "null", "-"],
            cancellation, "decode the file", check=False,
        )  # fmt: skip
        errors = tuple(line.strip() for line in result.stderr.splitlines() if line.strip())[
            :_MAX_ERROR_LINES
        ]
        frames = _PROGRESS_FRAME.findall(result.stdout)
        micro = _PROGRESS_TIME.findall(result.stdout)
        return DecodeReport(
            complete=result.exit_code == 0 and not errors,
            frames_decoded=int(frames[-1]) if frames else None,
            decoded_until=Fraction(int(micro[-1]), 1_000_000) if micro else None,
            errors=errors or ((f"exit code {result.exit_code}",) if result.exit_code != 0 else ()),
        )

    # --- pictures ---------------------------------------------------------------------------
    def sample_frames(
        self,
        path: Path,
        times: Sequence[Fraction],
        *,
        into: Path | None,
        cancellation: CancellationToken,
    ) -> tuple[FrameSample, ...]:
        if not times:
            return ()
        if into is not None:
            into.mkdir(parents=True, exist_ok=True)
            return self._sample(path, times, into, True, cancellation)
        with tempfile.TemporaryDirectory(prefix="mh-frames-") as scratch:
            return self._sample(path, times, Path(scratch), False, cancellation)

    def _sample(
        self,
        path: Path,
        times: Sequence[Fraction],
        folder: Path,
        keep: bool,
        cancellation: CancellationToken,
    ) -> tuple[FrameSample, ...]:
        samples: list[FrameSample] = []
        for index, moment in enumerate(times):
            cancellation.raise_if_cancelled()
            target = folder / f"{path.stem}-{index:04d}-{_decimal(moment).replace('.', '_')}.png"
            result = self._run(
                ["-nostdin", "-hide_banner", "-loglevel", "error", "-ss", _decimal(moment),
                 "-i", str(path), "-frames:v", "1", "-an", "-y", str(target)],
                cancellation, "decode a picture", check=False,
            )  # fmt: skip
            if result.exit_code != 0 or not target.is_file() or target.stat().st_size == 0:
                continue
            samples.append(_measure_picture(target, moment, target if keep else None))
        return tuple(samples)

    def compare_frames(self, a: Path, b: Path) -> FrameComparison:
        first, second = _reduced_luma(a), _reduced_luma(b)
        difference = float(np.mean(np.abs(first - second)))
        flat_a, flat_b = first.ravel(), second.ravel()
        if float(np.std(flat_a)) == 0.0 or float(np.std(flat_b)) == 0.0:
            correlation = 1.0 if np.array_equal(flat_a, flat_b) else 0.0
        else:
            correlation = float(np.corrcoef(flat_a, flat_b)[0, 1])
        return FrameComparison(
            similarity=max(0.0, 1.0 - difference / 255.0),
            correlation=correlation,
            mean_abs_difference=difference,
        )

    # --- plumbing ---------------------------------------------------------------------------
    def _streams(self, path: Path, cancellation: CancellationToken) -> "_Streams":
        # ``ffmpeg -i file`` with no output prints the stream map and exits non-zero by design.
        result = self._run(
            ["-nostdin", "-hide_banner", "-i", str(path)], cancellation, "read the streams",
            check=False,
        )  # fmt: skip
        text = result.stderr
        return _Streams(
            has_video=bool(_VIDEO.search(text)), has_audio=_AUDIO.search(text) is not None
        )

    def _run(
        self,
        args: list[str],
        cancellation: CancellationToken | None,
        operation: str,
        *,
        check: bool = True,
    ) -> ProcessResult:
        try:
            result = self._runner.run(
                ProcessSpec(self._exe, args, timeout_seconds=_TIMEOUT_SECONDS),
                cancellation=cancellation,
            )
        except OperationCancelledError:
            raise
        except ExternalSystemError as exc:
            raise ExternalSystemError(
                f"FFmpeg could not {operation}: {exc}",
                user_message=f"The media tool could not {operation}.",
            ) from exc
        if check and result.exit_code != 0:
            tail = result.stderr.strip().splitlines()[-1:] or [f"exit code {result.exit_code}"]
            raise ExternalSystemError(
                f"FFmpeg failed to {operation}: {tail[0]}",
                user_message=f"The media tool could not {operation}.",
                details={"exit_code": result.exit_code},
            )
        return result


@dataclass(frozen=True, slots=True)
class _Streams:
    has_video: bool
    has_audio: bool


# --- pictures: measuring one decoded frame --------------------------------------------------------
type _Array = NDArray[np.float32]


def _load(path: Path) -> _Array:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32)


def _luma(rgb: _Array) -> _Array:
    return np.asarray(
        0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2], dtype=np.float32
    )


def _reduced_luma(path: Path) -> _Array:
    with Image.open(path) as image:
        small = image.convert("L").resize((160, 90), Image.Resampling.BILINEAR)
        return np.asarray(small, dtype=np.float32)


def _border(luma: _Array) -> tuple[float, float, float, float]:
    rows = luma.mean(axis=1)
    cols = luma.mean(axis=0)

    def run(values: _Array) -> int:
        count = 0
        for value in values:
            if value > _BORDER_ROW:
                break
            count += 1
        return count

    height, width = luma.shape
    if run(rows) == height:  # a wholly black picture has no "border"
        return (0.0, 0.0, 0.0, 0.0)
    return (
        run(rows) / height,
        run(rows[::-1]) / height,
        run(cols) / width,
        run(cols[::-1]) / width,
    )


def _measure_picture(path: Path, requested: Fraction, kept: Path | None) -> FrameSample:
    rgb = _load(path)
    luma = _luma(rgb)
    highest = rgb.max(axis=2)
    lowest = rgb.min(axis=2)
    saturation = np.where(highest > 0, (highest - lowest) / np.maximum(highest, 1.0), 0.0)
    padded = np.pad(luma, 1, mode="edge")
    laplacian = (
        padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:] - 4.0 * luma
    )
    low, high = np.percentile(luma, [1, 99])
    return FrameSample(
        requested=requested,
        path=kept,
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
        luma_mean=float(luma.mean()),
        luma_low=float(low),
        luma_high=float(high),
        crushed_fraction=float(np.mean(luma <= _FLOOR)),
        clipped_fraction=float(np.mean(luma >= _CEILING)),
        mean_rgb=(float(rgb[..., 0].mean()), float(rgb[..., 1].mean()), float(rgb[..., 2].mean())),
        saturation_mean=float(saturation.mean()),
        sharpness=float(laplacian.var()),
        border_black=_border(luma),
        black_fraction=float(np.mean(luma <= _BLACK_PIXEL)),
    )
