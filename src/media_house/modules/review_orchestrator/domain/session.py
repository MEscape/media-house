"""The review session: one reviewable state of one project, with everything decided about it.

A session is an immutable value; every operation in ``workflow.py`` returns the next session and
the audit entries that explain the step. It never holds a timeline, only the exact version
(``TimelineRef``) every decision is bound to.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.modules.review_orchestrator.domain.permissions import PermissionPolicy
from media_house.modules.review_orchestrator.domain.timeline import TimelineFacts, TimelineRef
from media_house.modules.review_orchestrator.domain.values import (
    SCHEMA_VERSION,
    ActorKind,
    ApprovalKind,
    ExportStatus,
    ReviewState,
    SectionStatus,
    Verdict,
)


@dataclass(frozen=True, slots=True)
class FeedbackItem:
    """What a person said, kept as they said it. Interpreting it belongs to ``revision_engine``."""

    feedback_id: str
    text: str
    author: str
    received_at: datetime
    section_id: str | None = None
    #: The timeline version the person was looking at.
    timeline_version: int = 0
    #: The revision plan that took it up (empty while it is pending).
    handled_by: str = ""


@dataclass(frozen=True, slots=True)
class SectionRecord:
    section_id: str
    name: str
    status: SectionStatus
    #: The fingerprint of the section when it was approved or locked (empty before).
    digest: str = ""
    decided_by: str = ""
    decided_at: datetime | None = None
    #: The content changed after it was locked (the lock holds; automation stays out).
    drift: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class Approval:
    """One decision, bound to the exact version it was made on."""

    approval_id: str
    kind: ApprovalKind
    #: What was decided about: a revision plan, ``plan:proposal``, a section, an issue, ``final``...
    subject_id: str
    verdict: Verdict
    actor: str
    actor_kind: ActorKind
    timeline: TimelineRef
    decided_at: datetime
    review_version: int
    #: Sections it covers (a section approval, a revision's affected sections).
    sections: tuple[str, ...] = ()
    note: str = ""
    #: What the person changed in the proposal before approving it.
    modification: str = ""
    #: A person decided it in the moment (as opposed to a grant the project made in advance).
    manual: bool = True
    valid: bool = True
    invalidated_at: datetime | None = None
    invalidated_reason: str = ""
    #: The version a revision produced when it was carried out (the approval is then used up).
    consumed_by_version: int | None = None
    #: The fingerprint of the section when approved (a section approval only).
    digest: str = ""


@dataclass(frozen=True, slots=True)
class ExportAuthorization:
    """The one thing that lets the final export start, for exactly this video and this output."""

    authorization_id: str
    timeline: TimelineRef
    review_version: int
    final_approval_id: str
    quality_report_id: str
    polish_report_id: str
    export_profile_id: str
    #: Hash of the output configuration the person approved (codec, size, destination...).
    output_config_hash: str
    authorized_by: str
    authorized_at: datetime
    valid: bool = True
    invalidated_reason: str = ""
    #: The export that used it; one authorization starts one export at a time.
    used_by_export: str = ""


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    """A known, consistent state to come back to. Nothing in it is copied: every reference names
    an immutable stored document."""

    checkpoint_id: str
    label: str
    timeline: TimelineRef
    created_at: datetime
    review_version: int
    reason: str = ""
    #: The audio mix configuration in effect (a hash of the overrides) and its stored result.
    mix_result_asset_id: str = ""
    mix_overrides_hash: str = ""
    resolve_binding_asset_id: str = ""
    quality_report_id: str = ""
    locked_sections: tuple[str, ...] = ()
    approved_sections: tuple[str, ...] = ()
    #: The stored build was read back and its hash matched when the checkpoint was made.
    verified: bool = False


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One line of the history. ``sequence`` is assigned when the entry is stored."""

    kind: str
    at: datetime
    actor: str
    actor_kind: ActorKind
    summary: str
    timeline_version: int
    subject: str = ""
    sequence: int = 0
    data: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewSession:
    session_id: str
    project_id: str | None
    anchor_asset_id: str
    timeline: TimelineRef
    facts: TimelineFacts
    state: ReviewState
    review_version: int
    created_at: datetime
    updated_at: datetime
    reviewer: str
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    resolve_project_ref: str = ""
    resolve_timeline_ref: str = ""
    quality_report_id: str = ""
    polish_report_id: str = ""
    revision_plan_ids: tuple[str, ...] = ()
    render_report_ids: tuple[str, ...] = ()
    export_validation_id: str = ""
    sections: tuple[SectionRecord, ...] = ()
    approvals: tuple[Approval, ...] = ()
    feedback: tuple[FeedbackItem, ...] = ()
    authorizations: tuple[ExportAuthorization, ...] = ()
    checkpoints: tuple[CheckpointRecord, ...] = ()
    #: Earlier versions this session has been bound to, oldest first (the current one is last).
    timeline_history: tuple[TimelineRef, ...] = ()
    final_approval_id: str = ""
    export_authorization_id: str = ""
    export_id: str = ""
    export_status: ExportStatus = ExportStatus.NONE
    schema_version: int = SCHEMA_VERSION

    # --- lookups ---
    def section(self, section_id: str) -> SectionRecord | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def locked_sections(self) -> tuple[str, ...]:
        return tuple(s.section_id for s in self.sections if s.status is SectionStatus.LOCKED)

    def approved_sections(self) -> tuple[str, ...]:
        return tuple(
            s.section_id
            for s in self.sections
            if s.status in (SectionStatus.APPROVED, SectionStatus.LOCKED)
        )

    def pending_feedback(self) -> tuple[FeedbackItem, ...]:
        return tuple(f for f in self.feedback if not f.handled_by)

    def approval(self, approval_id: str) -> Approval | None:
        return next((a for a in self.approvals if a.approval_id == approval_id), None)

    def authorization(self, authorization_id: str) -> ExportAuthorization | None:
        return next(
            (a for a in self.authorizations if a.authorization_id == authorization_id), None
        )

    def checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        return next((c for c in self.checkpoints if c.checkpoint_id == checkpoint_id), None)

    def decisions_on(
        self, subject_id: str, kind: ApprovalKind | None = None
    ) -> tuple[Approval, ...]:
        return tuple(
            a
            for a in self.approvals
            if a.subject_id == subject_id and (kind is None or a.kind is kind)
        )

    def is_current(self, approval: Approval) -> bool:
        """Whether a decision still applies to the video as it is now."""
        if not approval.valid:
            return False
        if approval.kind is ApprovalKind.SECTION:
            record = self.section(approval.subject_id)
            return record is not None and record.digest == approval.digest
        return approval.timeline.same_content(self.timeline)

    def current_approval(self, subject_id: str, kind: ApprovalKind) -> Approval | None:
        """The newest decision on a subject of that kind that still applies."""
        for approval in reversed(self.decisions_on(subject_id, kind)):
            if self.is_current(approval):
                return approval
        return None

    def rejected_subjects(self) -> frozenset[str]:
        """Subjects a person rejected (and did not later approve): not to be proposed again."""
        verdict: dict[str, Verdict] = {}
        for approval in self.approvals:
            if approval.kind in (ApprovalKind.PROPOSAL, ApprovalKind.REVISION):
                verdict[approval.subject_id] = approval.verdict
        return frozenset(s for s, v in verdict.items() if v is Verdict.REJECTED)
