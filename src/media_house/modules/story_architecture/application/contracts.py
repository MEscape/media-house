"""The Story Architecture PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``story_architecture``.

``StoryPlanner.execute`` turns the stored content analysis of one or more recordings (and their
audio-visual alignment, when there is one) into a ``StoryPlan``: the best coherent story the
material supports, as a blueprint for editing. It is blocking (with ``Acquire.REQUEST`` or
``llm=True`` it may call other modules or a model): run it as a job. The plan recommends an order,
an opening, priorities and where visuals help, every one traced to the passages it rests on. It
never cuts, trims or times anything: those decisions belong to the editing stage, which reads the
plan instead of repeating the reasoning.

    result = planner.execute(PlanStoryCommand((asset_id,), StoryConfig("review")), ctx)
    if isinstance(result, Ok):
        plan = result.value.plan
        for section in plan.ordered_sections():            # the recommended order
            beats = plan.beats_of(section.section_id)       # what each passage does
        opening = plan.opening(plan.recommended_opening)

Stored plans are read without recomputation through ``StoryCatalog``; ``created`` is ``False``
when the identical plan already existed.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.story_architecture.application.plan_story import (
    Acquire,
    PlanStoryCommand,
    PlanStoryError,
    StoryResult,
)
from media_house.modules.story_architecture.domain.errors import (
    InvalidStoryConfig,
    InvalidStoryDocument,
    InvalidStoryPlan,
    MissingAnalysis,
    NothingToPlan,
    StoryArchitectureError,
)
from media_house.modules.story_architecture.domain.model import (
    AlternativePlan,
    AssetRecord,
    AssetRelation,
    AudienceIntent,
    Beat,
    CompletenessIssue,
    Confidence,
    ContextDependency,
    Criterion,
    DurationEstimate,
    EvidenceLink,
    InputRecord,
    KeyInformation,
    NarrativePattern,
    OpeningCandidate,
    OrderChange,
    OrderingConstraint,
    PacingNote,
    PacingProfile,
    PlanDuration,
    Provenance,
    Section,
    SetupPayoffLink,
    SourceRef,
    StageReport,
    Statement,
    StoryPlan,
    VisualOpportunity,
    VisualRef,
)
from media_house.modules.story_architecture.domain.profile import (
    DEFAULT_FORMAT,
    FORMATS,
    FormatProfile,
    ReorderPolicy,
    StoryConfig,
    Thresholds,
)
from media_house.modules.story_architecture.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.story_architecture.domain.validation import validate_plan
from media_house.modules.story_architecture.domain.values import (
    PLANNER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Basis,
    BeatFunction,
    Certainty,
    ClaimKind,
    ConstraintKind,
    ContentMode,
    DurationBasis,
    IssueKind,
    LinkKind,
    LinkState,
    Method,
    Necessity,
    OpeningStrategy,
    Origin,
    PacingAction,
    PacingKind,
    Priority,
    ResultState,
    Severity,
    StageState,
    Strength,
    SupportRelation,
    Treatment,
    VisualPurpose,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class StoryPlanner(Protocol):
    """Plan the story of one or more analysed recordings."""

    def execute(
        self, command: PlanStoryCommand, ctx: JobContext
    ) -> Result[StoryResult, PlanStoryError]: ...


class StoryCatalog(Protocol):
    """Read stored plans. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        format: str | None = None,  # noqa: A002
    ) -> Result[tuple[StoryPlan, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored plan of the asset, newest first (earlier plans are never changed)."""
        ...

    def load(self, plan_asset_id: str) -> Result[StoryPlan, MediaError | InvalidStoryDocument]: ...


__all__ = [
    "DEFAULT_FORMAT",
    "FORMATS",
    "PLANNER_VERSION",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "Acquire",
    "AlternativePlan",
    "AssetRecord",
    "AssetRelation",
    "AudienceIntent",
    "Basis",
    "Beat",
    "BeatFunction",
    "Certainty",
    "ClaimKind",
    "CompletenessIssue",
    "Confidence",
    "ConstraintKind",
    "ContentMode",
    "ContextDependency",
    "Criterion",
    "DurationBasis",
    "DurationEstimate",
    "EvidenceLink",
    "FormatProfile",
    "InputRecord",
    "InvalidStoryConfig",
    "InvalidStoryDocument",
    "InvalidStoryPlan",
    "IssueKind",
    "KeyInformation",
    "LinkKind",
    "LinkState",
    "Method",
    "MissingAnalysis",
    "NarrativePattern",
    "Necessity",
    "NothingToPlan",
    "OpeningCandidate",
    "OpeningStrategy",
    "OrderChange",
    "OrderingConstraint",
    "Origin",
    "PacingAction",
    "PacingKind",
    "PacingNote",
    "PacingProfile",
    "PlanDuration",
    "PlanStoryCommand",
    "PlanStoryError",
    "Priority",
    "Provenance",
    "ReorderPolicy",
    "ResultState",
    "Section",
    "SetupPayoffLink",
    "Severity",
    "SourceRef",
    "StageReport",
    "StageState",
    "Statement",
    "StoryArchitectureError",
    "StoryCatalog",
    "StoryConfig",
    "StoryPlan",
    "StoryPlanner",
    "StoryResult",
    "Strength",
    "SupportRelation",
    "Thresholds",
    "Treatment",
    "VisualOpportunity",
    "VisualPurpose",
    "VisualRef",
    "plan_from_json",
    "plan_to_json",
    "validate_plan",
]
