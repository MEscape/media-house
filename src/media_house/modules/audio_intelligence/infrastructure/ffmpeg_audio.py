"""FFmpeg/ffprobe adapter: any audio or video file -> standard PCM WAV for speech models.

The source file is only read. Nothing is trimmed or padded; the one timing fact that matters
(an audio stream that starts after the container's time origin) is measured and returned as
``audio_offset`` so word times can be mapped back onto the ORIGINAL media timeline.
"""

import json
import math
import wave
from pathlib import Path
from typing import Any

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.modules.audio_intelligence.application.ports import PreparedAudio
from media_house.modules.audio_intelligence.domain.errors import NoAudioTrack, UnreadableAudio
from media_house.modules.audio_intelligence.domain.values import PreparationConfig
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, ProcessFailedError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Mild, single-pass EBU R128 loudness normalisation (only when requested).
_LOUDNORM = "loudnorm=I=-16:LRA=11:TP=-1.5"
_PROBE_TIMEOUT = 60.0
_MIN_TIMEOUT = 600.0


def _seconds(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class FfmpegAudioPreparer:
    """Structurally implements ``AudioPreparer`` through the ``ProcessRunner`` port.

    A missing FFmpeg/ffprobe surfaces as ``ToolNotFoundError`` ("... was not found"), raised by
    the runner before any obscure subprocess failure can happen.
    """

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

    def prepare(
        self,
        source: Path,
        destination: Path,
        config: PreparationConfig,
        cancellation: CancellationToken,
    ) -> PreparedAudio:
        duration, offset = self._probe(source, config.audio_stream, cancellation)
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:a:{config.audio_stream}",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            str(config.channels),
            "-ar",
            str(config.sample_rate),
        ]
        if config.loudness_normalization:
            args += ["-af", _LOUDNORM]
        args += ["-c:a", "pcm_s16le", "-f", "wav", str(destination)]
        _log.info(
            "Extracting audio", audio_stream=config.audio_stream, sample_rate=config.sample_rate
        )
        try:
            self._runner.run(
                ProcessSpec(
                    self._ffmpeg,
                    args,
                    timeout_seconds=_MIN_TIMEOUT + duration,
                    check=True,
                ),
                cancellation=cancellation,
            )
        except ProcessFailedError as exc:
            raise UnreadableAudio(f"audio could not be decoded ({exc})") from exc
        return self._verify(destination, duration, offset)

    # ------------------------------------------------------------------------------------------
    def _probe(
        self,
        source: Path,
        audio_stream: int,
        cancellation: CancellationToken,
    ) -> tuple[float, float]:
        """``(source duration, offset of the chosen audio stream)``."""
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
                timeout_seconds=_PROBE_TIMEOUT,
            ),
            cancellation=cancellation,
        )
        if not result.succeeded:
            tail = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
            raise UnreadableAudio(tail)
        try:
            info: dict[str, Any] = json.loads(result.stdout)
        except ValueError as exc:
            raise UnreadableAudio("ffprobe returned unreadable output") from exc

        streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
        if audio_stream >= len(streams):
            raise NoAudioTrack
        stream = streams[audio_stream]
        container = info.get("format", {})
        duration = _seconds(container.get("duration")) or _seconds(stream.get("duration"))
        if duration is None or duration <= 0:
            raise UnreadableAudio("the media has no usable duration")
        origin = _seconds(container.get("start_time")) or 0.0
        start = _seconds(stream.get("start_time")) or 0.0
        return duration, max(0.0, start - origin)

    @staticmethod
    def _verify(
        destination: Path,
        source_duration: float,
        offset: float,
    ) -> PreparedAudio:
        try:
            with wave.open(str(destination), "rb") as wav:
                frames, rate, channels = wav.getnframes(), wav.getframerate(), wav.getnchannels()
        except (OSError, EOFError, wave.Error) as exc:
            raise ExternalSystemError(
                f"FFmpeg did not produce a readable WAV file: {exc}",
                user_message="Audio extraction failed.",
            ) from exc
        if frames == 0:
            raise UnreadableAudio("the audio stream is empty")
        return PreparedAudio(
            source_duration=source_duration,
            audio_offset=offset,
            sample_rate=rate,
            channels=channels,
            duration=frames / rate,
        )
