"""Any media file <-> the lossless working format (float PCM WAV at the source's own rate).

Timing is the contract: no resampling, no trimming. The one thing that can differ between a
video container and its audio stream, a start offset, is turned into explicit leading silence so
that every timestamp of the source media stays valid on the improved audio (and the amount is
reported, never hidden).
"""

from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.io import wavfile

from media_house.modules.audio_improvement.application.ports import (
    DecodedAudio,
    EngineIdentity,
    SourceAudioFacts,
)
from media_house.modules.audio_improvement.domain.errors import UnreadableSource
from media_house.modules.audio_improvement.infrastructure.ffmpeg_tool import AudioFacts, FfmpegTool
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ProcessFailedError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Revision of how this adapter builds its commands; part of the processing identity.
_REVISION = "1"
_MAX_CHANNELS = 2


def read_wav(path: Path) -> tuple[NDArray[np.float64], int]:
    """``(samples shaped (frames, channels) in -1..1, sample rate)`` of a PCM or float WAV."""
    rate, data = wavfile.read(path)
    samples = np.asarray(data)
    if samples.dtype.kind in "iu":
        scale = float(np.iinfo(samples.dtype).max) + 1.0
        offset = 0.0 if samples.dtype.kind == "i" else scale / 2
        samples = (samples.astype(np.float64) - offset) / (scale if offset == 0.0 else offset)
    out = samples.astype(np.float64)
    return (out[:, np.newaxis] if out.ndim == 1 else out), int(rate)


class FfmpegTranscoder:
    """Structurally implements ``application.ports.AudioTranscoder``."""

    def __init__(self, tool: FfmpegTool) -> None:
        self._tool = tool

    @property
    def identity(self) -> EngineIdentity:
        return EngineIdentity("ffmpeg-transcoder", f"{self._tool.version}+r{_REVISION}")

    def decode(
        self,
        source: Path,
        destination: Path,
        cancellation: CancellationToken,
        known: SourceAudioFacts | None = None,
    ) -> DecodedAudio:
        facts = (
            AudioFacts(known.sample_rate, known.channels, known.duration, known.start_offset)
            if known is not None
            else self._tool.probe_audio(source, cancellation)
        )
        channels = min(facts.channels, _MAX_CHANNELS)
        pad_samples = round(facts.start_offset * facts.sample_rate)
        args = [
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            str(channels),
        ]
        if pad_samples > 0:
            args += ["-af", f"adelay={pad_samples}S:all=1"]
        args += ["-c:a", "pcm_f32le", "-f", "wav", str(destination)]
        try:
            self._tool.run(args, cancellation)
        except ProcessFailedError as exc:
            raise UnreadableSource(f"audio could not be decoded ({exc})") from exc
        try:
            rate, data = wavfile.read(destination)
        except (OSError, ValueError, EOFError) as exc:
            raise UnreadableSource(f"decoding produced an unreadable file ({exc})") from exc
        if len(data) == 0:
            raise UnreadableSource("the audio stream is empty")
        pad = pad_samples / facts.sample_rate if pad_samples > 0 else 0.0
        if pad:
            _log.info(
                "Audio starts after the container origin; padded with silence",
                seconds=round(pad, 4),
            )
        return DecodedAudio(int(rate), channels, len(data) / rate, pad)

    def encode(self, source: Path, destination: Path, cancellation: CancellationToken) -> None:
        self._tool.run(
            [
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-c:a",
                "pcm_s24le",
                "-f",
                "wav",
                str(destination),
            ],
            cancellation,
        )
