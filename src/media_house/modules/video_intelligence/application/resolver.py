"""Upstream inputs: resolve, reuse, request, degrade. The one place that talks to other modules.

For every input the order is the same:

1. RESOLVE  look for a stored result (Media Library, through the owner's public contract);
2. REUSE    a trustworthy match is used as it is (``reused``);
3. REQUEST  otherwise the OWNER is asked to produce it and stores it itself (``requested``);
4. DEGRADE  if the owner cannot deliver an optional input, continue with an explicit
            ``not_available`` and a warning. Nothing is guessed.

This module contains no probing code: standalone use simply means the owner is asked.
"""

from media_house.modules.media_inspection.application.contracts import (
    FrameRateMode,
    InspectMediaCommand,
    MediaInspection,
    MediaInspector,
    VideoStream,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.video_improvement.application.contracts import (
    ProcessingStage,
    StageStatus,
    read_provenance,
)
from media_house.modules.video_intelligence.domain.errors import (
    InspectionUnavailable,
    NoVideoStream,
    UnreadableVideo,
)
from media_house.modules.video_intelligence.domain.source import (
    InputUse,
    ProcessingHistory,
    ResolvedInputs,
    SourceInfo,
)
from media_house.modules.video_intelligence.domain.values import (
    InputSource,
    MeasuredOn,
    Stabilization,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Processing stages that change camera motion. The video improver has none today; the rule is
#: here so a stabilizing stage added later is recognised without touching the analysis.
STABILIZING_STAGES = frozenset[str]({"stabilize"})


class UpstreamResolver:
    """Gathers the technical facts and the processing history of one asset version."""

    def __init__(self, inspector: MediaInspector) -> None:
        self._inspector = inspector

    def resolve(self, asset: MediaAssetDto, ctx: JobContext) -> ResolvedInputs:
        """Raises ``NoVideoStream`` / ``UnreadableVideo`` / ``InspectionUnavailable``."""
        inspection, source_kind, inspection_asset = self._inspection(asset, ctx)
        video = inspection.primary_video
        if not inspection.observed.integrity.readable:
            raise UnreadableVideo(inspection.observed.integrity.read_error or "unreadable")
        if video is None:
            raise NoVideoStream
        history, history_use = _history(asset)
        used = (
            InputUse(
                "media_inspection",
                source_kind,
                inspection_asset,
                f"inspection_v{inspection.inspection_version}",
            ),
            history_use,
            InputUse(
                "black_frame_findings",
                InputSource.NOT_AVAILABLE,
                detail="media_inspection reports no black or frozen frame findings",
            ),
            InputUse(
                "audio_intelligence",
                InputSource.NOT_APPLICABLE,
                detail="speech and activity hints are not used by this version",
            ),
            InputUse(
                "project_brief",
                InputSource.NOT_AVAILABLE,
                detail="no project brief is available; a neutral profile is used",
            ),
        )
        return ResolvedInputs(_source_info(inspection, video), history, used)

    def _inspection(
        self, asset: MediaAssetDto, ctx: JobContext
    ) -> tuple[MediaInspection, InputSource, str | None]:
        found = self._inspector.find(asset.id)
        if found is not None:
            _log.info("Inspection reused", asset_id=asset.id)
            return found.inspection, InputSource.REUSED, found.asset.id
        requested = self._inspector.execute(InspectMediaCommand(asset.id), ctx)
        if isinstance(requested, Err):
            raise InspectionUnavailable(str(requested.error))
        _log.info("Inspection requested from its owner", asset_id=asset.id)
        return requested.value.inspection, InputSource.REQUESTED, requested.value.asset.id


def _history(asset: MediaAssetDto) -> tuple[ProcessingHistory, InputUse]:
    """Which version of the footage this is and what produced it, from public provenance."""
    if asset.derivation is None:
        return (
            ProcessingHistory(MeasuredOn.ORIGINAL, Stabilization.NO),
            InputUse(
                "processing_history",
                InputSource.NOT_APPLICABLE,
                detail="the asset is an original, nothing was processed",
            ),
        )
    parent = asset.derivation.source_asset_id
    provenance = read_provenance(asset.metadata)
    if provenance is None:
        return (
            ProcessingHistory(
                MeasuredOn.UNKNOWN, Stabilization.UNKNOWN, derived_from_asset_id=parent
            ),
            InputUse(
                "processing_history",
                InputSource.NOT_AVAILABLE,
                asset.id,
                detail="the asset is derived but carries no readable processing record",
            ),
        )
    applied = tuple(o for o in provenance.operations if o.status is StageStatus.APPLIED)
    stabilized = any(_stabilizes(o.stage) for o in applied)
    return (
        ProcessingHistory(
            MeasuredOn.IMPROVED,
            Stabilization.YES if stabilized else Stabilization.NO,
            tuple(o.name for o in applied),
            parent,
        ),
        InputUse(
            "processing_history",
            InputSource.REUSED,
            asset.id,
            f"video_processing_v{provenance.processing_version}",
        ),
    )


def _stabilizes(stage: ProcessingStage) -> bool:
    return stage.value in STABILIZING_STAGES


def _source_info(inspection: MediaInspection, video: VideoStream) -> SourceInfo:
    width, height = video.geometry.display_size
    rate = video.frame_rate.value or video.average_frame_rate.value
    timing = video.timing
    production = inspection.observed.production
    timecode = inspection.observed.timecode
    return SourceInfo(
        width=width,
        height=height,
        frame_rate=rate,
        duration_seconds=video.duration or inspection.container.duration,
        declared_frame_count=video.declared_frame_count
        or (timing.packet_count if timing is not None else None),
        rotation=video.geometry.rotation.value or 0,
        variable_frame_rate=None if timing is None else timing.mode is FrameRateMode.VARIABLE,
        color_transfer=video.color.transfer.value,
        color_range=video.color.range.value,
        camera_make=production.camera_make,
        camera_model=production.camera_model,
        timecode=str(timecode.start) if timecode is not None else None,
        reel_id=production.reel_id,
        clip_id=production.clip_id,
    )
