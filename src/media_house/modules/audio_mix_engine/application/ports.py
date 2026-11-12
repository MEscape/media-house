"""The port to an audio processing backend. Owned by the application (it needs the capability);
implemented by the infrastructure backend.

Everything crossing it is a plain record. A backend states what it can do (backend-neutral
operation names); the engine checks the specification against that BEFORE rendering and reports
anything unsupported, never substituting another processor for it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.audio_mix_engine.domain.result import Measurement
from media_house.modules.audio_mix_engine.domain.spec import (
    Automation,
    Duck,
    MixBus,
    MixClip,
    RenderConfig,
)
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    #: Name and version of the backend (recorded for reproducibility).
    identity: str
    operations: frozenset[str]


@dataclass(frozen=True, slots=True)
class StemRequest:
    """One bus: its clips, summed and processed up to (not including) the master."""

    bus: MixBus
    clips: tuple[MixClip, ...]
    ducks: tuple[Duck, ...]
    automation: tuple[Automation, ...]
    #: Dialogue level trims (dB) by clip id.
    trims: Mapping[str, float]
    sources: Mapping[str, Path]
    #: The file's own sample rate by source id (``None``: trim by time).
    source_rates: Mapping[str, int | None]
    sample_rate: int
    timebase_rate: int
    #: Length of the timeline in samples of ``timebase_rate``.
    duration_samples: int
    destination: Path


@dataclass(frozen=True, slots=True)
class MixRequest:
    stems: tuple[Path, ...]
    master: MixBus
    #: Added to the master gain to reach the loudness target.
    trim_db: float
    render: RenderConfig
    duration_samples: int
    destination: Path


class AudioBackend(Protocol):
    def capabilities(self) -> BackendCapabilities: ...

    def render_stem(self, request: StemRequest, cancellation: CancellationToken) -> None: ...

    def render_mix(self, request: MixRequest, cancellation: CancellationToken) -> None: ...

    def attach_audio(
        self, picture: Path, audio: Path, destination: Path, cancellation: CancellationToken
    ) -> None:
        """Write ``picture``'s video untouched (copied) with ``audio`` as its sound."""
        ...

    def measure(self, path: Path, cancellation: CancellationToken) -> Measurement: ...

    def measure_range(
        self, path: Path, start_seconds: float, end_seconds: float, cancellation: CancellationToken
    ) -> float | None:
        """Integrated loudness of one part of a file, ``None`` if it is too short or silent."""
        ...

    def peak_loudness(
        self, path: Path, start_seconds: float, end_seconds: float, cancellation: CancellationToken
    ) -> float | None:
        """The loudest MOMENTARY loudness (400 ms) of one part of a file: how loud a short effect
        is heard, not averaged over the silence around it. ``None``: silent."""
        ...

    def loudness_over(
        self, path: Path, ranges: Sequence[tuple[float, float]], cancellation: CancellationToken
    ) -> float | None:
        """Integrated loudness of the file over several ranges together."""
        ...

    def silences(
        self, path: Path, threshold_db: float, min_seconds: float, cancellation: CancellationToken
    ) -> tuple[tuple[float, float], ...]: ...

    def null_test(self, a: Path, b: Path, cancellation: CancellationToken) -> float | None:
        """Peak (dBFS) of ``a`` minus ``b``: very low means identical; ``None``: not measurable."""
        ...
