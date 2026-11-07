"""The Revision Engine PUBLIC API for other modules (import this file only).

``revision_engine`` is the feedback loop of Media House. It turns the findings of a quality report
and a person's own words into proposals, each with its owner, the level of approval it needs, what
it touches and what it conflicts with; it carries out only what a person approved, through the
owner's own contract, after a checkpoint, and checks the result on the next report. It decides
nothing creative and never overwrites a person's work, a locked section or a decision.

    planned = planner.execute(
        PlanRevisionCommand(review_session_id=..., feedback=("Make the music quieter.",)), ctx
    )
    decided = decider.execute(
        DecideRevisionCommand(
            session, plan_id, Actor.human("Ada"), ref, (ProposalDecision("p1", Verdict.APPROVED),)
        )
    )
    done = executor.execute(ExecuteRevisionCommand(session, plan_id, Actor.human("Ada")), ctx)
    done.value.plan.validation_results        # what the revision really did
    RollbackPlanner.execute(PlanRollbackCommand(...))   # a checkpoint is restored as a revision

Blocking: run as jobs.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.revision_engine.application.decide_revision import (
    DecideRevisionCommand,
    DecisionResult,
    ProposalDecision,
)
from media_house.modules.revision_engine.application.events import (
    RevisionEvent,
    RevisionExecuted,
    RevisionHandedOff,
    RevisionPlanCreated,
)
from media_house.modules.revision_engine.application.execute_revision import (
    ExecuteRevisionCommand,
    ExecutionResult,
)
from media_house.modules.revision_engine.application.executors import (
    ChangeOutcome,
    ExecutionContext,
    RevisionExecutor,
)
from media_house.modules.revision_engine.application.plan_revisions import (
    PlanResult,
    PlanRevisionCommand,
    Question,
    RevisionFailure,
)
from media_house.modules.revision_engine.application.ports import (
    ManualChanges,
    ManualChangeSync,
    NoPreview,
    PreviewProvider,
)
from media_house.modules.revision_engine.application.rollback import PlanRollbackCommand
from media_house.modules.revision_engine.application.views import EDITOR_PREFIX
from media_house.modules.revision_engine.domain.errors import (
    ConflictWithPersonsWork,
    InvalidRevisionDocument,
    NeedsClarification,
    NothingToRevise,
    PlanNotCurrent,
    RevisionError,
)
from media_house.modules.revision_engine.domain.feedback import Context
from media_house.modules.revision_engine.domain.model import (
    Clarification,
    ExecutionRecord,
    ImprovementCheck,
    Instruction,
    ProposedChange,
    RevisionPlan,
    Skipped,
    Target,
    ValidationResult,
)
from media_house.modules.revision_engine.domain.values import (
    DOCUMENT_TYPE,
    INTENTS,
    RESULT_OPERATION,
    REVISION_VERSION,
    SCHEMA_VERSION,
    ChangeKind,
    ChangeStatus,
    InstructionKind,
    Level,
    Origin,
    PlanStatus,
    TargetKind,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class RevisionPlanner(Protocol):
    def execute(
        self, command: PlanRevisionCommand, ctx: JobContext
    ) -> Result[PlanResult, RevisionFailure]:
        """Plan the changes the latest quality report and the person's feedback ask for."""
        ...


class RevisionDecider(Protocol):
    def execute(self, command: DecideRevisionCommand) -> Result[DecisionResult, RevisionFailure]:
        """Record a person's decision on a plan (nothing is carried out)."""
        ...


class RevisionExecutorService(Protocol):
    def execute(
        self, command: ExecuteRevisionCommand, ctx: JobContext
    ) -> Result[ExecutionResult, RevisionFailure]:
        """Carry out an approved plan after a checkpoint, then check the result."""
        ...


class RollbackPlanner(Protocol):
    def execute(
        self, command: PlanRollbackCommand
    ) -> Result[tuple[RevisionPlan, MediaAssetDto], RevisionFailure]:
        """Plan the restoration of a checkpoint (it still needs a person's approval)."""
        ...


class RevisionCatalog(Protocol):
    """Stored plans; nothing is planned or executed."""

    def plans(
        self, anchor_asset_id: str, *, session_id: str | None = None
    ) -> Result[list[tuple[RevisionPlan, MediaAssetDto]], MediaError]: ...

    def find(
        self, anchor_asset_id: str, plan_id: str, *, session_id: str | None = None
    ) -> Result[tuple[RevisionPlan, MediaAssetDto] | None, MediaError]: ...


__all__ = [
    "DOCUMENT_TYPE",
    "EDITOR_PREFIX",
    "INTENTS",
    "RESULT_OPERATION",
    "REVISION_VERSION",
    "SCHEMA_VERSION",
    "ChangeKind",
    "ChangeOutcome",
    "ChangeStatus",
    "Clarification",
    "ConflictWithPersonsWork",
    "Context",
    "DecideRevisionCommand",
    "DecisionResult",
    "ExecuteRevisionCommand",
    "ExecutionContext",
    "ExecutionRecord",
    "ExecutionResult",
    "ImprovementCheck",
    "Instruction",
    "InstructionKind",
    "InvalidRevisionDocument",
    "Level",
    "ManualChangeSync",
    "ManualChanges",
    "NeedsClarification",
    "NoPreview",
    "NothingToRevise",
    "Origin",
    "PlanNotCurrent",
    "PlanResult",
    "PlanRevisionCommand",
    "PlanRollbackCommand",
    "PlanStatus",
    "PreviewProvider",
    "ProposalDecision",
    "ProposedChange",
    "Question",
    "RevisionCatalog",
    "RevisionDecider",
    "RevisionError",
    "RevisionEvent",
    "RevisionExecuted",
    "RevisionExecutor",
    "RevisionExecutorService",
    "RevisionFailure",
    "RevisionHandedOff",
    "RevisionPlan",
    "RevisionPlanCreated",
    "RevisionPlanner",
    "RollbackPlanner",
    "Skipped",
    "Target",
    "TargetKind",
    "ValidationResult",
]
