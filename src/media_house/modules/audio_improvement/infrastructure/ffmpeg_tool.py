"""The one place that knows how to call FFmpeg/ffprobe (through the ``ProcessRunner`` port).

Every FFmpeg-based adapter of this module (transcoder, stage engines, analyzer, mixer) uses it,
so command construction, timeouts, probing and version identity exist exactly once.
"""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_house.core.application.ports import ProcessResult, ProcessRunner, ProcessSpec
from media_house.modules.audio_improvement.domain.errors import UnreadableSource
from media_house.shared.concurrency import CancellationToken

_TIMEOUT_SECONDS = 1800.0
_PROBE_TIMEOUT_SECONDS = 60.0
_VERSION_PATTERN = re.compile(r"version\s+(\S+)")


@dataclass(frozen=True, slots=True)
class AudioFacts:
    sample_rate: int
    channels: int
    duration: float
    #: Seconds from the container's time origin to the first sample of the audio stream.
    start_offset: float


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
        """FFmpeg's own version (its filters define the processing, so it is part of identity)."""
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
    ) -> ProcessResult:
        return self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                ["-hide_banner", "-nostdin", "-y", *args],
                timeout_seconds=_TIMEOUT_SECONDS,
                check=check,
            ),
            cancellation=cancellation,
        )

    def filter_audio(
        self,
        source: Path,
        destination: Path,
        graph: str,
        cancellation: CancellationToken,
    ) -> None:
        """``source`` through the filter ``graph`` into float PCM WAV, same rate and channels."""
        self.run(
            [
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-af",
                graph,
                "-c:a",
                "pcm_f32le",
                "-f",
                "wav",
                str(destination),
            ],
            cancellation,
        )

    def probe_audio(self, source: Path, cancellation: CancellationToken) -> AudioFacts:
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
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
        streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
        if not streams:
            raise UnreadableSource("the media has no audio stream")
        stream, container = streams[0], info.get("format", {})
        rate, channels = _number(stream.get("sample_rate")), _number(stream.get("channels"))
        duration = _number(container.get("duration")) or _number(stream.get("duration"))
        if not rate or not channels or not duration or duration <= 0:
            raise UnreadableSource("the audio stream has no usable format or duration")
        origin = _number(container.get("start_time")) or 0.0
        start = _number(stream.get("start_time")) or 0.0
        return AudioFacts(int(rate), int(channels), duration, max(0.0, start - origin))
