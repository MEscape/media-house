"""The rules of a review: which state follows which, who may decide, what a decision is bound to.

Every function takes a session and returns an ``Update`` (the next session and the audit entries
that explain the step); nothing is mutated and nothing reads a clock. A refused step raises a
``ReviewError`` and leaves the session as it was.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from media_house.modules.review_orchestrator.domain.errors import (
    InvalidTransition,
    LockedSection,
    NotAuthorized,
    NotPermitted,
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
    ChangeAssessment,
    TimelineFacts,
    TimelineRef,
    assess_change,
)
from media_house.modules.review_orchestrator.domain.values import (
    FINAL_STAGE_STATES,
    REVISION_STAGE_STATES,
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
from media_house.shared.types import new_id

S = ReviewState

#: The only moves between states. Anything else is refused.
ALLOWED: dict[ReviewState, frozenset[ReviewState]] = {
    S.CREATED: frozenset({S.ANALYSIS_PENDING, S.CANCELLED}),
    S.ANALYSIS_PENDING: frozenset({S.ANALYSIS_COMPLETE, S.CREATED, S.CANCELLED}),
    S.ANALYSIS_COMPLETE: frozenset(
        {
            S.REVIEW_READY,
            S.REVISION_PROPOSED,
            S.ANALYSIS_PENDING,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.REVIEW_READY: frozenset(
        {
            S.AWAITING_HUMAN_REVIEW,
            S.ANALYSIS_PENDING,
            S.FEEDBACK_RECEIVED,
            S.REVISION_PROPOSED,
            S.FINAL_POLISH_PENDING,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.AWAITING_HUMAN_REVIEW: frozenset(
        {
            S.FEEDBACK_RECEIVED,
            S.REVISION_PROPOSED,
            S.FINAL_POLISH_PENDING,
            S.ANALYSIS_PENDING,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.FEEDBACK_RECEIVED: frozenset(
        {S.REVISION_PROPOSED, S.AWAITING_HUMAN_REVIEW, S.REVIEW_AGAIN_REQUIRED, S.CANCELLED}
    ),
    S.REVISION_PROPOSED: frozenset(
        {S.AWAITING_REVISION_APPROVAL, S.REVIEW_AGAIN_REQUIRED, S.CANCELLED}
    ),
    S.AWAITING_REVISION_APPROVAL: frozenset(
        {
            S.REVISION_APPROVED,
            S.REVISION_REJECTED,
            S.REVISION_PROPOSED,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.REVISION_APPROVED: frozenset({S.REVISION_IN_PROGRESS, S.REVIEW_AGAIN_REQUIRED, S.CANCELLED}),
    S.REVISION_REJECTED: frozenset(
        {
            S.AWAITING_HUMAN_REVIEW,
            S.REVISION_PROPOSED,
            S.REVIEW_READY,
            S.FINAL_POLISH_PENDING,
            S.CANCELLED,
        }
    ),
    S.REVISION_IN_PROGRESS: frozenset(
        {S.REVISION_COMPLETE, S.REVISION_APPROVED, S.AWAITING_HUMAN_REVIEW, S.CANCELLED}
    ),
    S.REVISION_COMPLETE: frozenset({S.REVALIDATION_PENDING, S.CANCELLED}),
    S.REVALIDATION_PENDING: frozenset(
        {S.REVIEW_AGAIN_REQUIRED, S.REVIEW_READY, S.REVISION_COMPLETE, S.CANCELLED}
    ),
    S.REVIEW_AGAIN_REQUIRED: frozenset(
        {S.AWAITING_HUMAN_REVIEW, S.REVISION_PROPOSED, S.ANALYSIS_PENDING, S.CANCELLED}
    ),
    S.FINAL_POLISH_PENDING: frozenset(
        {
            S.FINAL_REVIEW_READY,
            S.REVIEW_READY,
            S.REVISION_PROPOSED,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.FINAL_REVIEW_READY: frozenset(
        {
            S.AWAITING_FINAL_APPROVAL,
            S.FINAL_POLISH_PENDING,
            S.REVISION_PROPOSED,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.AWAITING_FINAL_APPROVAL: frozenset(
        {
            S.FINAL_APPROVED,
            S.AWAITING_HUMAN_REVIEW,
            S.REVISION_PROPOSED,
            S.FINAL_POLISH_PENDING,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.FINAL_APPROVED: frozenset(
        {
            S.EXPORT_AUTHORIZED,
            S.AWAITING_HUMAN_REVIEW,
            S.FINAL_POLISH_PENDING,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.EXPORT_AUTHORIZED: frozenset(
        {S.EXPORT_IN_PROGRESS, S.AWAITING_HUMAN_REVIEW, S.REVIEW_AGAIN_REQUIRED, S.CANCELLED}
    ),
    S.EXPORT_IN_PROGRESS: frozenset({S.EXPORT_VALIDATION_PENDING, S.EXPORT_FAILED}),
    S.EXPORT_VALIDATION_PENDING: frozenset({S.EXPORT_VALIDATED, S.EXPORT_FAILED}),
    S.EXPORT_VALIDATED: frozenset({S.DELIVERY_READY, S.AWAITING_HUMAN_REVIEW, S.CANCELLED}),
    S.EXPORT_FAILED: frozenset(
        {
            S.EXPORT_AUTHORIZED,
            S.REVISION_PROPOSED,
            S.AWAITING_HUMAN_REVIEW,
            S.FINAL_POLISH_PENDING,
            S.ANALYSIS_PENDING,
            S.REVIEW_AGAIN_REQUIRED,
            S.CANCELLED,
        }
    ),
    S.DELIVERY_READY: frozenset(),
    S.CANCELLED: frozenset(),
}

#: States only a specific operation may enter (a decision, an authorization, an export result).
GUARDED = frozenset(
    {
        S.REVISION_APPROVED,
        S.REVISION_REJECTED,
        S.FINAL_APPROVED,
        S.EXPORT_AUTHORIZED,
        S.EXPORT_IN_PROGRESS,
        S.EXPORT_VALIDATION_PENDING,
        S.EXPORT_VALIDATED,
        S.EXPORT_FAILED,
        S.DELIVERY_READY,
        S.REVISION_IN_PROGRESS,
        S.REVISION_COMPLETE,
    }
)
#: A change to the video makes the reports and proposals of these states describe another video.
_OUTDATED_BY_CHANGE = (
    frozenset(
        {S.ANALYSIS_COMPLETE, S.REVIEW_READY, S.AWAITING_HUMAN_REVIEW, S.FINAL_POLISH_PENDING}
    )
    | FINAL_STAGE_STATES
    | REVISION_STAGE_STATES
)


@dataclass(frozen=True, slots=True)
class Update:
    session: ReviewSession
    entries: tuple[AuditEntry, ...] = ()
    #: ``False`` when the step was already true (a repeated decision, the same version again).
    changed: bool = True


def _entry(
    session: ReviewSession,
    kind: str,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
    summary: str,
    *,
    subject: str = "",
    data: tuple[tuple[str, str], ...] = (),
) -> AuditEntry:
    return AuditEntry(
        kind=kind,
        at=now,
        actor=actor,
        actor_kind=actor_kind,
        summary=summary,
        timeline_version=session.timeline.version,
        subject=subject,
        data=data,
    )


def _touch(session: ReviewSession, now: datetime, **changes: Any) -> ReviewSession:
    return replace(session, updated_at=now, **changes)


def _same_version(session: ReviewSession, given: TimelineRef, what: str) -> None:
    if not session.timeline.same_content(given):
        raise StaleTimeline(session.timeline.version, given.version, what)


def _require(session: ReviewSession, *states: ReviewState, action: str) -> None:
    if session.state not in states:
        raise InvalidTransition(
            session.state.value,
            action,
            "it is allowed in: " + ", ".join(s.value for s in states),
        )


def _move(
    session: ReviewSession,
    to: ReviewState,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
    reason: str,
    **changes: Any,
) -> Update:
    if to not in ALLOWED[session.state]:
        raise InvalidTransition(session.state.value, to.value, reason)
    moved = _touch(session, now, state=to, **changes)
    entry = _entry(
        session,
        "state",
        now,
        actor,
        actor_kind,
        f"{session.state.value} -> {to.value}" + (f": {reason}" if reason else ""),
        data=(("from", session.state.value), ("to", to.value)),
    )
    return Update(moved, (entry,))


def _merge(first: Update, then: Update) -> Update:
    return Update(then.session, (*first.entries, *then.entries), first.changed or then.changed)


# --- opening ---------------------------------------------------------------------------------


def open_session(
    *,
    session_id: str,
    project_id: str | None,
    facts: TimelineFacts,
    reviewer: str,
    policy: PermissionPolicy,
    resolve_project_ref: str,
    resolve_timeline_ref: str,
    now: datetime,
) -> Update:
    sections = tuple(
        SectionRecord(section_id=s.section_id, name=s.name, status=SectionStatus.UNREVIEWED)
        for s in facts.sections
    )
    session = ReviewSession(
        session_id=session_id,
        project_id=project_id,
        anchor_asset_id=facts.ref.anchor_asset_id,
        timeline=facts.ref,
        facts=facts,
        state=ReviewState.CREATED,
        review_version=1,
        created_at=now,
        updated_at=now,
        reviewer=reviewer,
        policy=policy,
        resolve_project_ref=resolve_project_ref,
        resolve_timeline_ref=resolve_timeline_ref,
        sections=sections,
        timeline_history=(facts.ref,),
    )
    entry = _entry(
        session,
        "opened",
        now,
        reviewer,
        ActorKind.HUMAN,
        f"review opened on timeline version {facts.ref.version}",
        data=(("timeline_id", facts.ref.timeline_id), ("content_hash", facts.ref.content_hash)),
    )
    return Update(session, (entry,))


# --- the timeline moved ----------------------------------------------------------------------


def bind_timeline(
    session: ReviewSession,
    facts: TimelineFacts,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    """The timeline has another version. A rebuild with the same content only moves the reference;
    a change voids every decision that depended on what changed."""
    if session.state in TERMINAL_STATES:
        return Update(session, (), changed=False)
    if facts.ref == session.timeline:
        return Update(session, (), changed=False)
    if facts.ref.timeline_id != session.timeline.timeline_id:
        raise InvalidTransition(
            session.state.value, "bind", "the review belongs to another timeline"
        )
    change = assess_change(session.facts, facts)
    history = (*session.timeline_history, facts.ref)
    if not change.material:
        rebound = _touch(session, now, timeline=facts.ref, facts=facts, timeline_history=history)
        note = _entry(
            rebound,
            "timeline.rebound",
            now,
            actor,
            actor_kind,
            f"timeline version {facts.ref.version} has the same content as version "
            f"{session.timeline.version}; decisions stand",
        )
        return Update(rebound, (note,))
    return _apply_change(
        session, facts, change, history, now=now, actor=actor, actor_kind=actor_kind
    )


def _apply_change(
    session: ReviewSession,
    facts: TimelineFacts,
    change: ChangeAssessment,
    history: tuple[TimelineRef, ...],
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    entries: list[AuditEntry] = []
    sections, section_notes = _rebase_sections(session, facts, change)
    approvals: list[Approval] = []
    for approval in session.approvals:
        reason = _invalidation_reason(approval, change, session, facts)
        if reason and approval.valid:
            approval = replace(approval, valid=False, invalidated_at=now, invalidated_reason=reason)
            entries.append(
                _entry(
                    session,
                    "approval.invalidated",
                    now,
                    actor,
                    actor_kind,
                    f"{approval.kind.value} decision on {approval.subject_id} "
                    f"no longer applies: {reason}",
                    subject=approval.approval_id,
                )
            )
        approvals.append(approval)
    authorizations = tuple(
        replace(a, valid=False, invalidated_reason="the timeline changed after it was authorized")
        if a.valid
        else a
        for a in session.authorizations
    )
    for authorization in session.authorizations:
        if authorization.valid:
            entries.append(
                _entry(
                    session,
                    "authorization.invalidated",
                    now,
                    actor,
                    actor_kind,
                    "the export authorization no longer applies: the timeline changed",
                    subject=authorization.authorization_id,
                )
            )
    updated = _touch(
        session,
        now,
        timeline=facts.ref,
        facts=facts,
        timeline_history=history,
        sections=sections,
        approvals=tuple(approvals),
        authorizations=authorizations,
        final_approval_id="",
        export_authorization_id="",
        review_version=session.review_version + 1,
    )
    summary = f"timeline version {session.timeline.version} -> {facts.ref.version}: " + "; ".join(
        change.reasons()
    )
    entries.insert(0, _entry(updated, "timeline.changed", now, actor, actor_kind, summary))
    entries.extend(
        _entry(updated, "section.note", now, actor, actor_kind, n) for n in section_notes
    )
    result = Update(updated, tuple(entries))
    if session.state in _OUTDATED_BY_CHANGE:
        result = _merge(
            result,
            _move(
                result.session,
                ReviewState.REVIEW_AGAIN_REQUIRED,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason="the timeline changed; earlier reports and decisions describe another video",
                quality_report_id="",
                polish_report_id="",
                export_status=ExportStatus.NONE
                if session.state in (S.EXPORT_AUTHORIZED, S.EXPORT_FAILED)
                else session.export_status,
            ),
        )
    return result


def _rebase_sections(
    session: ReviewSession, facts: TimelineFacts, change: ChangeAssessment
) -> tuple[tuple[SectionRecord, ...], list[str]]:
    notes: list[str] = []
    records = {s.section_id: s for s in session.sections}
    result: list[SectionRecord] = []
    for section in facts.sections:
        record = records.get(section.section_id)
        if record is None:
            result.append(
                SectionRecord(
                    section_id=section.section_id,
                    name=section.name,
                    status=SectionStatus.UNREVIEWED,
                )
            )
            continue
        baseline = record.digest or ""
        moved = bool(baseline) and baseline != section.digest
        if record.status is SectionStatus.APPROVED and moved:
            notes.append(
                f"section {section.section_id} changed after it was approved: approval withdrawn"
            )
            record = replace(
                record, status=SectionStatus.UNREVIEWED, digest="", note="changed after approval"
            )
        elif record.status is SectionStatus.LOCKED:
            if moved and not record.drift:
                notes.append(
                    f"locked section {section.section_id} changed (not by automation); "
                    "the lock holds"
                )
            record = replace(record, drift=moved)
        result.append(replace(record, name=section.name))
    for gone in change.removed_sections:
        old = records.get(gone)
        if old is not None and old.status is SectionStatus.LOCKED:
            notes.append(f"locked section {gone} is gone from the timeline")
    return tuple(result), notes


def _invalidation_reason(
    approval: Approval, change: ChangeAssessment, session: ReviewSession, facts: TimelineFacts
) -> str:
    if approval.consumed_by_version is not None:
        return ""
    if approval.kind is ApprovalKind.SECTION:
        section = facts.section(approval.subject_id)
        if section is None:
            return "the section no longer exists"
        return "the section changed" if section.digest != approval.digest else ""
    if approval.timeline.same_content(facts.ref):
        return ""
    return "the timeline changed after it was decided"


# --- moves -----------------------------------------------------------------------------------


def advance(
    session: ReviewSession,
    to: ReviewState,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
    reason: str = "",
    timeline: TimelineRef | None = None,
) -> Update:
    """A plain move between states. States that stand for a decision are not reachable this way."""
    if timeline is not None:
        _same_version(session, timeline, "the move")
    if to in GUARDED:
        raise InvalidTransition(
            session.state.value, to.value, "this state is only reached by its own operation"
        )
    if to is ReviewState.FINAL_REVIEW_READY and not session.polish_report_id:
        raise InvalidTransition(
            session.state.value, to.value, "no final polish report for this version"
        )
    if to is ReviewState.AWAITING_FINAL_APPROVAL and not (
        session.polish_report_id and session.quality_report_id
    ):
        raise InvalidTransition(
            session.state.value,
            to.value,
            "needs a quality report and a final polish report for this version",
        )
    return _move(session, to, now=now, actor=actor, actor_kind=actor_kind, reason=reason)


def cancel(
    session: ReviewSession, *, now: datetime, actor: str, actor_kind: ActorKind, reason: str
) -> Update:
    return _move(
        session,
        ReviewState.CANCELLED,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason=reason,
        authorizations=tuple(
            replace(a, valid=False, invalidated_reason="the review was cancelled") if a.valid else a
            for a in session.authorizations
        ),
    )


# --- artifacts and feedback ------------------------------------------------------------------


def attach_artifact(
    session: ReviewSession,
    kind: ArtifactKind,
    artifact_id: str,
    *,
    timeline: TimelineRef,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    """Record a report of one of the modules, for exactly this version of the timeline."""
    _same_version(session, timeline, f"the {kind.value.replace('_', ' ')}")
    changes: dict[str, object]
    match kind:
        case ArtifactKind.QUALITY_REPORT:
            if session.quality_report_id == artifact_id:
                return Update(session, (), changed=False)
            changes = {"quality_report_id": artifact_id}
        case ArtifactKind.FINAL_POLISH_REPORT:
            if session.polish_report_id == artifact_id:
                return Update(session, (), changed=False)
            changes = {"polish_report_id": artifact_id}
        case ArtifactKind.REVISION_PLAN:
            if artifact_id in session.revision_plan_ids:
                return Update(session, (), changed=False)
            changes = {"revision_plan_ids": (*session.revision_plan_ids, artifact_id)}
        case ArtifactKind.RENDER_REPORT:
            if artifact_id in session.render_report_ids:
                return Update(session, (), changed=False)
            changes = {"render_report_ids": (*session.render_report_ids, artifact_id)}
        case ArtifactKind.EXPORT_VALIDATION_REPORT:
            changes = {"export_validation_id": artifact_id}
        case ArtifactKind.EXPORT_REPORT:
            return Update(session, (), changed=False)
    attached = _touch(session, now, **changes)
    update = Update(
        attached,
        (
            _entry(
                attached,
                "artifact",
                now,
                actor,
                actor_kind,
                f"{kind.value} {artifact_id} recorded for timeline "
                f"version {session.timeline.version}",
                subject=artifact_id,
                data=(("kind", kind.value),),
            ),
        ),
    )
    if kind is ArtifactKind.QUALITY_REPORT and attached.state is S.ANALYSIS_PENDING:
        return _merge(
            update,
            _move(
                attached,
                S.ANALYSIS_COMPLETE,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason="quality report ready",
            ),
        )
    if kind is ArtifactKind.FINAL_POLISH_REPORT and attached.state is S.FINAL_POLISH_PENDING:
        return _merge(
            update,
            _move(
                attached,
                S.FINAL_REVIEW_READY,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason="final review prepared",
            ),
        )
    return update


def add_feedback(
    session: ReviewSession,
    *,
    text: str,
    author: str,
    section_id: str | None,
    now: datetime,
) -> Update:
    item = FeedbackItem(
        feedback_id=new_id(),
        text=text,
        author=author,
        received_at=now,
        section_id=section_id,
        timeline_version=session.timeline.version,
    )
    updated = _touch(session, now, feedback=(*session.feedback, item))
    update = Update(
        updated,
        (
            _entry(
                updated,
                "feedback",
                now,
                author,
                ActorKind.HUMAN,
                text[:200],
                subject=item.feedback_id,
            ),
        ),
    )
    if updated.state in (S.REVIEW_READY, S.AWAITING_HUMAN_REVIEW):
        update = _merge(
            update,
            _move(
                updated,
                S.FEEDBACK_RECEIVED,
                now=now,
                actor=author,
                actor_kind=ActorKind.HUMAN,
                reason="feedback received",
            ),
        )
    return update


def feedback_handled(
    session: ReviewSession, feedback_ids: tuple[str, ...], plan_id: str, *, now: datetime
) -> Update:
    known = {f.feedback_id for f in session.feedback}
    for ident in feedback_ids:
        if ident not in known:
            raise UnknownSubject("feedback", ident)
    items = tuple(
        replace(f, handled_by=plan_id) if f.feedback_id in feedback_ids and not f.handled_by else f
        for f in session.feedback
    )
    if items == session.feedback:
        return Update(session, (), changed=False)
    updated = _touch(session, now, feedback=items)
    return Update(
        updated,
        (
            _entry(
                updated,
                "feedback.handled",
                now,
                "system",
                ActorKind.SYSTEM,
                f"feedback taken up by {plan_id}",
                subject=plan_id,
            ),
        ),
    )


# --- sections --------------------------------------------------------------------------------


def _section(session: ReviewSession, section_id: str) -> SectionRecord:
    record = session.section(section_id)
    if record is None:
        raise UnknownSubject("section", section_id)
    return record


def _replace_section(session: ReviewSession, record: SectionRecord) -> tuple[SectionRecord, ...]:
    return tuple(record if s.section_id == record.section_id else s for s in session.sections)


def lock_section(
    session: ReviewSession,
    section_id: str,
    *,
    timeline: TimelineRef,
    now: datetime,
    actor: str,
    note: str = "",
) -> Update:
    """Freeze a section: automation may not touch it until a person unlocks it."""
    _same_version(session, timeline, "locking a section")
    record = _section(session, section_id)
    if record.status is SectionStatus.LOCKED:
        return Update(session, (), changed=False)
    facts = session.facts.section(section_id)
    locked = replace(
        record,
        status=SectionStatus.LOCKED,
        digest=facts.digest if facts else record.digest,
        decided_by=actor,
        decided_at=now,
        drift=False,
        note=note,
    )
    updated = _touch(session, now, sections=_replace_section(session, locked))
    return Update(
        updated,
        (
            _entry(
                updated,
                "section.locked",
                now,
                actor,
                ActorKind.HUMAN,
                f"section {section_id} locked",
                subject=section_id,
            ),
        ),
    )


def unlock_section(
    session: ReviewSession,
    section_id: str,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
    reason: str,
) -> Update:
    """Only a person, and always on record: approved content is never unlocked silently."""
    if actor_kind is not ActorKind.HUMAN:
        raise NotPermitted("Unlocking a section", "only a person may unlock approved content")
    record = _section(session, section_id)
    if record.status is not SectionStatus.LOCKED:
        return Update(session, (), changed=False)
    unlocked = replace(
        record,
        status=SectionStatus.UNDER_REVIEW,
        digest="",
        drift=False,
        decided_by=actor,
        decided_at=now,
        note=reason,
    )
    updated = _touch(session, now, sections=_replace_section(session, unlocked))
    return Update(
        updated,
        (
            _entry(
                updated,
                "section.unlocked",
                now,
                actor,
                actor_kind,
                f"section {section_id} unlocked: {reason}",
                subject=section_id,
            ),
        ),
    )


def mark_under_review(
    session: ReviewSession, section_id: str, *, now: datetime, actor: str
) -> Update:
    record = _section(session, section_id)
    if record.status is not SectionStatus.UNREVIEWED:
        return Update(session, (), changed=False)
    updated = _touch(
        session,
        now,
        sections=_replace_section(
            session,
            replace(record, status=SectionStatus.UNDER_REVIEW, decided_by=actor, decided_at=now),
        ),
    )
    return Update(
        updated,
        (
            _entry(
                updated,
                "section.review",
                now,
                actor,
                ActorKind.HUMAN,
                f"section {section_id} under review",
                subject=section_id,
            ),
        ),
    )


def guard_locked(session: ReviewSession, section_ids: tuple[str, ...], what: str) -> None:
    """Refuse an automated change that reaches a locked section."""
    locked = set(session.locked_sections())
    for section_id in section_ids:
        if section_id in locked:
            raise LockedSection(section_id, what)


# --- decisions -------------------------------------------------------------------------------

_HUMAN_ONLY = frozenset(
    {ApprovalKind.FINAL, ApprovalKind.DELIVERY, ApprovalKind.OVERRIDE, ApprovalKind.SECTION}
)


def _check_actor(
    session: ReviewSession,
    kind: ApprovalKind,
    verdict: Verdict,
    actor_kind: ActorKind,
    operation: Operation | None,
) -> None:
    what = f"A {kind.value} decision"
    if actor_kind is ActorKind.SYSTEM:
        raise NotPermitted(what, "the system never decides for a person")
    if actor_kind is ActorKind.HUMAN:
        return
    if kind in _HUMAN_ONLY or verdict is not Verdict.APPROVED:
        raise NotPermitted(what, "only a person decides this")
    op = operation or Operation.LOCAL_CREATIVE_REVISION
    if session.policy.authority(op) is not Authority.AUTOMATIC:
        raise NotPermitted(
            what,
            f"'{op.value}' is {session.policy.authority(op).value.replace('_', ' ')} "
            "in this project; "
            "it needs a person's approval",
        )


def decide(
    session: ReviewSession,
    *,
    kind: ApprovalKind,
    subject_id: str,
    verdict: Verdict,
    actor: str,
    actor_kind: ActorKind,
    timeline: TimelineRef,
    now: datetime,
    sections: tuple[str, ...] = (),
    note: str = "",
    modification: str = "",
    operation: Operation | None = None,
    unlock_sections: tuple[str, ...] = (),
) -> Update:
    """Record a decision bound to the version the person looked at, and apply what it means."""
    if kind is ApprovalKind.EXPORT:
        raise NotPermitted("An export decision", "use authorize_export")
    _same_version(session, timeline, f"the {kind.value} decision")
    _check_actor(session, kind, verdict, actor_kind, operation)
    if verdict is Verdict.MODIFIED and not modification.strip():
        raise NotPermitted("A modified approval", "say what was changed")
    existing = session.current_approval(subject_id, kind)
    if (
        existing is not None
        and existing.verdict is verdict
        and existing.modification == modification
    ):
        return Update(session, (), changed=False)

    entries: list[AuditEntry] = []
    current = session
    for section_id in unlock_sections:
        unlocked = unlock_section(
            current,
            section_id,
            now=now,
            actor=actor,
            actor_kind=actor_kind,
            reason=f"to carry out {subject_id}",
        )
        current = unlocked.session
        entries.extend(unlocked.entries)
    if verdict is not Verdict.REJECTED and kind in (ApprovalKind.REVISION, ApprovalKind.PROPOSAL):
        guard_locked(current, sections, f"approving {subject_id}")

    digest = ""
    if kind is ApprovalKind.SECTION:
        record = _section(current, subject_id)
        if record.status is SectionStatus.LOCKED and verdict is Verdict.REJECTED:
            raise LockedSection(subject_id, "requesting a revision")
        facts = current.facts.section(subject_id)
        digest = facts.digest if facts else ""
    approval = Approval(
        approval_id=new_id(),
        kind=kind,
        subject_id=subject_id,
        verdict=verdict,
        actor=actor,
        actor_kind=actor_kind,
        timeline=current.timeline,
        decided_at=now,
        review_version=current.review_version,
        sections=sections or ((subject_id,) if kind is ApprovalKind.SECTION else ()),
        note=note,
        modification=modification,
        manual=actor_kind is ActorKind.HUMAN,
        digest=digest,
    )
    recorded = _touch(current, now, approvals=(*current.approvals, approval))
    entries.append(
        _entry(
            recorded,
            "decision",
            now,
            actor,
            actor_kind,
            f"{verdict.value} {kind.value} {subject_id}"
            + (f" ({modification})" if modification else ""),
            subject=approval.approval_id,
            data=(("kind", kind.value), ("verdict", verdict.value), ("subject", subject_id)),
        )
    )
    return _merge(Update(recorded, tuple(entries)), _effect_of(recorded, approval, now))


def _effect_of(session: ReviewSession, approval: Approval, now: datetime) -> Update:
    actor, actor_kind = approval.actor, approval.actor_kind
    verdict = approval.verdict
    match approval.kind:
        case ApprovalKind.REVISION:
            _require(session, S.AWAITING_REVISION_APPROVAL, action="decide on a revision")
            target = {
                Verdict.APPROVED: S.REVISION_APPROVED,
                Verdict.REJECTED: S.REVISION_REJECTED,
                Verdict.MODIFIED: S.REVISION_PROPOSED,
            }[verdict]
            return _move(
                session,
                target,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason=f"{verdict.value} {approval.subject_id}",
            )
        case ApprovalKind.SECTION:
            record = _section(session, approval.subject_id)
            status = (
                SectionStatus.APPROVED
                if verdict is Verdict.APPROVED
                else SectionStatus.REVISION_REQUESTED
            )
            if record.status is SectionStatus.LOCKED:
                return Update(session)
            decided = replace(
                record,
                status=status,
                digest=approval.digest if verdict is Verdict.APPROVED else "",
                decided_by=actor,
                decided_at=now,
            )
            return Update(_touch(session, now, sections=_replace_section(session, decided)))
        case ApprovalKind.FINAL:
            _require(session, S.AWAITING_FINAL_APPROVAL, action="approve the final video")
            if verdict is Verdict.REJECTED:
                return _move(
                    session,
                    S.AWAITING_HUMAN_REVIEW,
                    now=now,
                    actor=actor,
                    actor_kind=actor_kind,
                    reason="the final video was not approved",
                )
            if verdict is Verdict.MODIFIED:
                raise NotPermitted("The final approval", "approve it as it is or reject it")
            return _move(
                session,
                S.FINAL_APPROVED,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason="the final video was approved",
                final_approval_id=approval.approval_id,
            )
        case ApprovalKind.DELIVERY:
            _require(session, S.EXPORT_VALIDATED, action="approve delivery")
            return Update(session)
        case _:
            return Update(session)


# --- the final export ------------------------------------------------------------------------


def authorize_export(
    session: ReviewSession,
    *,
    timeline: TimelineRef,
    export_profile_id: str,
    output_config_hash: str,
    quality_report_id: str,
    polish_report_id: str,
    blocking_unresolved: tuple[str, ...],
    actor: str,
    actor_kind: ActorKind,
    now: datetime,
) -> Update:
    """The explicit step that lets the final export start. Only a person can take it, only for the
    exact video they approved, and only when nothing that blocks the export is left unresolved."""
    if actor_kind is not ActorKind.HUMAN:
        raise NotPermitted("Authorizing the final export", "only a person may authorize it")
    _same_version(session, timeline, "the export authorization")
    _require(session, S.FINAL_APPROVED, action="authorize the export")
    reasons: list[str] = []
    final = session.approval(session.final_approval_id)
    if final is None or not session.is_current(final) or final.verdict is not Verdict.APPROVED:
        reasons.append("there is no current final approval for this version")
    if not session.quality_report_id or quality_report_id != session.quality_report_id:
        reasons.append("the quality report is not the one of this version")
    if not session.polish_report_id or polish_report_id != session.polish_report_id:
        reasons.append("the final polish report is not the one of this version")
    for issue_id in blocking_unresolved:
        override = session.current_approval(issue_id, ApprovalKind.OVERRIDE)
        if override is None or override.verdict is not Verdict.APPROVED:
            reasons.append(f"issue {issue_id} blocks the export and has not been overridden")
    if reasons:
        raise NotAuthorized(reasons)
    authorization = ExportAuthorization(
        authorization_id=new_id(),
        timeline=session.timeline,
        review_version=session.review_version,
        final_approval_id=session.final_approval_id,
        quality_report_id=quality_report_id,
        polish_report_id=polish_report_id,
        export_profile_id=export_profile_id,
        output_config_hash=output_config_hash,
        authorized_by=actor,
        authorized_at=now,
    )
    record = Approval(
        approval_id=new_id(),
        kind=ApprovalKind.EXPORT,
        subject_id=authorization.authorization_id,
        verdict=Verdict.APPROVED,
        actor=actor,
        actor_kind=actor_kind,
        timeline=session.timeline,
        decided_at=now,
        review_version=session.review_version,
        note=f"profile {export_profile_id}",
    )
    authorized = _touch(
        session,
        now,
        authorizations=(*session.authorizations, authorization),
        approvals=(*session.approvals, record),
    )
    entry = _entry(
        authorized,
        "export.authorized",
        now,
        actor,
        actor_kind,
        f"final export authorized with profile {export_profile_id}",
        subject=authorization.authorization_id,
        data=(("profile", export_profile_id), ("config", output_config_hash)),
    )
    return _merge(
        Update(authorized, (entry,)),
        _move(
            authorized,
            S.EXPORT_AUTHORIZED,
            now=now,
            actor=actor,
            actor_kind=actor_kind,
            reason="authorized",
            export_authorization_id=authorization.authorization_id,
            export_status=ExportStatus.AUTHORIZED,
        ),
    )


def verify_export_authorization(
    session: ReviewSession,
    authorization_id: str,
    *,
    timeline: TimelineRef,
    export_profile_id: str,
    output_config_hash: str,
) -> ExportAuthorization:
    """Whether this authorization still lets THIS output of THIS video be exported. Raises
    ``NotAuthorized`` with every reason; nothing is changed."""
    reasons: list[str] = []
    authorization = session.authorization(authorization_id)
    if authorization is None:
        raise NotAuthorized(["there is no such authorization in this review"])
    if not authorization.valid:
        reasons.append(authorization.invalidated_reason or "the authorization was withdrawn")
    if not authorization.timeline.same_content(session.timeline):
        reasons.append("the timeline changed after the export was authorized")
    if not authorization.timeline.same_content(timeline):
        reasons.append("the export is for another version of the timeline than the one authorized")
    if authorization.export_profile_id != export_profile_id:
        reasons.append("the export profile is not the one authorized")
    if authorization.output_config_hash != output_config_hash:
        reasons.append("the output settings are not the ones authorized")
    final = session.approval(authorization.final_approval_id)
    if final is None or not session.is_current(final):
        reasons.append("the final approval no longer applies")
    if session.state not in (S.EXPORT_AUTHORIZED, S.EXPORT_IN_PROGRESS):
        reasons.append(f"the review is in state '{session.state.value}'")
    if reasons:
        raise NotAuthorized(reasons)
    return authorization


def begin_export(
    session: ReviewSession,
    authorization_id: str,
    export_id: str,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    _require(session, S.EXPORT_AUTHORIZED, action="start the export")
    authorization = session.authorization(authorization_id)
    if authorization is None or not authorization.valid:
        raise NotAuthorized(["there is no valid authorization"])
    if authorization.used_by_export and authorization.used_by_export != export_id:
        raise NotAuthorized(
            [f"the authorization already started export {authorization.used_by_export}"]
        )
    used = tuple(
        replace(a, used_by_export=export_id) if a.authorization_id == authorization_id else a
        for a in session.authorizations
    )
    return _move(
        session,
        S.EXPORT_IN_PROGRESS,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason=f"export {export_id} started",
        authorizations=used,
        export_id=export_id,
        export_status=ExportStatus.IN_PROGRESS,
    )


def export_rendered(
    session: ReviewSession, *, now: datetime, actor: str, actor_kind: ActorKind
) -> Update:
    return _move(
        session,
        S.EXPORT_VALIDATION_PENDING,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason="the file was written",
        export_status=ExportStatus.VALIDATING,
    )


def export_failed(
    session: ReviewSession, reason: str, *, now: datetime, actor: str, actor_kind: ActorKind
) -> Update:
    return _move(
        session,
        S.EXPORT_FAILED,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason=reason,
        export_status=ExportStatus.FAILED,
    )


def record_export_validation(
    session: ReviewSession,
    status: ValidationStatus,
    validation_id: str,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    """What the file check said. Only PASS and WARNING make the export technically ready; a check
    that could not decide waits for a person, a failure stops delivery."""
    _require(session, S.EXPORT_VALIDATION_PENDING, action="record the export validation")
    recorded = _touch(session, now, export_validation_id=validation_id)
    match status:
        case ValidationStatus.PASS | ValidationStatus.WARNING:
            return _move(
                recorded,
                S.EXPORT_VALIDATED,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason=f"validation {status.value}",
                export_status=ExportStatus.VALIDATED,
            )
        case ValidationStatus.FAIL:
            return _move(
                recorded,
                S.EXPORT_FAILED,
                now=now,
                actor=actor,
                actor_kind=actor_kind,
                reason="validation failed",
                export_status=ExportStatus.FAILED,
            )
        case ValidationStatus.MANUAL_REVIEW_REQUIRED:
            held = _touch(recorded, now, export_status=ExportStatus.MANUAL_REVIEW)
            return Update(
                held,
                (
                    _entry(
                        held,
                        "export.manual_review",
                        now,
                        actor,
                        actor_kind,
                        "the file needs a person's look; delivery waits",
                        subject=validation_id,
                    ),
                ),
            )


def resolve_manual_validation(
    session: ReviewSession,
    accept: bool,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
    note: str,
) -> Update:
    if actor_kind is not ActorKind.HUMAN:
        raise NotPermitted("Settling a validation that needs review", "only a person may")
    _require(session, S.EXPORT_VALIDATION_PENDING, action="settle the validation")
    if session.export_status is not ExportStatus.MANUAL_REVIEW:
        raise InvalidTransition(
            session.state.value, "settle", "the validation did not ask for a person"
        )
    target = S.EXPORT_VALIDATED if accept else S.EXPORT_FAILED
    status = ExportStatus.VALIDATED if accept else ExportStatus.FAILED
    return _move(
        session,
        target,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason=f"a person {'accepted' if accept else 'rejected'} the file: {note}",
        export_status=status,
    )


def retry_export(
    session: ReviewSession, *, now: datetime, actor: str, actor_kind: ActorKind
) -> Update:
    """After a failure the same authorization may be used again, if it still stands."""
    _require(session, S.EXPORT_FAILED, action="retry the export")
    authorization = session.authorization(session.export_authorization_id)
    if (
        authorization is None
        or not authorization.valid
        or not authorization.timeline.same_content(session.timeline)
    ):
        raise NotAuthorized(["the authorization no longer applies; approve and authorize again"])
    freed = tuple(
        replace(a, used_by_export="") if a.authorization_id == authorization.authorization_id else a
        for a in session.authorizations
    )
    return _move(
        session,
        S.EXPORT_AUTHORIZED,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason="retry",
        authorizations=freed,
        export_status=ExportStatus.AUTHORIZED,
    )


def mark_delivery_ready(
    session: ReviewSession, *, now: datetime, actor: str, actor_kind: ActorKind
) -> Update:
    _require(session, S.EXPORT_VALIDATED, action="mark the delivery ready")
    if session.policy.require_delivery_approval:
        approval = session.current_approval(session.export_id, ApprovalKind.DELIVERY)
        if approval is None or approval.verdict is not Verdict.APPROVED:
            raise NotAuthorized(["a person has not approved this export for delivery"])
    return _move(
        session,
        S.DELIVERY_READY,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason="delivery ready",
    )


# --- revisions -------------------------------------------------------------------------------


def begin_revision(
    session: ReviewSession, plan_id: str, *, now: datetime, actor: str, actor_kind: ActorKind
) -> Update:
    """A revision starts only on a current approval of exactly this plan."""
    _require(session, S.REVISION_APPROVED, action="start a revision")
    approval = session.current_approval(plan_id, ApprovalKind.REVISION)
    if approval is None or approval.verdict is not Verdict.APPROVED:
        raise NotAuthorized([f"revision plan {plan_id} has no current approval"])
    guard_locked(session, approval.sections, f"revision {plan_id}")
    return _move(
        session,
        S.REVISION_IN_PROGRESS,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason=f"carrying out {plan_id}",
    )


def revision_failed(
    session: ReviewSession,
    plan_id: str,
    reason: str,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    """The revision did not (fully) happen: back to where a person decides what next."""
    _require(session, S.REVISION_IN_PROGRESS, action="record a failed revision")
    return _move(
        session,
        S.AWAITING_HUMAN_REVIEW,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
        reason=f"{plan_id} failed: {reason}",
    )


def record_revision_result(
    session: ReviewSession,
    plan_id: str,
    facts: TimelineFacts,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    """The revision produced a new version. Its approval is used up; decisions about untouched
    sections stand; the review goes on to revalidation."""
    _require(session, S.REVISION_IN_PROGRESS, action="record the result of a revision")
    approvals = tuple(
        replace(a, consumed_by_version=facts.ref.version)
        if a.subject_id == plan_id
        and a.kind is ApprovalKind.REVISION
        and a.consumed_by_version is None
        else a
        for a in session.approvals
    )
    bound = bind_timeline(
        _touch(session, now, approvals=approvals),
        facts,
        now=now,
        actor=actor,
        actor_kind=actor_kind,
    )
    current = bound.session
    return _merge(
        bound,
        _move(
            current,
            S.REVISION_COMPLETE,
            now=now,
            actor=actor,
            actor_kind=actor_kind,
            reason=f"{plan_id} carried out",
        ),
    )


# --- checkpoints -----------------------------------------------------------------------------


def add_checkpoint(
    session: ReviewSession,
    checkpoint: CheckpointRecord,
    *,
    now: datetime,
    actor: str,
    actor_kind: ActorKind,
) -> Update:
    updated = _touch(session, now, checkpoints=(*session.checkpoints, checkpoint))
    return Update(
        updated,
        (
            _entry(
                updated,
                "checkpoint",
                now,
                actor,
                actor_kind,
                f"checkpoint '{checkpoint.label}' of timeline version {checkpoint.timeline.version}"
                + ("" if checkpoint.verified else " (not verified)"),
                subject=checkpoint.checkpoint_id,
            ),
        ),
    )
