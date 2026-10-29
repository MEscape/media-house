"""The Content Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``content_intelligence``.

``ContentAnalyzer.execute`` is blocking (it may ask Audio Intelligence for a transcript, run an
embedding model and call a language model): run it as a job. It returns a typed
``ContentAnalysis`` that UNDERSTANDS the recording (topics, key information, relationships,
scores, opportunities). Every score is advisory evidence, never an edit decision.
``created`` is ``False`` when the identical result already existed.

    result = analyzer.execute(AnalyzeContentCommand(source_asset_id=clip_id), ctx)
    if isinstance(result, Ok):
        analysis = result.value.analysis
        clips = highlights(analysis, minimum=0.6, limit=5)
        for segment in clips:
            ev = evidence_for(analysis, segment.segment_id)   # context a clip needs

Stored results are read without recomputation through ``ContentCatalog``.
"""

from typing import Protocol

from media_house.modules.content_intelligence.application.analyze_content import (
    AnalyzeContentCommand,
    AnalyzeContentError,
    AudioUse,
    CacheLayer,
    ContentAnalysisResult,
    VideoUse,
)
from media_house.modules.content_intelligence.domain.config import (
    PROFILES,
    ContentConfig,
    EmbeddingSettings,
    LlmScope,
    LlmSettings,
    Profile,
    ProfileSettings,
    Thresholds,
)
from media_house.modules.content_intelligence.domain.errors import (
    ContentIntelligenceError,
    InvalidAnalysis,
    InvalidAnalysisDocument,
    InvalidConfiguration,
    InvalidTranscriptInput,
    TranscriptUnavailable,
)
from media_house.modules.content_intelligence.domain.model import (
    Boundary,
    ContentAnalysis,
    Evidence,
    InputRecord,
    KeyItem,
    Observation,
    Opportunity,
    Provenance,
    Ref,
    Relationship,
    RhetoricalMarker,
    RoleLabel,
    Score,
    SemanticSegment,
    SourceRecord,
    StageReport,
    Summary,
    Topic,
)
from media_house.modules.content_intelligence.domain.query import (
    SegmentEvidence,
    evidence_for,
    highlights,
    key_items,
    opportunities,
    ranked,
    redundancy_links,
    related_segments,
    relationships_of,
    removal_candidates,
    segments_between,
    segments_of_topic,
    standalone_candidates,
    topics_at,
)
from media_house.modules.content_intelligence.domain.references import (
    Designation,
    Reference,
    ReferenceKind,
    ReferenceState,
    designations,
    resolve_in_texts,
    resolve_references,
)
from media_house.modules.content_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.content_intelligence.domain.source import (
    SourceSegment,
    SourceTranscript,
    SourceWord,
)
from media_house.modules.content_intelligence.domain.span import Span
from media_house.modules.content_intelligence.domain.stance import Stance, stance_of
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.validation import validate_analysis
from media_house.modules.content_intelligence.domain.values import (
    PROCESSING_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Adds,
    GraphicKind,
    KeyKind,
    Method,
    OpportunityKind,
    Origin,
    RelationKind,
    ResultState,
    RhetoricalKind,
    Role,
    ScoreName,
    SegmentLevel,
    StageState,
    TimeBasis,
    VisualCategory,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Result


class ContentAnalyzer(Protocol):
    """Analyse a recording (or a supplied transcript) into a ``ContentAnalysis``."""

    def execute(
        self,
        command: AnalyzeContentCommand,
        ctx: JobContext,
    ) -> Result[ContentAnalysisResult, AnalyzeContentError]: ...


class ContentCatalog(Protocol):
    """Read stored analyses. Nothing is computed."""

    def latest(
        self, asset_id: str
    ) -> Result[
        tuple[ContentAnalysis, MediaAssetDto] | None, MediaError | InvalidAnalysisDocument
    ]: ...

    def load(
        self, analysis_asset_id: str
    ) -> Result[ContentAnalysis, MediaError | InvalidAnalysisDocument]: ...


class TextEncoder(Protocol):
    """Short texts to multilingual sentence vectors (the engine Content Intelligence uses).

    ``unavailable`` says why it cannot run now (library, model files), else ``None``. Nothing is
    ever downloaded; ``identity`` names the engine and model for cache keys. Blocking.
    """

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...

    def embed(self, texts: list[str], cancellation: CancellationToken) -> list[list[float]]: ...


class TextGenerator(Protocol):
    """A grounded-prompt chat model for other modules (the engine Content Intelligence uses).

    ``unavailable`` says why it cannot run now (library, model files), else ``None``. Nothing is
    ever downloaded; ``identity`` names the engine and model for cache keys. Blocking. Decoding is
    deterministic. Raises ``ExternalSystemError`` for a failure of the model runtime.
    """

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...

    def generate(self, system: str, user: str, cancellation: CancellationToken) -> str: ...


__all__ = [
    "PROCESSING_VERSION",
    "PROFILES",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "Adds",
    "AnalyzeContentCommand",
    "AnalyzeContentError",
    "AudioUse",
    "Boundary",
    "CacheLayer",
    "ContentAnalysis",
    "ContentAnalysisResult",
    "ContentAnalyzer",
    "ContentCatalog",
    "ContentConfig",
    "ContentIntelligenceError",
    "Designation",
    "EmbeddingSettings",
    "Evidence",
    "GraphicKind",
    "InputRecord",
    "InvalidAnalysis",
    "InvalidAnalysisDocument",
    "InvalidConfiguration",
    "InvalidTranscriptInput",
    "KeyItem",
    "KeyKind",
    "LlmScope",
    "LlmSettings",
    "Method",
    "Observation",
    "Opportunity",
    "OpportunityKind",
    "Origin",
    "Profile",
    "ProfileSettings",
    "Provenance",
    "Ref",
    "Reference",
    "ReferenceKind",
    "ReferenceState",
    "RelationKind",
    "Relationship",
    "ResultState",
    "RhetoricalKind",
    "RhetoricalMarker",
    "Role",
    "RoleLabel",
    "Score",
    "ScoreName",
    "SegmentEvidence",
    "SegmentLevel",
    "SemanticSegment",
    "SourceRecord",
    "SourceSegment",
    "SourceTranscript",
    "SourceWord",
    "Span",
    "StageReport",
    "StageState",
    "Stance",
    "Summary",
    "TextEncoder",
    "TextGenerator",
    "Thresholds",
    "TimeBasis",
    "Topic",
    "TranscriptUnavailable",
    "Unit",
    "VideoUse",
    "VisualCategory",
    "analysis_from_json",
    "analysis_to_json",
    "designations",
    "evidence_for",
    "highlights",
    "key_items",
    "opportunities",
    "ranked",
    "redundancy_links",
    "related_segments",
    "relationships_of",
    "removal_candidates",
    "resolve_in_texts",
    "resolve_references",
    "segments_between",
    "segments_of_topic",
    "stance_of",
    "standalone_candidates",
    "topics_at",
    "validate_analysis",
]
