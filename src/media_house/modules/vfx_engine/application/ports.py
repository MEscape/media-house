"""What the VFX engine needs from the outside world (adapters live in ``infrastructure``)."""

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from media_house.modules.vfx_engine.domain.facts import Box
from media_house.modules.vfx_engine.domain.model import RenderQc, Treatment
from media_house.modules.vfx_engine.domain.profile import Qc
from media_house.modules.vfx_engine.domain.timing import FrameView
from media_house.modules.vfx_engine.domain.values import EffectType
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceMedia:
    """Technical facts of a recording the renderer needs (from the stored inspection; probed
    minimally only when no inspection exists)."""

    width: int
    height: int
    frame_rate: Fraction
    pixel_format: str
    color_space: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_range: str | None = None
    timecode: str | None = None
    from_inspection: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlaySource:
    """Footage of a higher layer composited into the treatment (picture-in-picture)."""

    clip_id: str
    path: Path
    media: SourceMedia
    #: Source time of the overlay clip at the composite effect's first frame.
    source_offset: Fraction


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderJob:
    treatment: Treatment
    source: Path
    media: SourceMedia
    frame_rate: Fraction
    #: The domain's per-output-frame view (source time, punch-in window, mask boxes).
    frames: tuple[FrameView, ...]
    output: Path
    limits: Qc
    #: Exact track boxes per output frame (privacy QC: detail must be gone inside them).
    protected: tuple[tuple[Box, ...], ...] = ()
    overlays: tuple[OverlaySource, ...] = ()
    glow_tint: str | None = None
    #: Deterministic seed for grain / film damage (derived from the treatment).
    seed: int = 0
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedClip:
    path: Path
    qc: RenderQc
    engine: str
    log: tuple[str, ...] = field(default=())


class EffectRenderer(Protocol):
    def identity(self) -> str:
        """Engine and version (part of the render cache key and the provenance)."""
        ...

    def unavailable(self) -> str | None:
        """Why the renderer cannot run here (missing tool), or ``None``."""
        ...

    def supports(self) -> frozenset[EffectType]: ...

    def probe(self, path: Path, cancellation: CancellationToken) -> SourceMedia:
        """Minimal facts when no inspection is stored (graceful degradation)."""
        ...

    def render(self, job: RenderJob, cancellation: CancellationToken) -> RenderedClip:
        """Render the treatment to ``job.output`` and measure it. Raises on tool failure."""
        ...


class SubjectMatter(Protocol):
    """Per-frame subject mattes of video (background blur / replacement, subject isolation)."""

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...


class Inpainter(Protocol):
    """Video inpainting (object removal). Never used to fabricate content without a model."""

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...
