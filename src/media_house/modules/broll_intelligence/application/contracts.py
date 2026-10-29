"""The B-roll Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``broll_intelligence``.

``BrollAnalyzer.execute`` finds, for the passages of a story's recordings that would gain from
supplementary footage, the footage of the WHOLE library that genuinely supports them, and says where
in it, how sure it is and why. It READS the stored story plan, content, audio, video, alignment and
pacing results and the library's shot indexes (it analyses nothing, edits nothing and builds no
timeline). Footage that is only illustrative is labelled as such; a passage nothing qualifies for is
a coverage gap, not a forced match. It is blocking (library reads, an optional embedding model):
run it as a job.

    result = analyzer.execute(AnalyzeBrollCommand((asset_id,), BrollConfig("tutorial")), ctx)
    if isinstance(result, Ok):
        analysis = result.value.analysis        # BrollAnalysis (created is False on a reuse)
        for rec in analysis.recommendations:
            best = rec.best                      # None when nothing qualifies
            if best is not None and best.support is Support.SATISFIES:
                best.asset_id, best.source       # ORIGINAL asset and source range (exact seconds)
                best.scores, best.evidence       # separate scores; what each rests on
        analysis.gaps                            # places the library cannot cover, and why

Stored analyses are read without recomputation through ``BrollCatalog``.
"""

from typing import Protocol

from media_house.modules.broll_intelligence.application.analyze_broll import (
    AnalyzeBrollCommand,
    AnalyzeBrollError,
    BrollResult,
)
from media_house.modules.broll_intelligence.application.pool import LibraryScope
from media_house.modules.broll_intelligence.domain.errors import (
    BrollIntelligenceError,
    InvalidBrollAnalysis,
    InvalidBrollConfig,
    InvalidBrollDocument,
    MissingTargetEvidence,
    NothingToCover,
)
from media_house.modules.broll_intelligence.domain.model import (
    BrollAnalysis,
    Candidate,
    Considered,
    CoverageGap,
    EvidenceRef,
    InputRecord,
    Issue,
    NoChange,
    PoolRecord,
    Provenance,
    Recommendation,
    Score,
    Searched,
    SourceInterval,
    StageReport,
    StoryRef,
    SuggestedPlacement,
)
from media_house.modules.broll_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    BrollConfig,
    BrollProfile,
)
from media_house.modules.broll_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.broll_intelligence.domain.values import (
    ANALYZER_VERSION,
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    AssetVia,
    Association,
    AudioBehavior,
    ClockBasis,
    EvidenceRole,
    GapKind,
    Identity,
    InputHow,
    NeedKind,
    Precision,
    Priority,
    Purpose,
    ResultState,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    Support,
    TargetOrigin,
    UseKind,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class BrollAnalyzer(Protocol):
    """Find and rank supplementary footage for a story's passages."""

    def execute(
        self, command: AnalyzeBrollCommand, ctx: JobContext
    ) -> Result[BrollResult, AnalyzeBrollError]: ...


class BrollCatalog(Protocol):
    """Read stored analyses. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        profile: str | None = None,
        story_plan_id: str | None = None,
    ) -> Result[tuple[BrollAnalysis, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored analysis of the asset, newest first (earlier ones are never changed)."""
        ...

    def load(
        self, analysis_asset_id: str
    ) -> Result[BrollAnalysis, MediaError | InvalidBrollDocument]: ...


__all__ = [
    "ANALYZER_VERSION",
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "PROFILES",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "AnalyzeBrollCommand",
    "AnalyzeBrollError",
    "AssetVia",
    "Association",
    "AudioBehavior",
    "BrollAnalysis",
    "BrollAnalyzer",
    "BrollCatalog",
    "BrollConfig",
    "BrollIntelligenceError",
    "BrollProfile",
    "BrollResult",
    "Candidate",
    "ClockBasis",
    "Considered",
    "CoverageGap",
    "EvidenceRef",
    "EvidenceRole",
    "GapKind",
    "Identity",
    "InputHow",
    "InputRecord",
    "InvalidBrollAnalysis",
    "InvalidBrollConfig",
    "InvalidBrollDocument",
    "Issue",
    "LibraryScope",
    "MissingTargetEvidence",
    "NeedKind",
    "NoChange",
    "NothingToCover",
    "PoolRecord",
    "Precision",
    "Priority",
    "Provenance",
    "Purpose",
    "Recommendation",
    "ResultState",
    "Score",
    "ScoreBasis",
    "ScoreName",
    "Searched",
    "Severity",
    "SourceInterval",
    "StageReport",
    "StageState",
    "StoryRef",
    "SuggestedPlacement",
    "Support",
    "TargetOrigin",
    "UseKind",
    "analysis_from_json",
    "analysis_to_json",
]
