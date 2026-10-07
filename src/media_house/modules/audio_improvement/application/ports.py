"""Ports the use cases need from the outside world.

Extension points: a better denoiser, another dereverb algorithm or a new mastering engine is a
new ``StageProcessor`` registered for its stage in ``module.py``. Nothing else changes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.settings import MixSettings
from media_house.modules.audio_improvement.domain.values import Parameter, ProcessingStage
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    """Cheap to obtain (no model loading); part of a result's processing fingerprint."""

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}-{self.version}"


@dataclass(frozen=True, slots=True)
class SourceAudioFacts:
    """What is already known about a source's first audio stream, so it need not be probed again.

    Time is measured from the container's time origin. ``start_offset`` is how late the audio
    starts (never negative).
    """

    sample_rate: int
    channels: int
    duration: float
    start_offset: float


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    """Facts about the working copy written by the transcoder."""

    sample_rate: int
    channels: int
    duration: float
    #: Silence placed before the audio so it starts at the container's time origin.
    leading_pad_seconds: float


class AudioTranscoder(Protocol):
    """Between any media file and the lossless working format (and back to a delivery file).

    The working format is float PCM at the source's own sample rate: no resampling, so time and
    sample alignment survive every stage.
    """

    @property
    def identity(self) -> EngineIdentity: ...

    def decode(
        self,
        source: Path,
        destination: Path,
        cancellation: CancellationToken,
        known: SourceAudioFacts | None = None,
    ) -> DecodedAudio:
        """Blocking. ``known`` replaces probing the source when it is available.

        Raises ``UnreadableSource``; a missing FFmpeg raises ``ToolNotFoundError``.
        """
        ...

    def encode(self, source: Path, destination: Path, cancellation: CancellationToken) -> None:
        """Blocking. Working format -> delivery format (24-bit PCM WAV)."""
        ...


class QualityAnalyzer(Protocol):
    """Measures the quality of an audio file in the working format."""

    @property
    def identity(self) -> EngineIdentity: ...

    def analyze(self, audio: Path, cancellation: CancellationToken) -> QualityMeasurements:
        """Blocking. Raises ``ExternalSystemError`` if a measurement tool fails; absence of
        speech or noise is NOT an error (the affected measurements are ``None``)."""
        ...


class StageProcessor(Protocol):
    """One engine for one processing stage. Same-length output, same sample rate and channels."""

    @property
    def stage(self) -> ProcessingStage: ...

    @property
    def identity(self) -> EngineIdentity: ...

    def process(
        self,
        source: Path,
        destination: Path,
        params: Mapping[str, Parameter],
        cancellation: CancellationToken,
    ) -> None:
        """Blocking. ``params`` are the engine-neutral values the stage's planner produced
        (mastering: ``gain_db``, ``ceiling_dbtp``, ``release_ms``)."""
        ...


class AudioMixer(Protocol):
    """Voice over looped, ducked music in the working format."""

    @property
    def identity(self) -> EngineIdentity: ...

    def mix(
        self,
        voice: Path,
        music: Path,
        destination: Path,
        settings: MixSettings,
        music_gain_db: float,
        cancellation: CancellationToken,
    ) -> None:
        """Blocking. The result is exactly as long as the voice; the music is looped or cut,
        gained by ``music_gain_db``, ducked by the voice and faded out at the end."""
        ...
