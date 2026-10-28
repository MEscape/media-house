"""The Pacing Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``pacing_intelligence``.

``PacingAnalyzer.execute`` turns the stored analyses of one or more recordings (audio, content,
video, alignment, inspection) and, when there is one, their story plan into a ``PacingAnalysis``:
how the rhythm helps or hurts the material at the scale of words, pauses, passages, shots, story
sections and the whole recording, and the recommendations that would improve it, each with its
evidence, confidence, benefit and trade-off. It is blocking (cheap, pure computation over stored
results): run it as a job. It edits nothing and decides no edit: which operation applies a
recommendation is the editing stage's decision.

    command = AnalyzePacingCommand((asset_id,), PacingConfig(profile="tutorial"))
    result = analyzer.execute(command, ctx)
    if isinstance(result, Ok):
        analysis = result.value.analysis
        for rec in analysis.recommendations:        # ordered by priority
            rec.type, rec.target, rec.delta_seconds, rec.confidence, rec.tradeoffs, rec.safety
        analysis.pauses_of(PauseVerdict.DEAD_AIR)   # what each silence is
        analysis.duration                           # current / proposed runtime, target check

Stored analyses are read without recomputation through ``PacingCatalog``; ``created`` is ``False``
when the identical analysis already existed.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.pacing_intelligence.application.analyze_pacing import (
    AnalyzePacingCommand,
    AnalyzePacingError,
    PacingResult,
)
from media_house.modules.pacing_intelligence.domain.errors import (
    InvalidPacingAnalysis,
    InvalidPacingConfig,
    InvalidPacingDocument,
    MissingEvidence,
    NothingToAnalyse,
    PacingIntelligenceError,
)
from media_house.modules.pacing_intelligence.domain.model import (
    AttentionBin,
    DurationEstimate,
    EvidenceRef,
    InputRecord,
    Issue,
    Measure,
    PacingAnalysis,
    PassageAnalysis,
    PauseAnalysis,
    Provenance,
    Recommendation,
    RhythmFinding,
    SectionAnalysis,
    ShotAnalysis,
    SourceRecord,
    StageReport,
    StoryRef,
    StrategyProjection,
    TargetCheck,
    TimelineAnalysis,
    TimeSpan,
)
from media_house.modules.pacing_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    STRATEGIES,
    PacingConfig,
    PacingProfile,
    StrategyAdjust,
    TargetDuration,
)
from media_house.modules.pacing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.pacing_intelligence.domain.values import (
    ANALYZER_VERSION,
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Basis,
    EvidenceKind,
    InputHow,
    IssueKind,
    Level,
    PaceClass,
    PauseVerdict,
    Precision,
    ReasonCode,
    RecommendationType,
    ResultState,
    Safety,
    Severity,
    StageState,
    Strategy,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class PacingAnalyzer(Protocol):
    """Analyse the pacing of one or more recordings."""

    def execute(
        self, command: AnalyzePacingCommand, ctx: JobContext
    ) -> Result[PacingResult, AnalyzePacingError]: ...


class PacingCatalog(Protocol):
    """Read stored analyses. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        profile: str | None = None,
        story_plan_id: str | None = None,
    ) -> Result[tuple[PacingAnalysis, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored analysis of the asset, newest first (earlier ones are never changed)."""
        ...

    def load(
        self, analysis_asset_id: str
    ) -> Result[PacingAnalysis, MediaError | InvalidPacingDocument]: ...


__all__ = [
    "ANALYZER_VERSION",
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "PROFILES",
    "SCHEMA_VERSION",
    "STRATEGIES",
    "VOCABULARY_VERSION",
    "AnalyzePacingCommand",
    "AnalyzePacingError",
    "AttentionBin",
    "Basis",
    "DurationEstimate",
    "EvidenceKind",
    "EvidenceRef",
    "InputHow",
    "InputRecord",
    "InvalidPacingAnalysis",
    "InvalidPacingConfig",
    "InvalidPacingDocument",
    "Issue",
    "IssueKind",
    "Level",
    "Measure",
    "MissingEvidence",
    "NothingToAnalyse",
    "PaceClass",
    "PacingAnalysis",
    "PacingAnalyzer",
    "PacingCatalog",
    "PacingConfig",
    "PacingIntelligenceError",
    "PacingProfile",
    "PacingResult",
    "PassageAnalysis",
    "PauseAnalysis",
    "PauseVerdict",
    "Precision",
    "Provenance",
    "ReasonCode",
    "Recommendation",
    "RecommendationType",
    "ResultState",
    "RhythmFinding",
    "Safety",
    "SectionAnalysis",
    "Severity",
    "ShotAnalysis",
    "SourceRecord",
    "StageReport",
    "StageState",
    "StoryRef",
    "Strategy",
    "StrategyAdjust",
    "StrategyProjection",
    "TargetCheck",
    "TargetDuration",
    "TimeSpan",
    "TimelineAnalysis",
    "analysis_from_json",
    "analysis_to_json",
]
