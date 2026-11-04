"""Ports the use cases need; the adapters live in ``infrastructure`` (wired in ``module.py``)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.music_and_sound_design.domain.analysis import (
    MusicMeasurement,
    SfxMeasurement,
)
from media_house.modules.music_and_sound_design.domain.library import SoundManifest
from media_house.modules.music_and_sound_design.domain.model import SoundDesignPlan
from media_house.shared.concurrency import CancellationToken


class SoundMeasurer(Protocol):
    """Measures one sound file (decode + signal analysis). Raises ``UnreadableSound``."""

    @property
    def identity(self) -> str:
        """Engine versions that define the measurement (part of every analysis cache key)."""
        ...

    def measure_music(self, path: Path, cancellation: CancellationToken) -> MusicMeasurement: ...

    def measure_sfx(self, path: Path, cancellation: CancellationToken) -> SfxMeasurement: ...


class ManifestReader(Protocol):
    """Reads and validates the owner's ``manifest.json``. Raises ``InvalidManifest``."""

    def read(self, path: Path) -> SoundManifest: ...


@dataclass(frozen=True, slots=True)
class StemOptions:
    #: Render the planned ducking as gain rides on the music (else the plan keeps it as intent).
    include_ducking: bool = True
    sample_rate: int = 48_000


@dataclass(frozen=True, slots=True)
class StemReport:
    duration_seconds: float
    peak_dbfs: float | None
    sample_rate: int
    channels: int
    codec: str


class StemRenderer(Protocol):
    """Renders the music + effects of a plan into one float WAV. No mastering, no limiting."""

    @property
    def identity(self) -> str: ...

    def render(
        self,
        plan: SoundDesignPlan,
        sources: dict[str, Path],
        destination: Path,
        options: StemOptions,
        cancellation: CancellationToken,
    ) -> StemReport: ...
