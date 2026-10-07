"""Ports the use case needs from the outside world."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticMeasurements
from media_house.modules.audio_intelligence.domain.analysis.config import AcousticConfig
from media_house.modules.audio_intelligence.domain.raw import RawTranscription
from media_house.modules.audio_intelligence.domain.values import (
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    """Facts about the audio file written by the preparer."""

    #: Length of the SOURCE media (container duration), not of the speech.
    source_duration: float
    #: Seconds from the source timeline origin to the first prepared sample.
    audio_offset: float
    sample_rate: int
    channels: int
    #: Length of the prepared file.
    duration: float


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    """Cheap to obtain (no model loading); part of a result's processing fingerprint."""

    name: str
    version: str


@dataclass(frozen=True, slots=True)
class KnownSourceTiming:
    """Timing of the chosen audio stream already known, so the source need not be probed again."""

    #: Length of the SOURCE media (container duration).
    duration: float
    #: Seconds from the source timeline origin to where the audio stream starts (never negative).
    audio_offset: float


class AudioPreparer(Protocol):
    """Extracts one audio stream to a standard PCM WAV without touching the source."""

    def prepare(
        self,
        source: Path,
        destination: Path,
        config: PreparationConfig,
        cancellation: CancellationToken,
        known: KnownSourceTiming | None = None,
    ) -> PreparedAudio:
        """Blocking. ``known`` replaces probing the source when it is available.

        Raises ``NoAudioTrack`` / ``UnreadableAudio``; a missing FFmpeg raises
        ``ToolNotFoundError``.
        """
        ...


class TranscriptionEngine(Protocol):
    """Speech recognition followed by forced alignment, behind an engine-neutral interface."""

    def identity(self, config: TranscriptionConfig) -> EngineIdentity: ...

    def transcribe(
        self,
        audio: Path,
        config: TranscriptionConfig,
        cancellation: CancellationToken,
        on_stage: Callable[[str], None],
    ) -> RawTranscription:
        """Blocking and heavy. Reuses loaded models between calls.

        Raises ``AlignmentUnavailable`` (policy ``ERROR``), ``ConfigurationError`` (e.g. CUDA
        requested but absent) or ``ExternalSystemError`` (engine/model failure, out of memory).
        """
        ...


class AcousticExtractor(Protocol):
    """Measures the continuous acoustic timeline (pitch, energy, loudness, activity, events)."""

    def identity(self, config: AcousticConfig) -> dict[str, str]:
        """``{analyzer: version}`` of everything that produces measurements. Cheap."""
        ...

    def measure(
        self,
        audio: Path,
        config: AcousticConfig,
        cancellation: CancellationToken,
    ) -> AcousticMeasurements:
        """Blocking. Times are seconds from the start of ``audio`` (prepared-audio time).

        Decodes once and runs independent analyzers concurrently. Raises ``ExternalSystemError``
        when an analyzer fails; absence of speech or pitch is NOT an error (the frames say so).
        """
        ...
