"""The Video Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``video_intelligence``.

``VideoAnalyzer.execute`` is blocking (FFmpeg decode, frame analysis, file I/O): run it as a job.
It analyses ONE asset version (the original or an improved derivative; results are addressed by
asset, never by file name) and returns a typed ``VideoAnalysis`` plus the Media Library assets it
lives in. ``created`` is ``False`` when the identical result already existed and nothing ran.

The analysis OBSERVES and ASSESSES; it never recommends. Typical use from another module::

    result = analyzer.execute(AnalyzeVideoCommand(clip_asset_id, profile="standard"), ctx)
    if isinstance(result, Ok):
        analysis = result.value.analysis
        steady = find_shots(
            analysis,
            ShotFilter(camera_movements=frozenset({CameraMovement.STATIC}), min_sharpness=0.5),
            RankBy.SHARPNESS,
        )
        shot = analysis.shot_at(frame=240)
        start = shot.range.start        # FrameTime: frame index + pts + rational time base

Conventions: every time is a ``FrameTime`` (frame index in presentation order from the first
presented frame, pts in ticks of an exact rational time base); pictures are in display
orientation. See the module README for the full contract.
"""

from typing import Protocol

from media_house.core.domain import FrameTime, Rational, TimeRange
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.video_intelligence.application.analyze_video import (
    AnalyzeVideoCommand,
    AnalyzeVideoError,
    VideoAnalysisResult,
)
from media_house.modules.video_intelligence.application.shot_index import IndexedAsset
from media_house.modules.video_intelligence.domain.analyzers import ANALYZERS, AnalyzerSpec
from media_house.modules.video_intelligence.domain.errors import (
    InspectionUnavailable,
    InvalidAnalysis,
    InvalidAnalysisDocument,
    InvalidProfile,
    NoVideoStream,
    UnreadableVideo,
    VideoIntelligenceError,
)
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.observations import (
    Assessed,
    AttentionPoint,
    BodyObservation,
    BoundaryObservation,
    CameraObservation,
    CompositionObservation,
    ContentTypeObservation,
    ContinuityObservation,
    CropSafeRegion,
    Embedding,
    EntitiesObservation,
    Event,
    Evidence,
    FaceObservation,
    FramingObservation,
    GeometryObservation,
    Gesture,
    HandlesObservation,
    IdentityCluster,
    IndicatorObservation,
    Keyframes,
    LightingObservation,
    MotionObservation,
    Overlay,
    QualityAssessment,
    QualityMetrics,
    RetakeGroup,
    Scene,
    Score,
    TextItem,
    TextObservation,
    Track,
    TrackPoint,
    VisualMeaningObservation,
)
from media_house.modules.video_intelligence.domain.profiles import (
    CONTENT_PROFILES,
    DEFAULT_PROFILE,
    PROFILES,
    RUBRIC_VERSION,
    CinemaSettings,
    ContentTypeProfile,
    FaceSettings,
    MeaningSettings,
    MeasurementSettings,
    MotionSettings,
    ProcessingProfile,
    QualitySettings,
    RuntimeConfig,
    ShotSettings,
    TextSettings,
    TrackingSettings,
    content_profile,
    get_profile,
)
from media_house.modules.video_intelligence.domain.query import RankBy, ShotFilter, find_shots
from media_house.modules.video_intelligence.domain.result import (
    AnalyzerReport,
    Curve,
    Provenance,
    Shot,
    VideoAnalysis,
)
from media_house.modules.video_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.video_intelligence.domain.shot_index import (
    INDEX_VERSION,
    IndexedEvent,
    IndexedShot,
    ShotIndex,
    build_shot_index,
)
from media_house.modules.video_intelligence.domain.source import (
    InputUse,
    ProcessingHistory,
    SourceInfo,
)
from media_house.modules.video_intelligence.domain.validation import validate_analysis
from media_house.modules.video_intelligence.domain.values import (
    PROCESSING_VERSION,
    SCHEMA_VERSION,
    AnalyzerId,
    AnalyzerState,
    BoundaryKind,
    CacheOutcome,
    CameraMovement,
    CostTier,
    DeviceKind,
    EntityKind,
    EventKind,
    FramingType,
    GestureKind,
    InputSource,
    MeasuredOn,
    OverlayKind,
    RelationKind,
    ScoreName,
    Stabilization,
)
from media_house.modules.video_intelligence.domain.vocabulary import VOCABULARY_VERSION
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Result


class VideoAnalyzer(Protocol):
    """Analyse one video asset version into a ``VideoAnalysis`` (reusing everything stored)."""

    def execute(
        self,
        command: AnalyzeVideoCommand,
        ctx: JobContext,
    ) -> Result[VideoAnalysisResult, AnalyzeVideoError]: ...


class VideoCatalog(Protocol):
    """Read stored analyses. Nothing is computed."""

    def latest(
        self, asset_id: str, profile: str | None = None
    ) -> Result[tuple[VideoAnalysis, MediaAssetDto] | None, MediaError]: ...


class ShotIndexCatalog(Protocol):
    """The searchable shot rows of an asset's newest stored analysis, for library-wide search.

    The index is a small derived document built from the stored analysis the first time it is asked
    for and reused afterwards, so searching many assets never parses their full analyses. Nothing
    is analysed: an asset without a stored analysis answers ``Ok(None)``. Blocking (file I/O).
    """

    def shot_index(self, asset_id: str) -> Result[IndexedAsset | None, MediaError]: ...


class VisualTextEncoder(Protocol):
    """Words into the embedding space of the shots (the CLIP text tower the embeddings come from).

    A vector from ``embed`` is comparable (dot product, unit length) with ``IndexedShot.vector()``
    of an index whose ``embedding_model`` equals ``identity()``; a different model is a different
    space and must not be compared. ``unavailable`` says why it cannot run now (library, model
    files), else ``None``; nothing is ever downloaded. Blocking.
    """

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...

    def embed(
        self, texts: list[str], cancellation: CancellationToken
    ) -> list[tuple[float, ...]]: ...


__all__ = [
    "ANALYZERS",
    "CONTENT_PROFILES",
    "DEFAULT_PROFILE",
    "INDEX_VERSION",
    "PROCESSING_VERSION",
    "PROFILES",
    "RUBRIC_VERSION",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "AnalyzeVideoCommand",
    "AnalyzeVideoError",
    "AnalyzerId",
    "AnalyzerReport",
    "AnalyzerSpec",
    "AnalyzerState",
    "Assessed",
    "AttentionPoint",
    "BBox",
    "BodyObservation",
    "BoundaryKind",
    "BoundaryObservation",
    "CacheOutcome",
    "CameraMovement",
    "CameraObservation",
    "CinemaSettings",
    "CompositionObservation",
    "ContentTypeObservation",
    "ContentTypeProfile",
    "ContinuityObservation",
    "CostTier",
    "CropSafeRegion",
    "Curve",
    "DeviceKind",
    "Embedding",
    "EntitiesObservation",
    "EntityKind",
    "Event",
    "EventKind",
    "Evidence",
    "FaceObservation",
    "FaceSettings",
    "FrameTime",
    "FramingObservation",
    "FramingType",
    "GeometryObservation",
    "Gesture",
    "GestureKind",
    "HandlesObservation",
    "IdentityCluster",
    "IndexedAsset",
    "IndexedEvent",
    "IndexedShot",
    "IndicatorObservation",
    "InputSource",
    "InputUse",
    "InspectionUnavailable",
    "InvalidAnalysis",
    "InvalidAnalysisDocument",
    "InvalidProfile",
    "Keyframes",
    "LightingObservation",
    "MeaningSettings",
    "MeasuredOn",
    "MeasurementSettings",
    "MotionObservation",
    "MotionSettings",
    "NoVideoStream",
    "Overlay",
    "OverlayKind",
    "ProcessingHistory",
    "ProcessingProfile",
    "Provenance",
    "QualityAssessment",
    "QualityMetrics",
    "QualitySettings",
    "RankBy",
    "Rational",
    "RelationKind",
    "RetakeGroup",
    "RuntimeConfig",
    "Scene",
    "Score",
    "ScoreName",
    "Shot",
    "ShotFilter",
    "ShotIndex",
    "ShotIndexCatalog",
    "ShotSettings",
    "SourceInfo",
    "Stabilization",
    "TextItem",
    "TextObservation",
    "TextSettings",
    "TimeRange",
    "Track",
    "TrackPoint",
    "TrackingSettings",
    "UnreadableVideo",
    "VideoAnalysis",
    "VideoAnalysisResult",
    "VideoAnalyzer",
    "VideoCatalog",
    "VideoIntelligenceError",
    "VisualMeaningObservation",
    "VisualTextEncoder",
    "analysis_from_json",
    "analysis_to_json",
    "build_shot_index",
    "content_profile",
    "find_shots",
    "get_profile",
    "validate_analysis",
]
