"""What the engine needs from the outside world (implemented in ``infrastructure``)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.errors import RenderFailed
from media_house.modules.motion_graphics_engine.domain.model import (
    CompiledAnimation,
    Composition,
    EasingFacts,
    FaceMetrics,
)
from media_house.modules.motion_graphics_engine.domain.profile import RenderSettings
from media_house.modules.motion_graphics_engine.domain.qc import RenderFacts
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import Result


@dataclass(frozen=True, slots=True, kw_only=True)
class FontAsset:
    key: str
    family: str
    weight: int
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageAsset:
    asset_id: str
    path: Path
    checksum: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """Everything one render needs: the laid-out composition, its keyframes and local files."""

    composition: Composition
    animation: CompiledAnimation
    fps: Rational
    frames: int
    easings: tuple[EasingFacts, ...]
    fonts: tuple[FontAsset, ...]
    images: tuple[ImageAsset, ...]
    #: Render only this frame as a PNG still (preview); ``None`` = the whole MOV.
    still_frame: int | None = None
    #: Output scale (preview renders smaller); 1.0 = the style's output size.
    scale: float = 1.0
    #: Several graphics in one file (a caption batch); empty = ``composition`` alone.
    parts: tuple["ScenePart", ...] = ()
    #: Still fast path: only these local frames are drawn (ascending, from 0), with
    #: ``animation`` resampled onto them (``engine.resample``); every other frame repeats the
    #: last drawn one before it. Empty = every one of ``frames`` is drawn.
    samples: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ScenePart:
    """One graphic of a batch scene, shown from ``start_frame`` for ``frames`` frames."""

    composition: Composition
    animation: CompiledAnimation
    start_frame: int
    frames: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ComposedScene:
    """The backend's document (HTML for HyperFrames) and the files it references."""

    document: str
    files: tuple[tuple[str, Path], ...]
    #: sha256 of the document and every referenced file's checksum: the render identity.
    digest: str
    width: int
    height: int
    frames: int
    still: bool
    #: The drawn frames of a still fast path (``Scene.samples``); empty = all ``frames``.
    samples: tuple[int, ...] = ()


class GraphicsBackend(Protocol):
    def identity(self) -> tuple[str, str]:
        """(renderer name, pinned version)."""
        ...

    def unavailable(self) -> str | None:
        """Why rendering cannot run here (with the exact setup command), or ``None``."""
        ...

    def compose(self, scene: Scene) -> ComposedScene: ...

    def render(
        self,
        composed: ComposedScene,
        *,
        output: Path,
        fps: Rational,
        settings: RenderSettings,
        cancellation: CancellationToken,
    ) -> Result[Path, RenderFailed]:
        """Render to ``output`` (MOV, or PNG for a still). Never downloads anything."""
        ...


class RenderProbe(Protocol):
    def probe(
        self, path: Path, sample_frames: tuple[int, ...], cancellation: CancellationToken
    ) -> Result[RenderFacts, RenderFailed]:
        """ffprobe facts and decoded alpha statistics of ``sample_frames``."""
        ...


class FontMetricsReader(Protocol):
    def metrics(self, key: str, path: Path) -> FaceMetrics: ...
