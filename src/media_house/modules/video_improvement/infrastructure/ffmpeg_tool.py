"""The one place that knows how to call FFmpeg/ffprobe (through the ``ProcessRunner`` port).

Command construction, timeouts, version identity and the MINIMAL probe live here, so the
sampler, the renderer and the verification share them. The probe reads only what the processing
needs; when Media Inspection has already established these facts they are taken from it and this
probe does not run on the source (see ``application/prior_inspection.py``).
"""

import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from media_house.core.application.ports import (
    OutputLine,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)
from media_house.modules.video_improvement.domain.color import color_from_tags
from media_house.modules.video_improvement.domain.errors import UnreadableSource
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.domain.values import FactsSource, FrameRate
from media_house.shared.concurrency import CancellationToken

_TIMEOUT_SECONDS = 7200.0
_PROBE_TIMEOUT_SECONDS = 600.0
_VERSION_PATTERN = re.compile(r"version\s+(\S+)")
_BIT_SUFFIX = re.compile(r"(\d{2})(?:le|be)$")
_VFR_TOLERANCE = 0.01
_PRE_ROLL_SLACK = 1e-6


_MATRICES = {
    "bt709": "bt709",
    "bt470bg": "bt601",
    "smpte170m": "smpte170m",
    "bt2020nc": "bt2020",
    "bt2020c": "bt2020",
}


def matrix_name(facts: VideoFacts) -> str:
    """The YUV->RGB matrix FFmpeg's ``scale`` should use: the declared one, else the convention
    for the size (HD is Rec.709, SD is BT.601)."""
    named = _MATRICES.get((facts.color_matrix or "").lower())
    if named is not None:
        return named
    return "bt709" if facts.is_hd else "smpte170m"


def range_name(facts: VideoFacts) -> str:
    return "pc" if facts.color_range == "pc" else "tv"


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stated(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in {"", "n/a", "unknown", "unspecified"} else text


class FfmpegTool:
    """Runs FFmpeg and ffprobe; a missing tool surfaces as ``ToolNotFoundError`` from the runner."""

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
    ) -> None:
        self._runner = runner
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._version: str | None = None

    @property
    def version(self) -> str:
        """FFmpeg's own version (its filters and encoders define the output)."""
        if self._version is None:
            result = self._runner.run(
                ProcessSpec(
                    self._ffmpeg, ["-version"], timeout_seconds=_PROBE_TIMEOUT_SECONDS, check=True
                )
            )
            match = _VERSION_PATTERN.search(result.stdout.splitlines()[0] if result.stdout else "")
            self._version = match.group(1) if match else "unknown"
        return self._version

    def run(
        self,
        args: list[str],
        cancellation: CancellationToken,
        *,
        check: bool = True,
        cwd: Path | None = None,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult:
        return self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                ["-hide_banner", "-nostdin", "-y", *args],
                cwd=cwd,
                timeout_seconds=_TIMEOUT_SECONDS,
                check=check,
                max_captured_lines=200,
            ),
            cancellation=cancellation,
            on_output=on_output,
        )

    def encoders(self, cancellation: CancellationToken) -> frozenset[str]:
        """Names of every encoder this FFmpeg build contains."""
        result = self.run(["-encoders"], cancellation, check=False)
        names = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                names.add(parts[1])
        return frozenset(names)

    def probe_video(self, source: Path, cancellation: CancellationToken) -> VideoFacts:
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                [
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(source),
                ],
                timeout_seconds=_PROBE_TIMEOUT_SECONDS,
            ),
            cancellation=cancellation,
        )
        if not result.succeeded:
            raise UnreadableSource((result.stderr.strip().splitlines() or ["unknown error"])[-1])
        try:
            info: dict[str, Any] = json.loads(result.stdout)
        except ValueError as exc:
            raise UnreadableSource("ffprobe returned unreadable output") from exc
        return facts_from_probe(info, self._visible_frames(source, cancellation))

    def _visible_frames(self, source: Path, cancellation: CancellationToken) -> int | None:
        """Frames a player shows: packets with a timestamp from zero on. A file cut without
        re-encoding carries hidden pre-roll packets that every decoder discards."""
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                [
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "packet=pts_time",
                    "-of",
                    "csv=p=0",
                    str(source),
                ],
                timeout_seconds=_PROBE_TIMEOUT_SECONDS,
                max_captured_lines=5_000_000,
            ),
            cancellation=cancellation,
        )
        if not result.succeeded:
            return None
        stamps = [_number(line.strip().rstrip(",")) for line in result.stdout.splitlines()]
        visible = [t for t in stamps if t is not None and t >= -_PRE_ROLL_SLACK]
        return len(visible) or None


def facts_from_probe(info: dict[str, Any], frame_count: int | None = None) -> VideoFacts:
    streams: list[dict[str, Any]] = info.get("streams", [])
    container: dict[str, Any] = info.get("format", {})
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    if not videos:
        raise UnreadableSource("the media has no video stream")
    video = videos[0]
    width, height = video.get("width"), video.get("height")
    nominal = FrameRate.parse(video.get("r_frame_rate")) or FrameRate.parse(
        video.get("avg_frame_rate")
    )
    # the video stream's own length: the container's can include longer audio or data tracks
    duration = _number(video.get("duration")) or _number(container.get("duration"))
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise UnreadableSource("the video stream has no usable size")
    if nominal is None or not duration or duration <= 0:
        raise UnreadableSource("the video stream has no usable frame rate or duration")
    average = FrameRate.parse(video.get("avg_frame_rate"))
    variable = (
        average is not None and abs(average.value - nominal.value) / nominal.value > _VFR_TOLERANCE
    )

    tags: dict[str, str] = {}
    for source_tags in (container.get("tags"), video.get("tags")):
        if isinstance(source_tags, dict):
            tags.update({str(k).lower(): str(v) for k, v in source_tags.items()})

    pix_fmt = _stated(video.get("pix_fmt"))
    depth = _number(video.get("bits_per_raw_sample"))
    if depth is None and pix_fmt is not None:
        suffix = _BIT_SUFFIX.search(pix_fmt)
        depth = float(suffix.group(1)) if suffix else 8.0
    return VideoFacts(
        width=width,
        height=height,
        frame_rate=nominal,
        duration=duration,
        frame_count=frame_count,
        pixel_format=pix_fmt,
        bit_depth=int(depth) if depth else None,
        color=color_from_tags(
            _stated(video.get("color_transfer")), _stated(video.get("color_primaries"))
        ),
        color_range=_stated(video.get("color_range")),
        color_matrix=_stated(video.get("color_space")),
        interlaced=_stated(video.get("field_order")) in {"tt", "bb", "tb", "bt"},
        rotation=_rotation(video, tags),
        variable_frame_rate=variable,
        audio_stream_count=sum(1 for s in streams if s.get("codec_type") == "audio"),
        timecode=tags.get("timecode"),
        camera_make=tags.get("com.apple.quicktime.make") or tags.get("make"),
        camera_model=tags.get("com.apple.quicktime.model") or tags.get("model"),
        tags=tags,
        source=FactsSource.PROBE,
    )


def _rotation(stream: dict[str, Any], tags: dict[str, str]) -> int:
    """Clockwise degrees a player turns the picture (ffprobe reports it counter-clockwise)."""
    for side in stream.get("side_data_list") or ():
        if isinstance(side, dict) and (degrees := _number(side.get("rotation"))) is not None:
            return round(-degrees / 90) * 90 % 360
    legacy = _number(tags.get("rotate"))
    return round(legacy / 90) * 90 % 360 if legacy is not None else 0
