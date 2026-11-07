"""The Review Orchestrator PUBLIC API for other modules (import this file only).

``review_orchestrator`` is the one place where a review is kept: its state, the exact version of
the timeline each report and decision belongs to, approvals, locked sections, checkpoints, the
authorization of the final export and the history of all of it. ``quality_control``,
``revision_engine``, ``final_polish``, ``render_manager``, ``export_validation`` and
``resolve_export`` report to it and ask it; none of them keeps a second copy of any of this.

    opened = sessions.open(OpenSessionCommand(anchor_asset_id=..., reviewer="Ada"))
    sessions.attach(session_id, ArtifactKind.QUALITY_REPORT, report_id, timeline=ref,
                    actor=Actor.system("quality_control"))
    sessions.decide(DecideCommand(session_id, ApprovalKind.FINAL, "final", Verdict.APPROVED,
                                  Actor.human("Ada"), ref))
    sessions.verify_export(session_id, authorization_id, timeline=ref,
                           export_profile_id=..., output_config_hash=...)

A person's decision is only ever recorded for the version they looked at; ``Err`` carries the
reason a step was refused. Blocking: the store is a local database; run as jobs.
"""

from typing import Protocol

from media_house.modules.review_orchestrator.application.actors import Actor
from media_house.modules.review_orchestrator.application.events import (
    ApprovalInvalidated,
    DecisionRecorded,
    ExportAuthorized,
    ReviewEvent,
    ReviewStateChanged,
    TimelineVersionBound,
)
from media_house.modules.review_orchestrator.application.orchestrator import (
    AuthorizeExportCommand,
    CheckpointCommand,
    DecideCommand,
    OpenResult,
    OpenSessionCommand,
    ReviewFailure,
)
from media_house.modules.review_orchestrator.domain.errors import (
    InvalidReviewDocument,
    InvalidTransition,
    LockedSection,
    NotAuthorized,
    NotPermitted,
    ReviewError,
    StaleTimeline,
    UnknownSubject,
)
from media_house.modules.review_orchestrator.domain.permissions import (
    Authority,
    Operation,
    PermissionPolicy,
)
from media_house.modules.review_orchestrator.domain.session import (
    Approval,
    AuditEntry,
    CheckpointRecord,
    ExportAuthorization,
    FeedbackItem,
    ReviewSession,
    SectionRecord,
)
from media_house.modules.review_orchestrator.domain.timeline import (
    SectionFacts,
    TimelineFacts,
    TimelineRef,
)
from media_house.modules.review_orchestrator.domain.values import (
    ORCHESTRATOR_VERSION,
    SCHEMA_VERSION,
    TERMINAL_STATES,
    ActorKind,
    ApprovalKind,
    ArtifactKind,
    ExportStatus,
    ReviewState,
    SectionStatus,
    ValidationStatus,
    Verdict,
)
from media_house.shared.errors import Result


class ReviewSessions(Protocol):
    """Everything that changes a review."""

    def open(self, command: OpenSessionCommand) -> Result[OpenResult, ReviewFailure]: ...

    def sync_timeline(self, session_id: str) -> Result[ReviewSession, ReviewFailure]: ...

    def advance(
        self,
        session_id: str,
        to: ReviewState,
        *,
        actor: Actor,
        reason: str = "",
        timeline: TimelineRef | None = None,
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def cancel(
        self, session_id: str, *, actor: Actor, reason: str
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def attach(
        self,
        session_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        *,
        timeline: TimelineRef,
        actor: Actor,
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def add_feedback(
        self, session_id: str, text: str, *, author: str, section_id: str | None = None
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def feedback_handled(
        self, session_id: str, feedback_ids: tuple[str, ...], plan_id: str
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def decide(self, command: DecideCommand) -> Result[ReviewSession, ReviewFailure]: ...

    def lock_section(
        self,
        session_id: str,
        section_id: str,
        *,
        timeline: TimelineRef,
        actor: Actor,
        note: str = "",
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def unlock_section(
        self, session_id: str, section_id: str, *, actor: Actor, reason: str
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def mark_under_review(
        self, session_id: str, section_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def ensure_automation_allowed(
        self, session_id: str, section_ids: tuple[str, ...], what: str
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def begin_revision(
        self, session_id: str, plan_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def revision_failed(
        self, session_id: str, plan_id: str, reason: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def record_revision_result(
        self, session_id: str, plan_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def create_checkpoint(
        self, command: CheckpointCommand, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def authorize_export(
        self, command: AuthorizeExportCommand
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def verify_export(
        self,
        session_id: str,
        authorization_id: str,
        *,
        timeline: TimelineRef,
        export_profile_id: str,
        output_config_hash: str,
    ) -> Result[ExportAuthorization, ReviewFailure]: ...

    def begin_export(
        self, session_id: str, authorization_id: str, export_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def export_rendered(
        self, session_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def export_failed(
        self, session_id: str, reason: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def record_export_validation(
        self, session_id: str, status: ValidationStatus, validation_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def resolve_manual_validation(
        self, session_id: str, *, accept: bool, actor: Actor, note: str
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def retry_export(
        self, session_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...

    def mark_delivery_ready(
        self, session_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]: ...


class ReviewCatalog(Protocol):
    """Reads of a review; nothing changes."""

    def get(self, session_id: str) -> Result[ReviewSession, ReviewFailure]: ...

    def find(
        self, anchor_asset_id: str, *, timeline_id: str | None = None, active_only: bool = True
    ) -> list[ReviewSession]: ...

    def history(self, session_id: str, *, after: int = 0, limit: int = 500) -> list[AuditEntry]: ...


__all__ = [
    "ORCHESTRATOR_VERSION",
    "SCHEMA_VERSION",
    "TERMINAL_STATES",
    "Actor",
    "ActorKind",
    "Approval",
    "ApprovalInvalidated",
    "ApprovalKind",
    "ArtifactKind",
    "AuditEntry",
    "Authority",
    "AuthorizeExportCommand",
    "CheckpointCommand",
    "CheckpointRecord",
    "DecideCommand",
    "DecisionRecorded",
    "ExportAuthorization",
    "ExportAuthorized",
    "ExportStatus",
    "FeedbackItem",
    "InvalidReviewDocument",
    "InvalidTransition",
    "LockedSection",
    "NotAuthorized",
    "NotPermitted",
    "OpenResult",
    "OpenSessionCommand",
    "Operation",
    "PermissionPolicy",
    "ReviewCatalog",
    "ReviewError",
    "ReviewEvent",
    "ReviewFailure",
    "ReviewSession",
    "ReviewSessions",
    "ReviewState",
    "ReviewStateChanged",
    "SectionFacts",
    "SectionRecord",
    "SectionStatus",
    "StaleTimeline",
    "TimelineFacts",
    "TimelineRef",
    "TimelineVersionBound",
    "UnknownSubject",
    "ValidationStatus",
    "Verdict",
]
