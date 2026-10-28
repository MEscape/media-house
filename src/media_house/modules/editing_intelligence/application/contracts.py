"""The Editing Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``editing_intelligence``.

``EditPlanner.execute`` turns a validated story plan and the stored analyses of its recordings
(content, audio, video, alignment, inspection) into an ``EditPlan``: what to keep, cut, shorten,
reorder and cover, as exact source ranges placed on a backend-neutral timeline. It is blocking
(with ``Acquire.REQUEST`` it may ask the story planner to plan): run it as a job. It never edits
media, never builds a timeline for a tool and never names one: a later stage compiles the plan.

    result = planner.execute(PlanEditCommand((asset_id,), EditConfig(profile="talking_head")), ctx)
    if isinstance(result, Ok):
        plan = result.value.plan
        for clip in plan.track(TrackKind.VIDEO):            # the timeline, main layer
            clip.source, clip.timeline_start, clip.timeline_duration
        for decision in plan.decisions_for(Action.CUT):      # what was removed, and why
            decision.rationale, decision.signals, decision.executability
        plan.issues, plan.checks        # what to look at, what was not checkable

Stored plans are read without recomputation through ``EditCatalog``; ``created`` is ``False`` when
the identical plan already existed. A person's decisions go in ``PlanEditCommand.overrides`` and are
carried through every later replan.
"""

from typing import Protocol

from media_house.modules.editing_intelligence.application.plan_edit import (
    Acquire,
    Advisors,
    ContinuityAdvisor,
    CoverageAdvisor,
    EditResult,
    PacingAdvisor,
    PlanEditCommand,
    PlanEditError,
)
from media_house.modules.editing_intelligence.domain.errors import (
    EditingIntelligenceError,
    InvalidEditConfig,
    InvalidEditDocument,
    InvalidEditPlan,
    InvalidStoryInput,
    MissingStoryPlan,
    NothingToEdit,
)
from media_house.modules.editing_intelligence.domain.facts import ShotChoice
from media_house.modules.editing_intelligence.domain.hooks import (
    ContinuityVerdict,
    CoverageQuery,
    CutQuery,
    PaceAdvice,
    SectionPace,
)
from media_house.modules.editing_intelligence.domain.model import (
    AudioBridge,
    Check,
    Confidence,
    Constraint,
    CoverageRequest,
    CoverCandidate,
    CutAlternative,
    CutPoint,
    Decision,
    EditDuration,
    EditPlan,
    EffectRequest,
    EvidenceRef,
    InputRecord,
    Issue,
    ManualOverride,
    PacingNote,
    Placement,
    Provenance,
    Signal,
    SourceRange,
    SourceRecord,
    StageReport,
    StoryRef,
    StructuralProposal,
    TargetRecord,
    Timebase,
    Trim,
)
from media_house.modules.editing_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    EditConfig,
    EditingProfile,
    TargetDuration,
)
from media_house.modules.editing_intelligence.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.editing_intelligence.domain.values import (
    DOCUMENT_TYPE,
    EDITOR_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Action,
    AudioBehavior,
    BridgeKind,
    CheckState,
    ConstraintKind,
    ConstraintState,
    DurationMode,
    EffectIntent,
    EffectKind,
    EvidenceKind,
    Executability,
    InputHow,
    IssueKind,
    Origin,
    PlacementRole,
    PlanState,
    Precision,
    Severity,
    SignalBasis,
    SignalName,
    StageState,
    StructuralKind,
    TrackKind,
    TrimKind,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class EditPlanner(Protocol):
    """Plan the edit of a story's recordings."""

    def execute(
        self, command: PlanEditCommand, ctx: JobContext
    ) -> Result[EditResult, PlanEditError]: ...


class EditCatalog(Protocol):
    """Read stored plans. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        profile: str | None = None,
    ) -> Result[tuple[EditPlan, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored plan of the asset, newest first (earlier plans are never changed)."""
        ...

    def load(self, plan_asset_id: str) -> Result[EditPlan, MediaError | InvalidEditDocument]: ...


__all__ = [
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "EDITOR_VERSION",
    "PROFILES",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "Acquire",
    "Action",
    "Advisors",
    "AudioBehavior",
    "AudioBridge",
    "BridgeKind",
    "Check",
    "CheckState",
    "Confidence",
    "Constraint",
    "ConstraintKind",
    "ConstraintState",
    "ContinuityAdvisor",
    "ContinuityVerdict",
    "CoverCandidate",
    "CoverageAdvisor",
    "CoverageQuery",
    "CoverageRequest",
    "CutAlternative",
    "CutPoint",
    "CutQuery",
    "Decision",
    "DurationMode",
    "EditCatalog",
    "EditConfig",
    "EditDuration",
    "EditPlan",
    "EditPlanner",
    "EditResult",
    "EditingIntelligenceError",
    "EditingProfile",
    "EffectIntent",
    "EffectKind",
    "EffectRequest",
    "EvidenceKind",
    "EvidenceRef",
    "Executability",
    "InputHow",
    "InputRecord",
    "InvalidEditConfig",
    "InvalidEditDocument",
    "InvalidEditPlan",
    "InvalidStoryInput",
    "Issue",
    "IssueKind",
    "ManualOverride",
    "MissingStoryPlan",
    "NothingToEdit",
    "Origin",
    "PaceAdvice",
    "PacingAdvisor",
    "PacingNote",
    "Placement",
    "PlacementRole",
    "PlanEditCommand",
    "PlanEditError",
    "PlanState",
    "Precision",
    "Provenance",
    "SectionPace",
    "Severity",
    "ShotChoice",
    "Signal",
    "SignalBasis",
    "SignalName",
    "SourceRange",
    "SourceRecord",
    "StageReport",
    "StageState",
    "StoryRef",
    "StructuralKind",
    "StructuralProposal",
    "TargetDuration",
    "TargetRecord",
    "Timebase",
    "TrackKind",
    "Trim",
    "TrimKind",
    "plan_from_json",
    "plan_to_json",
]
