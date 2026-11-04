"""Ports the use cases need from the outside world (pixels, files, FFmpeg, a generator)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.visual_asset_generation.domain.generation import GenerationRequest
from media_house.modules.visual_asset_generation.domain.profile import Enhancement
from media_house.modules.visual_asset_generation.domain.qc import ImageFacts
from media_house.modules.visual_asset_generation.domain.requests import ExportSettings
from media_house.modules.visual_asset_generation.domain.values import (
    AdjustmentSettings,
    Canvas,
    MaskStats,
)
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class RenderReport:
    """Facts about a finished render, stored as asset metadata."""

    mask: MaskStats
    #: Subject size relative to its source pixels (after cropping to the subject).
    scale: float
    #: ``(left, top, right, bottom)`` of the subject on the output canvas, glow excluded.
    subject_box: tuple[int, int, int, int]
    #: Size of the decoded source picture the mask refers to (``None`` from older renderers).
    source_size: tuple[int, int] | None = None


class ImageRenderer(Protocol):
    """Segments, cleans, glows and places the subject; writes a transparent PNG."""

    def render(
        self,
        source: Path,
        destination: Path,
        settings: AdjustmentSettings,
    ) -> RenderReport:
        """Blocking and CPU heavy.

        Raises ``ImageUnreadable`` or ``SegmentationRejected`` (nothing is written then).
        """
        ...


@dataclass(frozen=True, slots=True)
class EnhanceReport:
    #: Output pixels per clean-cutout pixel (1.0 = the subject kept its exact pixels).
    scale: float
    #: Visible bounds of the result on the canvas (effects included).
    visible_box: tuple[int, int, int, int]


class CutoutEnhancer(Protocol):
    """Applies an ``Enhancement`` to a clean transparent cutout; writes an RGBA PNG."""

    def enhance(
        self,
        cutout: Path,
        destination: Path,
        enhancement: Enhancement,
        canvas: Canvas,
    ) -> EnhanceReport:
        """Blocking. Raises ``ImageUnreadable`` (nothing is written then)."""
        ...


class ImageMeasurer(Protocol):
    """Measures what a written image file really is (QC input). Never raises for a bad file."""

    def measure(self, path: Path) -> ImageFacts: ...


@dataclass(frozen=True, slots=True)
class ExportReport:
    width: int
    height: int
    #: ``False`` when the picture had to be fitted smaller.
    kept_size: bool
    #: The source had transparent pixels (checked, not assumed from the format).
    source_has_transparency: bool


class ImageExporter(Protocol):
    def export(self, source: Path, destination: Path, settings: ExportSettings) -> ExportReport:
        """Blocking. Raises ``ImageUnreadable`` or ``ExportRefused`` (nothing written then)."""
        ...

    def transparency(self, source: Path) -> bool:
        """Whether any pixel of ``source`` is not fully opaque. Raises ``ImageUnreadable``."""
        ...


@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    width: int
    height: int
    pixel_format: str
    #: ``(name, version)`` of the tool that decoded it.
    engine: tuple[str, str]


class FrameExtractor(Protocol):
    """Writes one decoded picture of a video as a high-precision PNG (RGB48 when possible)."""

    def extract(
        self,
        video: Path,
        seek_seconds: str,
        destination: Path,
        cancellation: CancellationToken,
    ) -> ExtractedFrame:
        """``seek_seconds`` is an exact decimal string.

        Raises ``FrameGrabFailed`` (this picture) or ``ExternalSystemError`` (no tool, timeout).
        """
        ...


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    model: str
    model_version: str
    #: The seed actually used (reproducibility), ``None`` if the generator has none.
    seed: int | None


class ImageGenerator(Protocol):
    """Generates an illustrative picture. No implementation exists on this machine.

    ``unavailable`` says why it cannot run now (no model, no provider), else ``None``; nothing is
    ever downloaded. ``generate`` is blocking and writes a PNG.
    """

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...

    def generate(
        self,
        request: GenerationRequest,
        destination: Path,
        cancellation: CancellationToken,
    ) -> GeneratedImage: ...
