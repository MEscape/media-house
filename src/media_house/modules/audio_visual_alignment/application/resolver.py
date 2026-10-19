"""The single boundary to the upstream modules: resolve, reuse, request, degrade.

For each input: the caller's object (``supplied``), else a stored result (``reused``), else, only
when the command allows it, the owner computes it (``requested``), else it is ``not_available``.
Audio and video analyses are required; content analysis and the inspection are optional. Stored
results are read through the owners' catalogs and never computed by a lookup. An inspection is a
cheap probe done once per asset, so it is always requested when missing; the expensive analyses
are requested only for ``Acquire.REQUEST``.
"""

import hashlib
from dataclasses import dataclass

from media_house.modules.audio_intelligence.application.contracts import (
    AnalyzeAudioCommand,
    AudioAnalyzer,
    AudioCatalog,
    AudioIntelligenceTimeline,
    timeline_to_json,
)
from media_house.modules.audio_visual_alignment.application.views import (
    audio_facts,
    clock_facts,
    content_facts,
    video_facts,
)
from media_house.modules.audio_visual_alignment.domain.errors import (
    IncompatibleInputs,
    MissingPrerequisite,
)
from media_house.modules.audio_visual_alignment.domain.inputs import (
    AudioFacts,
    ClockFacts,
    ContentFacts,
    VideoFacts,
)
from media_house.modules.audio_visual_alignment.domain.model import InputRecord
from media_house.modules.audio_visual_alignment.domain.values import Acquire, HowObtained
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalysis,
    ContentAnalyzer,
    ContentCatalog,
    VideoUse,
    analysis_to_json,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectionCatalog,
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzeVideoCommand,
    VideoAnalysis,
    VideoAnalyzer,
    VideoCatalog,
)
from media_house.modules.video_intelligence.application.contracts import (
    analysis_to_json as video_to_json,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_DEFAULT_VIDEO_PROFILE = "standard"


@dataclass(frozen=True, slots=True)
class Request:
    """What the resolver needs from the command (no policy lives anywhere else)."""

    video_asset_id: str
    audio_asset_id: str
    acquire: Acquire
    video_profile: str | None
    use_content: bool
    audio_timeline: AudioIntelligenceTimeline | None
    video_analysis: VideoAnalysis | None
    content_analysis: ContentAnalysis | None


@dataclass(frozen=True, slots=True)
class Resolved:
    audio: AudioFacts
    video: VideoFacts
    content: ContentFacts | None
    clock: ClockFacts | None
    records: tuple[InputRecord, ...]
    #: Identities (checksums or digests) of what was read, in a fixed order, for the cache key.
    identities: tuple[tuple[str, str | None], ...]
    warnings: tuple[str, ...]


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class UpstreamResolver:
    def __init__(
        self,
        audio_catalog: AudioCatalog,
        audio_analyzer: AudioAnalyzer,
        video_catalog: VideoCatalog,
        video_analyzer: VideoAnalyzer,
        content_catalog: ContentCatalog,
        content_analyzer: ContentAnalyzer,
        inspections: InspectionCatalog,
        inspector: MediaInspector,
    ) -> None:
        self._audio_catalog = audio_catalog
        self._audio_analyzer = audio_analyzer
        self._video_catalog = video_catalog
        self._video_analyzer = video_analyzer
        self._content_catalog = content_catalog
        self._content_analyzer = content_analyzer
        self._inspections = inspections
        self._inspector = inspector

    def resolve(self, request: Request, ctx: JobContext) -> Result[Resolved, MediaHouseError]:
        records: list[InputRecord] = []
        identities: list[tuple[str, str | None]] = []
        warnings: list[str] = []

        video = self._video(request, ctx)
        if isinstance(video, MediaHouseError):
            return Err(video)
        analysis, video_asset, how = video
        video_identity = video_asset.checksum if video_asset else _digest(video_to_json(analysis))
        records.append(
            InputRecord(
                name="video_analysis",
                how=how,
                asset_id=analysis.asset_id,
                document_id=video_asset.id if video_asset else None,
                identity=video_identity,
                detail=f"profile {analysis.provenance.profile_name}",
            )
        )
        identities.append(("video", video_identity))

        audio = self._audio(request, ctx)
        if isinstance(audio, MediaHouseError):
            return Err(audio)
        timeline, audio_asset, how = audio
        audio_identity = (
            audio_asset.checksum if audio_asset else _digest(timeline_to_json(timeline))
        )
        records.append(
            InputRecord(
                name="audio_timeline",
                how=how,
                asset_id=timeline.metadata.source_asset_id,
                document_id=audio_asset.id if audio_asset else None,
                identity=audio_identity,
            )
        )
        identities.append(("audio", audio_identity))

        content, record, identity = self._content(request, ctx)
        records.append(record)
        identities.append(("content", identity))

        clock, record, identity = self._inspection(request, ctx)
        records.append(record)
        identities.append(("inspection", identity))
        if clock is None:
            warnings.append("no inspection: the video's time origin could not be checked")
        if request.audio_asset_id != request.video_asset_id:
            warnings.append(
                "the audio is a separate asset: unless a shift is supplied, both clocks are "
                "assumed to start together"
            )
        return Ok(
            Resolved(
                audio=audio_facts(
                    timeline, request.audio_asset_id, audio_asset.id if audio_asset else None
                ),
                video=video_facts(analysis, video_asset.id if video_asset else None),
                content=content,
                clock=clock,
                records=tuple(records),
                identities=tuple(identities),
                warnings=tuple(warnings),
            )
        )

    # --- video -------------------------------------------------------------------------------
    def _video(
        self, request: Request, ctx: JobContext
    ) -> tuple[VideoAnalysis, MediaAssetDto | None, HowObtained] | MediaHouseError:
        if request.video_analysis is not None:
            found: tuple[VideoAnalysis, MediaAssetDto | None, HowObtained] = (
                request.video_analysis,
                None,
                HowObtained.SUPPLIED,
            )
        else:
            stored = self._video_catalog.latest(request.video_asset_id, request.video_profile)
            if isinstance(stored, Err):
                return stored.error
            if stored.value is not None:
                found = (*stored.value, HowObtained.REUSED)
            elif request.acquire is Acquire.REQUEST:
                ran = self._video_analyzer.execute(
                    AnalyzeVideoCommand(
                        request.video_asset_id, request.video_profile or _DEFAULT_VIDEO_PROFILE
                    ),
                    ctx,
                )
                if isinstance(ran, Err):
                    return ran.error
                found = (ran.value.analysis, ran.value.asset, HowObtained.REQUESTED)
            else:
                return MissingPrerequisite(
                    "a stored video analysis",
                    "Run video analysis first, or allow it with Acquire.REQUEST.",
                )
        if found[0].asset_id != request.video_asset_id:
            return IncompatibleInputs(
                f"the video analysis belongs to {found[0].asset_id}, not {request.video_asset_id}"
            )
        return found

    # --- audio -------------------------------------------------------------------------------
    def _audio(
        self, request: Request, ctx: JobContext
    ) -> tuple[AudioIntelligenceTimeline, MediaAssetDto | None, HowObtained] | MediaHouseError:
        if request.audio_timeline is not None:
            found: tuple[AudioIntelligenceTimeline, MediaAssetDto | None, HowObtained] = (
                request.audio_timeline,
                None,
                HowObtained.SUPPLIED,
            )
        else:
            stored = self._audio_catalog.latest(request.audio_asset_id)
            if isinstance(stored, Err):
                return stored.error
            if stored.value is not None:
                found = (*stored.value, HowObtained.REUSED)
            elif request.acquire is Acquire.REQUEST:
                ran = self._audio_analyzer.execute(AnalyzeAudioCommand(request.audio_asset_id), ctx)
                if isinstance(ran, Err):
                    return ran.error
                found = (ran.value.timeline, ran.value.asset, HowObtained.REQUESTED)
            else:
                return MissingPrerequisite(
                    "a stored audio analysis",
                    "Run audio analysis first, or allow it with Acquire.REQUEST.",
                )
        if found[0].metadata.source_asset_id != request.audio_asset_id:
            return IncompatibleInputs(
                f"the audio analysis belongs to {found[0].metadata.source_asset_id}, "
                f"not {request.audio_asset_id}"
            )
        return found

    # --- content (optional) ------------------------------------------------------------------
    def _content(
        self, request: Request, ctx: JobContext
    ) -> tuple[ContentFacts | None, InputRecord, str | None]:
        name = "content_analysis"
        if not request.use_content:
            return (
                None,
                InputRecord(name=name, how=HowObtained.NOT_USED, detail="switched off"),
                None,
            )
        analysis: ContentAnalysis | None = request.content_analysis
        asset: MediaAssetDto | None = None
        how = HowObtained.SUPPLIED
        if analysis is None:
            stored = self._content_catalog.latest(request.audio_asset_id)
            if isinstance(stored, Ok) and stored.value is not None:
                analysis, asset = stored.value
                how = HowObtained.REUSED
            elif request.acquire is Acquire.REQUEST:
                ran = self._content_analyzer.execute(
                    AnalyzeContentCommand(
                        source_asset_id=request.audio_asset_id, video=VideoUse.OFF
                    ),
                    ctx,
                )
                if isinstance(ran, Ok):
                    analysis, asset, how = (
                        ran.value.analysis,
                        ran.value.asset,
                        HowObtained.REQUESTED,
                    )
                else:
                    _log.warning("Content analysis could not be requested", reason=str(ran.error))
        if analysis is None:
            return (
                None,
                InputRecord(
                    name=name,
                    how=HowObtained.NOT_AVAILABLE,
                    detail="no stored content analysis; text matching is skipped",
                ),
                None,
            )
        identity = asset.checksum if asset is not None else _digest(analysis_to_json(analysis))
        record = InputRecord(
            name=name,
            how=how,
            asset_id=analysis.asset_id,
            document_id=asset.id if asset is not None else None,
            identity=identity,
        )
        return content_facts(analysis, asset.id if asset is not None else None), record, identity

    # --- inspection (optional, cheap) ----------------------------------------------------------
    def _inspection(
        self, request: Request, ctx: JobContext
    ) -> tuple[ClockFacts | None, InputRecord, str | None]:
        name = "inspection"
        found = self._inspections.find(request.video_asset_id)
        how = HowObtained.REUSED
        if found is None:
            ran = self._inspector.execute(InspectMediaCommand(request.video_asset_id), ctx)
            if isinstance(ran, Err):
                return (
                    None,
                    InputRecord(
                        name=name, how=HowObtained.NOT_AVAILABLE, detail=ran.error.user_message
                    ),
                    None,
                )
            found, how = ran.value, HowObtained.REQUESTED
        return (
            clock_facts(found.inspection),
            InputRecord(
                name=name,
                how=how,
                asset_id=request.video_asset_id,
                document_id=found.asset.id,
                identity=found.asset.checksum,
            ),
            found.asset.checksum,
        )
