"""What the thumbnail use cases need from the outside world: pixels."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from media_house.modules.thumbnail_design.domain.brief import DeviceCard, Headline, StoryBadge
from media_house.modules.thumbnail_design.domain.layout import Layout
from media_house.modules.thumbnail_design.domain.qc import Box
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True, kw_only=True)
class Colours:
    """Channel palette as ``#RRGGBB`` (from the resolved style, never hard-coded by callers)."""

    blue: str
    blue_light: str
    highlight: str
    white: str
    black: str
    silver_light: str
    silver_mid: str
    silver_dark: str
    strike: str
    price: str = "#0B3F8C"


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """Everything one render needs: local files, the layout, the words. No library ids."""

    layout: Layout
    font: Path
    colours: Colours
    headline: Headline
    reaction: Path
    flip_reaction: bool = False
    #: Asset id -> local cut-out file, for the layout's product slots.
    products: dict[str, Path] = field(default_factory=dict)
    logo: Path | None = None
    device: DeviceCard | None = None
    device_product: Path | None = None
    device_screen: Path | None = None
    badge: StoryBadge | None = None
    plate: Path | None = None


@dataclass(frozen=True, slots=True)
class RenderReport:
    width: int
    height: int
    #: What was drawn where (left, top, right, bottom), for the quality checks.
    boxes: dict[str, Box]
    #: The headline size actually used per line (after shrinking to fit).
    headline_px: tuple[int, ...]


class ThumbnailRenderer(Protocol):
    def identity(self) -> dict[str, str]:
        """Renderer name and version: part of every thumbnail's cache key."""
        ...

    def render(
        self, scene: Scene, destination: Path, cancellation: CancellationToken
    ) -> RenderReport:
        """Write a 1920x1080 PNG. Raises ``ThumbnailFailed`` for an unusable input."""
        ...

    def export(self, source: Path, destination: Path, width: int, max_bytes: int) -> Path:
        """A delivery copy (JPEG at ``width``, under ``max_bytes``); returns the written file."""
        ...
