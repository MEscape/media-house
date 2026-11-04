"""The Visual Asset Generation PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``visual_asset_generation``.

Every call is blocking (decode, segmentation, compositing, FFmpeg, file I/O): run it as a job.
Results are ordinary media-library assets; ``created`` is ``False`` when the identical output
already existed and was reused without processing.

* ``ImageAdjuster``: one background-removed (optionally glowing) standardised PNG (unchanged API).
* ``VisualAssetPreparer``: a batch of requests (image, or video + exact source time) -> clean
  cutouts, enhanced variants (sticker, silhouette, glow, shadow, outline), optional delivery
  copies, all QC'd, plus a stored ``visual_asset_set`` document naming every asset.
* ``FrameGrabber``: one exact picture of a video as a derived 16-bit PNG.
* ``ImageSelector``: existing library images for a concept (or for every b-roll coverage gap).
* ``ImageGenerationService``: generation, gated by policy; ``not_available`` on this machine.
* ``VisualAssetCatalog``: stored sets, read-only.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import (
    DerivedAssetResultDto,
    ImportResultDto,
    MediaAssetDto,
    MediaError,
)
from media_house.modules.visual_asset_generation.application.adjust_image import (
    AdjustError,
    AdjustImageCommand,
)
from media_house.modules.visual_asset_generation.application.generate_image import (
    GenerateError,
    GenerateImageCommand,
)
from media_house.modules.visual_asset_generation.application.grab_frame import (
    FrameError,
    FrameGrabResult,
    GrabFrameCommand,
)
from media_house.modules.visual_asset_generation.application.ports import (
    GeneratedImage,
    ImageGenerator,
)
from media_house.modules.visual_asset_generation.application.prepare_visual_assets import (
    PreparedVisualAssets,
    PrepareError,
    PrepareVisualAssetsCommand,
)
from media_house.modules.visual_asset_generation.application.select_images import (
    GapVisuals,
    ImageSelection,
    SelectImagesCommand,
)
from media_house.modules.visual_asset_generation.domain.errors import (
    AdjustmentFailed,
    ExportRefused,
    FrameGrabFailed,
    GenerationNotAvailable,
    GenerationRefused,
    ImageUnreadable,
    InvalidVisualAssetConfig,
    InvalidVisualAssetDocument,
    InvalidVisualAssetRequest,
    NoVideoStream,
    SegmentationRejected,
    VisualAssetError,
)
from media_house.modules.visual_asset_generation.domain.frames import FramePosition
from media_house.modules.visual_asset_generation.domain.generation import (
    GENERATED_TAG,
    GenerationKind,
    GenerationRecord,
    GenerationRequest,
)
from media_house.modules.visual_asset_generation.domain.model import (
    DOCUMENT_TYPE,
    ENHANCEMENT_OPERATION,
    EXPORT_OPERATION,
    FRAME_GRAB_OPERATION,
    SET_OPERATION,
    AssetKind,
    EditRef,
    FrameGrab,
    InputHow,
    InputRecord,
    ItemStatus,
    PreparedAsset,
    Provenance,
    ResultState,
    StageReport,
    StageState,
    StyleRef,
    VisualAssetSet,
)
from media_house.modules.visual_asset_generation.domain.profile import (
    DEFAULT_PROFILE,
    PROFILE_VERSION,
    PROFILES,
    Contrast,
    CutoutDefaults,
    Enhancement,
    Outline,
    QcThresholds,
    RimLight,
    SelectionSettings,
    Shadow,
    Silhouette,
    Tilt,
    VariantName,
    Variants,
    VisualAssetConfig,
    VisualAssetProfile,
)
from media_house.modules.visual_asset_generation.domain.qc import QcCategory, QcIssue, Severity
from media_house.modules.visual_asset_generation.domain.requests import (
    AssetRequest,
    ExportSettings,
    ImageFormat,
)
from media_house.modules.visual_asset_generation.domain.selection import Candidate, SourceTier
from media_house.modules.visual_asset_generation.domain.values import (
    ADJUSTMENT_OPERATION,
    ADJUSTMENT_VERSION,
    AdjustmentSettings,
    BackgroundRemoval,
    Canvas,
    Centering,
    Glow,
    HexColor,
    MaskLimits,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ImageAdjuster(Protocol):
    """Create or reuse a background-removed, optionally glowing, standardised PNG."""

    def execute(
        self,
        command: AdjustImageCommand,
        ctx: JobContext,
    ) -> Result[DerivedAssetResultDto, AdjustError]: ...


class VisualAssetPreparer(Protocol):
    """Prepare (or reuse) every requested picture and store the ``visual_asset_set``."""

    def execute(
        self, command: PrepareVisualAssetsCommand, ctx: JobContext
    ) -> Result[PreparedVisualAssets, PrepareError]: ...


class FrameGrabber(Protocol):
    """Take (or reuse) the picture of a video at an exact source time."""

    def execute(
        self, command: GrabFrameCommand, ctx: JobContext
    ) -> Result[FrameGrabResult, FrameError]: ...


class ImageSelector(Protocol):
    """Existing library images first: nothing is generated or analysed."""

    def execute(self, command: SelectImagesCommand) -> ImageSelection: ...

    def for_gaps(
        self,
        asset_id: str,
        *,
        project_group_ids: tuple[str, ...] = (),
        config: VisualAssetConfig | None = None,
    ) -> Result[tuple[GapVisuals, ...], MediaError]: ...


class ImageGenerationService(Protocol):
    """Policy-gated generation; ``GenerationNotAvailable`` when no generator is installed."""

    def execute(
        self, command: GenerateImageCommand, ctx: JobContext
    ) -> Result[ImportResultDto, GenerateError]: ...


class VisualAssetCatalog(Protocol):
    """Stored sets; nothing is computed."""

    def latest(
        self, asset_id: str, *, project_id: str | None = None
    ) -> Result[tuple[VisualAssetSet, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored set of the anchor, newest first."""
        ...

    def load(
        self, set_asset_id: str
    ) -> Result[VisualAssetSet, MediaError | InvalidVisualAssetDocument]: ...


__all__ = [
    "ADJUSTMENT_OPERATION",
    "ADJUSTMENT_VERSION",
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "ENHANCEMENT_OPERATION",
    "EXPORT_OPERATION",
    "FRAME_GRAB_OPERATION",
    "GENERATED_TAG",
    "PROFILES",
    "PROFILE_VERSION",
    "SET_OPERATION",
    "AdjustError",
    "AdjustImageCommand",
    "AdjustmentFailed",
    "AdjustmentSettings",
    "AssetKind",
    "AssetRequest",
    "BackgroundRemoval",
    "Candidate",
    "Canvas",
    "Centering",
    "Contrast",
    "CutoutDefaults",
    "EditRef",
    "Enhancement",
    "ExportRefused",
    "ExportSettings",
    "FrameError",
    "FrameGrab",
    "FrameGrabFailed",
    "FrameGrabResult",
    "FrameGrabber",
    "FramePosition",
    "GapVisuals",
    "GenerateError",
    "GenerateImageCommand",
    "GeneratedImage",
    "GenerationKind",
    "GenerationNotAvailable",
    "GenerationRecord",
    "GenerationRefused",
    "GenerationRequest",
    "Glow",
    "GrabFrameCommand",
    "HexColor",
    "ImageAdjuster",
    "ImageFormat",
    "ImageGenerationService",
    "ImageGenerator",
    "ImageSelection",
    "ImageSelector",
    "ImageUnreadable",
    "InputHow",
    "InputRecord",
    "InvalidVisualAssetConfig",
    "InvalidVisualAssetDocument",
    "InvalidVisualAssetRequest",
    "ItemStatus",
    "MaskLimits",
    "NoVideoStream",
    "Outline",
    "PrepareError",
    "PrepareVisualAssetsCommand",
    "PreparedAsset",
    "PreparedVisualAssets",
    "Provenance",
    "QcCategory",
    "QcIssue",
    "QcThresholds",
    "ResultState",
    "RimLight",
    "SegmentationRejected",
    "SelectImagesCommand",
    "SelectionSettings",
    "Severity",
    "Shadow",
    "Silhouette",
    "SourceTier",
    "StageReport",
    "StageState",
    "StyleRef",
    "Tilt",
    "VariantName",
    "Variants",
    "VisualAssetCatalog",
    "VisualAssetConfig",
    "VisualAssetError",
    "VisualAssetPreparer",
    "VisualAssetProfile",
    "VisualAssetSet",
]
