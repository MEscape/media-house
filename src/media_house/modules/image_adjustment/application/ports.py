"""Ports the use case needs from the outside world."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.image_adjustment.domain.values import AdjustmentSettings, MaskStats


@dataclass(frozen=True, slots=True)
class RenderReport:
    """Facts about a finished render, stored as asset metadata."""

    mask: MaskStats
    #: Subject size relative to its source pixels (after cropping to the subject).
    scale: float
    #: ``(left, top, right, bottom)`` of the subject on the output canvas, glow excluded.
    subject_box: tuple[int, int, int, int]


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
