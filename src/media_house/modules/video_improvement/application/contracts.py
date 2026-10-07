"""The Video Improvement PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``video_improvement``.

``execute`` is blocking (FFmpeg, frame analysis, file I/O): run it as a job. The original asset
is never modified: the result is a NEW asset derived from it, carrying its processing
provenance in the asset metadata, readable by anyone through ``read_provenance(asset.metadata)``
without calling this module. When the footage needs nothing, ``result.changed`` is false and
``result.asset`` is the source itself.

Typical use::

    result = improver.execute(
        ImproveVideoCommand(clip_id, source_profile="gopro_hero_9", processing_profile="outdoor"),
        ctx,
    )
    if isinstance(result, Ok):
        video = result.value.asset               # derived asset, same frames, same timing
        result.value.provenance.applied(ProcessingStage.COLOR)

Settings are the same mapping everywhere (code, tests, a future UI): ``overrides`` takes dotted
paths such as ``{"color.saturation_max": 0.38, "denoise.enabled": False}``.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from media_house.modules.video_improvement.application.calibration import CalibrationResult
from media_house.modules.video_improvement.application.improve_video import (
    ImproveError,
    ImproveVideoCommand,
    VideoImprovementResult,
)
from media_house.modules.video_improvement.domain.errors import (
    InvalidLut,
    InvalidProfile,
    UnreadableSource,
    UnsupportedSource,
    VerificationFailed,
    VideoImprovementError,
)
from media_house.modules.video_improvement.domain.profiles import (
    DEFAULT_PROCESSING_PROFILE,
    processing_profile_names,
)
from media_house.modules.video_improvement.domain.provenance import (
    OperationRecord,
    ProcessingProvenance,
)
from media_house.modules.video_improvement.domain.source import source_profile_names
from media_house.modules.video_improvement.domain.values import (
    METADATA_KEY,
    JsonValue,
    ProcessingStage,
    StageStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import MediaHouseError, Result


class VideoImprover(Protocol):
    """Improve one video asset into a new, natural, consistent and colour-managed asset."""

    def execute(
        self,
        command: ImproveVideoCommand,
        ctx: JobContext,
    ) -> Result[VideoImprovementResult, ImproveError]: ...


class VideoCalibration(Protocol):
    """Derive explicit configuration overrides from representative reference footage."""

    def execute(
        self,
        reference_asset_ids: Sequence[str],
        ctx: JobContext,
        *,
        source_profile: str | None = None,
    ) -> Result[CalibrationResult, MediaHouseError]: ...


def read_provenance(metadata: Mapping[str, JsonValue]) -> ProcessingProvenance | None:
    """What was done to an asset, from its metadata; ``None`` if it carries no valid record.

    The way for any module to learn about prior video processing. ``None`` means "unknown".
    """
    return ProcessingProvenance.from_metadata(metadata)


__all__ = [
    "DEFAULT_PROCESSING_PROFILE",
    "METADATA_KEY",
    "CalibrationResult",
    "ImproveError",
    "ImproveVideoCommand",
    "InvalidLut",
    "InvalidProfile",
    "OperationRecord",
    "ProcessingProvenance",
    "ProcessingStage",
    "StageStatus",
    "UnreadableSource",
    "UnsupportedSource",
    "VerificationFailed",
    "VideoCalibration",
    "VideoImprovementError",
    "VideoImprovementResult",
    "VideoImprover",
    "processing_profile_names",
    "read_provenance",
    "source_profile_names",
]
