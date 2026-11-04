"""``SoundMeasurer`` adapter: FFmpeg decodes and measures loudness, numpy/scipy analyse."""

from pathlib import Path

import numpy as np
import scipy

from media_house.modules.music_and_sound_design.domain.analysis import (
    MusicMeasurement,
    SfxMeasurement,
)
from media_house.modules.music_and_sound_design.infrastructure import music_signal, sfx_signal
from media_house.modules.music_and_sound_design.infrastructure.ffmpeg_audio import FfmpegAudio
from media_house.shared.concurrency import CancellationToken
from media_house.shared.filesystem import AppPaths


class FfmpegSoundMeasurer:
    def __init__(self, ffmpeg: FfmpegAudio, paths: AppPaths) -> None:
        self._ffmpeg = ffmpeg
        self._paths = paths

    @property
    def identity(self) -> str:
        return f"ffmpeg {self._ffmpeg.version}; numpy {np.__version__}; scipy {scipy.__version__}"

    def _engines(self) -> tuple[tuple[str, str], ...]:
        return (
            ("ffmpeg", self._ffmpeg.version),
            ("numpy", np.__version__),
            ("scipy", scipy.__version__),
        )

    def measure_music(self, path: Path, cancellation: CancellationToken) -> MusicMeasurement:
        with self._paths.temporary_directory(prefix="music-measure-") as tmp:
            samples = self._ffmpeg.decode_mono(
                path, tmp / "music.f32", music_signal.SAMPLE_RATE, cancellation
            )
        loudness = self._ffmpeg.loudness(path, cancellation)
        return music_signal.measure_music_signal(
            samples, loudness=loudness, engines=self._engines()
        )

    def measure_sfx(self, path: Path, cancellation: CancellationToken) -> SfxMeasurement:
        with self._paths.temporary_directory(prefix="sfx-measure-") as tmp:
            samples = self._ffmpeg.decode_mono(
                path, tmp / "sfx.f32", sfx_signal.SAMPLE_RATE, cancellation
            )
        loudness = self._ffmpeg.loudness(path, cancellation)
        return sfx_signal.measure_sfx_signal(samples, loudness=loudness, engines=self._engines())
