"""The ``FrameSource`` adapter: FFmpeg decodes the video ONCE into proxy-resolution gray frames.

One FFmpeg process reads the file and writes up to two raw files:

* ``dense``: every frame at ``dense_width`` (cuts, flicker, handles need every frame);
* ``samples``: every ``stride``-th frame at ``sample_width`` (motion and picture quality; the
  adaptive plan later picks which of them to analyse).

The ``showinfo`` filter, placed before the split, reports the exact presentation timestamp and
time base of every decoded frame, so frame index and pts are exact for variable frame rate too
and no second pass over the file is needed. The raw files live in a scratch directory owned by
the caller and are read back in bounded chunks. Pictures are decoded in display orientation
(FFmpeg applies rotation metadata) and scaled to the display aspect ratio, full-range luma.
"""

import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from media_house.core.application.ports import (
    OutputLine,
    ProcessRunner,
    ProcessSpec,
)
from media_house.core.domain import Rational
from media_house.modules.video_intelligence.application.ports import DecodeRequest, FrameTimeline
from media_house.modules.video_intelligence.domain.errors import UnreadableVideo
from media_house.shared.concurrency import CancellationToken
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Revision of how frames are requested, scaled and timestamped. Part of the cache identity.
REVISION = "1"
_TIMEOUT_SECONDS = 7200.0
_VERSION = re.compile(r"version\s+(\S+)")
_FRAME = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+|NOPTS)\b")
_TIMEBASE = re.compile(r"config in time_base:\s*(\d+)/(\d+)")
_DURATION = re.compile(r"\bduration:\s*(-?\d+)\b")


@dataclass(frozen=True, slots=True)
class DecodedFrames:
    """Structurally implements ``application.ports.DecodedVideo``; read by this module's engines."""

    timeline: FrameTimeline
    dense_path: Path | None
    dense_size: tuple[int, int]  # (height, width)
    dense_count: int
    sample_path: Path | None
    sample_size: tuple[int, int]
    sample_count: int
    stride: int
    #: Colour frames (rgb24) at every ``rgb_stride``-th frame, for the model-based analyzers.
    rgb_path: Path | None
    rgb_size: tuple[int, int]
    rgb_count: int
    rgb_stride: int
    warnings: tuple[str, ...]


def _proxy_size(width: int, height: int, target_width: int) -> tuple[int, int]:
    """(height, width) of the proxy for a display size, keeping the display aspect ratio."""
    proxy_width = max(2, min(target_width, width))
    return max(2, round(proxy_width * height / width)), proxy_width


class _Timestamps:
    """Collects the per-frame lines FFmpeg's ``showinfo`` prints while decoding."""

    def __init__(self) -> None:
        self.timebase: Rational | None = None
        self.pts: list[int | None] = []
        self.last_duration = 0

    def feed(self, line: OutputLine) -> None:
        text = line.text
        if "showinfo" not in text:
            return
        if self.timebase is None and (config := _TIMEBASE.search(text)):
            num, den = int(config.group(1)), int(config.group(2))
            if num > 0 and den > 0:
                self.timebase = Rational(num, den)
            return
        if frame := _FRAME.search(text):
            self.pts.append(None if frame.group(2) == "NOPTS" else int(frame.group(2)))
            if duration := _DURATION.search(text):
                self.last_duration = max(0, int(duration.group(1)))


class FfmpegFrameSource:
    """Structurally implements ``application.ports.FrameSource``."""

    def __init__(self, runner: ProcessRunner, *, ffmpeg: str = "ffmpeg") -> None:
        self._runner = runner
        self._ffmpeg = ffmpeg
        self._version: str | None = None

    def identity(self) -> dict[str, str]:
        if self._version is None:
            result = self._runner.run(
                ProcessSpec(self._ffmpeg, ["-version"], timeout_seconds=60.0, check=True)
            )
            first = result.stdout.splitlines()[0] if result.stdout else ""
            match = _VERSION.search(first)
            self._version = match.group(1) if match else "unknown"
        return {"ffmpeg": self._version, "frame_source": REVISION}

    def decode(
        self,
        path: Path,
        request: DecodeRequest,
        work_dir: Path,
        cancellation: CancellationToken,
    ) -> DecodedFrames:
        source, settings = request.source, request.settings
        dense_size = _proxy_size(source.width, source.height, settings.dense_width)
        sample_size = _proxy_size(source.width, source.height, settings.sample_width)
        dense_path = work_dir / "dense.gray" if request.dense else None
        sample_path = work_dir / "samples.gray" if request.samples else None
        rgb_size = _proxy_size(source.width, source.height, settings.rgb_width)
        rgb_path = work_dir / "frames.rgb" if request.rgb else None

        convert = "flags=area:in_range=auto:out_range=pc"
        branches: list[tuple[str, str]] = []  # (label, filter chain ending in that label)
        if dense_path is not None:
            branches.append(
                ("dense", f"scale={dense_size[1]}:{dense_size[0]}:{convert},format=gray[dense]")
            )
        if sample_path is not None:
            branches.append(
                (
                    "samples",
                    f"select='not(mod(n,{request.stride}))',"
                    f"scale={sample_size[1]}:{sample_size[0]}:{convert},format=gray[samples]",
                )
            )
        if rgb_path is not None:
            branches.append(
                (
                    "rgb",
                    f"select='not(mod(n,{request.rgb_stride}))',"
                    f"scale={rgb_size[1]}:{rgb_size[0]}:{convert},format=rgb24[rgb]",
                )
            )
        if len(branches) == 1:
            filter_graph = f"[0:v:0]showinfo,{branches[0][1]}"
        else:
            tags = [f"[b{i}]" for i in range(len(branches))]
            parts = [f"[0:v:0]showinfo,split={len(branches)}{''.join(tags)}"]
            parts += [f"{tag}{chain}" for tag, (_, chain) in zip(tags, branches, strict=True)]
            filter_graph = ";".join(parts)

        command = ["-hide_banner", "-nostdin", "-y", "-loglevel", "info", "-i", str(path)]
        command += ["-an", "-sn", "-dn", "-filter_complex", filter_graph]
        outputs = {"dense": dense_path, "samples": sample_path, "rgb": rgb_path}
        for label, _ in branches:
            command += ["-map", f"[{label}]", "-fps_mode", "passthrough", "-f", "rawvideo"]
            command += [str(outputs[label])]

        stamps = _Timestamps()
        result = self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                command,
                timeout_seconds=_TIMEOUT_SECONDS,
                max_captured_lines=40,
            ),
            cancellation=cancellation,
            on_output=stamps.feed,
        )

        warnings: list[str] = []
        timeline = _timeline(stamps)
        dense_count = _frames_in(dense_path, dense_size)
        sample_count = _frames_in(sample_path, sample_size)
        rgb_count = _frames_in(rgb_path, rgb_size, channels=3)
        decoded = len(timeline.pts)
        if decoded == 0 or (request.dense and dense_count == 0):
            reason = (result.stderr.strip().splitlines() or ["no frame could be decoded"])[-1]
            raise UnreadableVideo(reason)
        if not result.succeeded:
            warnings.append(f"the decoder reported errors (exit code {result.exit_code})")
        if request.dense and dense_count != decoded:
            # a truncated write: keep only the frames that exist on both sides
            warnings.append(f"decoded {decoded} frame timestamps but {dense_count} pictures")
            decoded = min(decoded, dense_count)
            timeline = _cut(timeline, decoded)
        expected_samples = -(-decoded // request.stride)
        if request.samples and sample_count < expected_samples:
            warnings.append(f"decoded {sample_count} of {expected_samples} sample pictures")
        expected_rgb = -(-decoded // request.rgb_stride)
        if request.rgb and rgb_count < expected_rgb:
            warnings.append(f"decoded {rgb_count} of {expected_rgb} colour pictures")
        _log.info(
            "Video decoded once",
            frames=decoded,
            dense=dense_count,
            samples=sample_count,
            seconds=round(result.duration_seconds, 2),
        )
        return DecodedFrames(
            timeline=timeline,
            dense_path=dense_path,
            dense_size=dense_size,
            dense_count=dense_count if request.dense else 0,
            sample_path=sample_path,
            sample_size=sample_size,
            sample_count=sample_count,
            stride=request.stride,
            rgb_path=rgb_path,
            rgb_size=rgb_size,
            rgb_count=rgb_count,
            rgb_stride=request.rgb_stride,
            warnings=tuple(warnings),
        )


def _frames_in(path: Path | None, size: tuple[int, int], channels: int = 1) -> int:
    if path is None or not path.exists():
        return 0
    return path.stat().st_size // (size[0] * size[1] * channels)


def _timeline(stamps: _Timestamps) -> FrameTimeline:
    """Complete the decoder's timestamps: interpolate missing ones, never decreasing."""
    timebase = stamps.timebase or Rational(1, 1_000_000)
    raw = stamps.pts
    known = [p for p in raw if p is not None]
    step = 0
    if len(known) >= 2:
        gaps = sorted(b - a for a, b in pairwise(known) if b > a)
        step = gaps[len(gaps) // 2] if gaps else 0
    filled: list[int] = []
    estimated = 0
    for value in raw:
        if value is None:
            estimated += 1
            value = (filled[-1] + step) if filled else 0
        filled.append(max(value, filled[-1]) if filled else value)
    duration = stamps.last_duration or step
    end = (filled[-1] + duration) if filled else 0
    return FrameTimeline(timebase, tuple(filled), end, estimated)


def _cut(timeline: FrameTimeline, count: int) -> FrameTimeline:
    pts = timeline.pts[:count]
    end = timeline.end_pts if count == len(timeline.pts) else (pts[-1] if pts else 0)
    return FrameTimeline(
        timeline.timebase, pts, max(end, pts[-1] if pts else 0), timeline.estimated
    )
