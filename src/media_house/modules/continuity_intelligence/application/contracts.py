"""The Continuity Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``continuity_intelligence``.

``ContinuityAnalyzer.execute`` judges the cuts of a PROPOSED SEQUENCE (the clips of an edit with
their source ranges and timeline places, built by the caller from the edit plan) and explains what
could distract or confuse a viewer: jump cuts, breaks of look, light, direction, eyeline, action,
identity, sound and chronology. It READS the stored video, audio and alignment results and the
B-roll analysis; it diagnoses and recommends, and changes no media, timeline or decision. A
difference is not an error: every issue carries an estimate of how likely it is deliberate, and
what could be repaired, by whom, and how far each repair was examined. It is blocking (library
reads): run it as a job.

    sequence = ProposedSequence("ep_1", 3, clips=(ProposedClip(...), ...))
    command = AnalyzeContinuityCommand(sequence, ContinuityConfig("talking_head"))
    result = analyzer.execute(command, ctx)
    if isinstance(result, Ok):
        analysis = result.value.analysis      # ContinuityAnalysis (created is False on a reuse)
        for issue in analysis.surfaced():     # worth the editing stage's attention
            issue.type, issue.level, issue.scores, issue.repairs, issue.explanation
        analysis.risk_between(asset_id, leaves, resumes)   # risk of one cut, for a consumer

Stored analyses are read without recomputation through ``ContinuityCatalog``.
"""

from typing import Protocol

from media_house.modules.continuity_intelligence.application.analyze_continuity import (
    AnalyzeContinuityCommand,
    AnalyzeContinuityError,
    ContinuityResult,
)
from media_house.modules.continuity_intelligence.domain.errors import (
    ContinuityIntelligenceError,
    InvalidContinuityAnalysis,
    InvalidContinuityConfig,
    InvalidContinuityDocument,
    InvalidSequence,
    NothingToJudge,
)
from media_house.modules.continuity_intelligence.domain.facts import (
    ProposedClip,
    ProposedSequence,
)
from media_house.modules.continuity_intelligence.domain.model import (
    AlternativeRef,
    CompatiblePair,
    ContinuityAnalysis,
    EvidenceRef,
    InputRecord,
    Issue,
    IssueDecision,
    Metrics,
    Provenance,
    Repair,
    Score,
    SourceSpan,
    StageReport,
    TransitionRecord,
    Unresolved,
    ValidationIssue,
)
from media_house.modules.continuity_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    ContinuityConfig,
    ContinuityProfile,
)
from media_house.modules.continuity_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.continuity_intelligence.domain.values import (
    ANALYZER_VERSION,
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Availability,
    Decision,
    IdentityVerdict,
    InputHow,
    IssueType,
    Level,
    Owner,
    RepairKind,
    ResultState,
    Scope,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    TransitionKind,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ContinuityAnalyzer(Protocol):
    """Judge the cuts of a proposed sequence."""

    def execute(
        self, command: AnalyzeContinuityCommand, ctx: JobContext
    ) -> Result[ContinuityResult, AnalyzeContinuityError]: ...


class ContinuityCatalog(Protocol):
    """Read stored analyses. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        profile: str | None = None,
        edit_plan_id: str | None = None,
    ) -> Result[tuple[ContinuityAnalysis, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored analysis of the asset, newest first (earlier ones are never changed)."""
        ...

    def load(
        self, analysis_asset_id: str
    ) -> Result[ContinuityAnalysis, MediaError | InvalidContinuityDocument]: ...


__all__ = [
    "ANALYZER_VERSION",
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "PROFILES",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "AlternativeRef",
    "AnalyzeContinuityCommand",
    "AnalyzeContinuityError",
    "Availability",
    "CompatiblePair",
    "ContinuityAnalysis",
    "ContinuityAnalyzer",
    "ContinuityCatalog",
    "ContinuityConfig",
    "ContinuityIntelligenceError",
    "ContinuityProfile",
    "ContinuityResult",
    "Decision",
    "EvidenceRef",
    "IdentityVerdict",
    "InputHow",
    "InputRecord",
    "InvalidContinuityAnalysis",
    "InvalidContinuityConfig",
    "InvalidContinuityDocument",
    "InvalidSequence",
    "Issue",
    "IssueDecision",
    "IssueType",
    "Level",
    "Metrics",
    "NothingToJudge",
    "Owner",
    "ProposedClip",
    "ProposedSequence",
    "Provenance",
    "Repair",
    "RepairKind",
    "ResultState",
    "Scope",
    "Score",
    "ScoreBasis",
    "ScoreName",
    "Severity",
    "SourceSpan",
    "StageReport",
    "StageState",
    "TransitionKind",
    "TransitionRecord",
    "Unresolved",
    "ValidationIssue",
    "analysis_from_json",
    "analysis_to_json",
]
