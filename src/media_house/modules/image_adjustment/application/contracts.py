"""The Image Adjustment PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``image_adjustment``.

``adjust`` is blocking (decode, segmentation, compositing, file I/O): run it as a job. The
result is an ordinary media-library asset (``DerivedAssetResultDto.asset``); ``created`` is
``False`` when the identical variant already existed and was reused without processing.
"""

from typing import Protocol

from media_house.modules.image_adjustment.application.adjust_image import (
    AdjustError,
    AdjustImageCommand,
)
from media_house.modules.image_adjustment.domain.errors import (
    AdjustmentFailed,
    ImageUnreadable,
    SegmentationRejected,
)
from media_house.modules.image_adjustment.domain.values import (
    AdjustmentSettings,
    BackgroundRemoval,
    Canvas,
    Centering,
    Glow,
    HexColor,
    MaskLimits,
)
from media_house.modules.media_library.application.contracts import DerivedAssetResultDto
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ImageAdjuster(Protocol):
    """Create or reuse a background-removed, optionally glowing, standardised PNG."""

    def execute(
        self,
        command: AdjustImageCommand,
        ctx: JobContext,
    ) -> Result[DerivedAssetResultDto, AdjustError]: ...


__all__ = [
    "AdjustError",
    "AdjustImageCommand",
    "AdjustmentFailed",
    "AdjustmentSettings",
    "BackgroundRemoval",
    "Canvas",
    "Centering",
    "Glow",
    "HexColor",
    "ImageAdjuster",
    "ImageUnreadable",
    "MaskLimits",
    "SegmentationRejected",
]
