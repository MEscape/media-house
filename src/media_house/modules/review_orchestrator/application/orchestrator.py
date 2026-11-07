"""The review orchestrator: one authoritative review state per timeline, shared by every Layer 6
module. It owns no timeline and decides nothing creative; it keeps what was decided, about which
exact version, by whom, and refuses what the state of the review does not allow.

Every operation loads the session, applies a pure step from ``domain/workflow.py`` and stores the
result and its audit entries in one write. A write that lost a race to another is applied again
to the newer session, so two modules reporting at once never overwrite each other.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import MediaError
from media_house.modules.review_orchestrator.application.actors import Actor
from media_house.modules.review_orchestrator.application.events import (
    ApprovalInvalidated,
    DecisionRecorded,
    ExportAuthorized,
    ReviewEvent,
    ReviewStateChanged,
    TimelineVersionBound,
)
from media_house.modules.review_orchestrator.application.ports import ReviewStore
from media_house.modules.review_orchestrator.application.views import facts_of
from media_house.modules.review_orchestrator.domain import workflow
from media_house.modules.review_orchestrator.domain.errors import ReviewError
from media_house.modules.review_orchestrator.domain.permissions import Operation, PermissionPolicy
from media_house.modules.review_orchestrator.domain.session import (
    AuditEntry,
    CheckpointRecord,
    ExportAuthorization,
    ReviewSession,
)
from media_house.modules.review_orchestrator.domain.timeline import TimelineFacts, TimelineRef
from media_house.modules.review_orchestrator.domain.values import (
    ApprovalKind,
    ArtifactKind,
    ReviewState,
    ValidationStatus,
    Verdict,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)
_ATTEMPTS = 5

type ReviewFailure = ReviewError | NotFoundError | ConflictError | MediaError


@dataclass(frozen=True, slots=True)
class OpenSessionCommand:
    anchor_asset_id: str
    reviewer: str
    timeline_id: str | None = None
    project_id: str | None = None
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    resolve_project_ref: str = ""
    resolve_timeline_ref: str = ""


@dataclass(frozen=True, slots=True)
class OpenResult:
    session: ReviewSession
    #: ``False``: an open review of this timeline already existed (it is brought up to date).
    created: bool


@dataclass(frozen=True, slots=True)
class DecideCommand:
    session_id: str
    kind: ApprovalKind
    subject_id: str
    verdict: Verdict
    actor: Actor
    #: The version the person looked at. A decision on another version is refused.
    timeline: TimelineRef
    sections: tuple[str, ...] = ()
    note: str = ""
    modification: str = ""
    operation: Operation | None = None
    unlock_sections: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthorizeExportCommand:
    session_id: str
    actor: Actor
    timeline: TimelineRef
    export_profile_id: str
    output_config_hash: str
    quality_report_id: str
    polish_report_id: str
    #: Issues that block the export (from the quality and polish reports); each needs an override.
    blocking_unresolved: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckpointCommand:
    session_id: str
    label: str
    reason: str = ""
    mix_result_asset_id: str = ""
    mix_overrides_hash: str = ""
    resolve_binding_asset_id: str = ""


class ReviewOrchestrator:
    """Implements ``ReviewSessions`` and ``ReviewCatalog``."""

    def __init__(
        self,
        store: ReviewStore,
        catalog: BuiltTimelineCatalog,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._clock = clock
        self._events = events

    # --- reading --------------------------------------------------------------------------------
    def get(self, session_id: str) -> Result[ReviewSession, ReviewFailure]:
        stored = self._store.get(session_id)
        if stored is None:
            return Err(_missing(session_id))
        return Ok(stored.session)

    def find(
        self, anchor_asset_id: str, *, timeline_id: str | None = None, active_only: bool = True
    ) -> list[ReviewSession]:
        return self._store.find(anchor_asset_id, timeline_id=timeline_id, active_only=active_only)

    def history(self, session_id: str, *, after: int = 0, limit: int = 500) -> list[AuditEntry]:
        return self._store.entries(session_id, after=after, limit=limit)

    # --- opening and the timeline ---------------------------------------------------------------
    def open(self, command: OpenSessionCommand) -> Result[OpenResult, ReviewFailure]:
        facts = self._latest_facts(command.anchor_asset_id, command.timeline_id)
        if isinstance(facts, Err):
            return facts
        existing = self._store.find(
            command.anchor_asset_id, timeline_id=facts.value.ref.timeline_id, active_only=True
        )
        if existing:
            synced = self.sync_timeline(existing[0].session_id)
            if isinstance(synced, Err):
                return synced
            return Ok(OpenResult(synced.value, created=False))
        now = self._clock.now()
        update = workflow.open_session(
            session_id=new_id(),
            project_id=command.project_id,
            facts=facts.value,
            reviewer=command.reviewer,
            policy=command.policy,
            resolve_project_ref=command.resolve_project_ref,
            resolve_timeline_ref=command.resolve_timeline_ref,
            now=now,
        )
        self._store.create(update.session, update.entries)
        self._publish(update.session, update.entries)
        _log.info(
            "Review opened",
            session_id=update.session.session_id,
            timeline_version=update.session.timeline.version,
        )
        return Ok(OpenResult(update.session, created=True))

    def sync_timeline(self, session_id: str) -> Result[ReviewSession, ReviewFailure]:
        """Look at the stored timeline again: a new version (a revision, a person's edits read back
        from Resolve) is bound, and what it changes is decided here, on record."""
        found = self.get(session_id)
        if isinstance(found, Err):
            return found
        session = found.value
        facts = self._latest_facts(session.anchor_asset_id, session.timeline.timeline_id)
        if isinstance(facts, Err):
            return facts
        actor = Actor.system("timeline sync")
        return self._mutate(
            session_id,
            lambda s: workflow.bind_timeline(
                s, facts.value, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    # --- moves ----------------------------------------------------------------------------------
    def advance(
        self,
        session_id: str,
        to: ReviewState,
        *,
        actor: Actor,
        reason: str = "",
        timeline: TimelineRef | None = None,
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.advance(
                s, to, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind,
                reason=reason, timeline=timeline,
            ),
        )  # fmt: skip

    def cancel(
        self, session_id: str, *, actor: Actor, reason: str
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.cancel(
                s, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind, reason=reason
            ),
        )

    def attach(
        self,
        session_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        *,
        timeline: TimelineRef,
        actor: Actor,
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.attach_artifact(
                s, kind, artifact_id, timeline=timeline, now=self._clock.now(),
                actor=actor.name, actor_kind=actor.kind,
            ),
        )  # fmt: skip

    # --- people ---------------------------------------------------------------------------------
    def add_feedback(
        self, session_id: str, text: str, *, author: str, section_id: str | None = None
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.add_feedback(
                s, text=text, author=author, section_id=section_id, now=self._clock.now()
            ),
        )

    def feedback_handled(
        self, session_id: str, feedback_ids: tuple[str, ...], plan_id: str
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.feedback_handled(s, feedback_ids, plan_id, now=self._clock.now()),
        )

    def decide(self, command: DecideCommand) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            command.session_id,
            lambda s: workflow.decide(
                s,
                kind=command.kind,
                subject_id=command.subject_id,
                verdict=command.verdict,
                actor=command.actor.name,
                actor_kind=command.actor.kind,
                timeline=command.timeline,
                now=self._clock.now(),
                sections=command.sections,
                note=command.note,
                modification=command.modification,
                operation=command.operation,
                unlock_sections=command.unlock_sections,
            ),
        )

    def lock_section(
        self,
        session_id: str,
        section_id: str,
        *,
        timeline: TimelineRef,
        actor: Actor,
        note: str = "",
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.lock_section(
                s, section_id, timeline=timeline, now=self._clock.now(), actor=actor.name, note=note
            ),
        )

    def unlock_section(
        self, session_id: str, section_id: str, *, actor: Actor, reason: str
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.unlock_section(
                s, section_id, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind,
                reason=reason,
            ),
        )  # fmt: skip

    def mark_under_review(
        self, session_id: str, section_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.mark_under_review(
                s, section_id, now=self._clock.now(), actor=actor.name
            ),
        )

    def ensure_automation_allowed(
        self, session_id: str, section_ids: tuple[str, ...], what: str
    ) -> Result[ReviewSession, ReviewFailure]:
        """Whether an automated change to these sections may go ahead (no locked section)."""
        found = self.get(session_id)
        if isinstance(found, Err):
            return found
        try:
            workflow.guard_locked(found.value, section_ids, what)
        except ReviewError as exc:
            return Err(exc)
        return found

    # --- revisions ------------------------------------------------------------------------------
    def begin_revision(
        self, session_id: str, plan_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.begin_revision(
                s, plan_id, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    def revision_failed(
        self, session_id: str, plan_id: str, reason: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.revision_failed(
                s, plan_id, reason, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    def record_revision_result(
        self, session_id: str, plan_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        """The revision produced a new stored version: bind it, use up the approval, go on."""
        found = self.get(session_id)
        if isinstance(found, Err):
            return found
        facts = self._latest_facts(found.value.anchor_asset_id, found.value.timeline.timeline_id)
        if isinstance(facts, Err):
            return facts
        return self._mutate(
            session_id,
            lambda s: workflow.record_revision_result(
                s, plan_id, facts.value, now=self._clock.now(), actor=actor.name,
                actor_kind=actor.kind,
            ),
        )  # fmt: skip

    def create_checkpoint(
        self, command: CheckpointCommand, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        """A known state to come back to. The stored build is read back and its hash compared, so a
        checkpoint says whether it is usable."""
        found = self.get(command.session_id)
        if isinstance(found, Err):
            return found
        session = found.value
        verified = False
        if session.timeline.build_asset_id:
            loaded = self._catalog.load(session.timeline.build_asset_id)
            verified = (
                isinstance(loaded, Ok)
                and loaded.value.build.build_hash == session.timeline.content_hash
            )
        locked, approved = session.locked_sections(), session.approved_sections()
        checkpoint = CheckpointRecord(
            checkpoint_id=new_id(),
            label=command.label,
            timeline=session.timeline,
            created_at=self._clock.now(),
            review_version=session.review_version,
            reason=command.reason,
            mix_result_asset_id=command.mix_result_asset_id,
            mix_overrides_hash=command.mix_overrides_hash,
            resolve_binding_asset_id=command.resolve_binding_asset_id,
            quality_report_id=session.quality_report_id,
            locked_sections=locked,
            approved_sections=approved,
            verified=verified,
        )
        return self._mutate(
            command.session_id,
            lambda s: workflow.add_checkpoint(
                s, checkpoint, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    # --- the final export -----------------------------------------------------------------------
    def authorize_export(
        self, command: AuthorizeExportCommand
    ) -> Result[ReviewSession, ReviewFailure]:
        synced = self.sync_timeline(command.session_id)
        if isinstance(synced, Err):
            return synced
        return self._mutate(
            command.session_id,
            lambda s: workflow.authorize_export(
                s,
                timeline=command.timeline,
                export_profile_id=command.export_profile_id,
                output_config_hash=command.output_config_hash,
                quality_report_id=command.quality_report_id,
                polish_report_id=command.polish_report_id,
                blocking_unresolved=command.blocking_unresolved,
                actor=command.actor.name,
                actor_kind=command.actor.kind,
                now=self._clock.now(),
            ),
        )

    def verify_export(
        self,
        session_id: str,
        authorization_id: str,
        *,
        timeline: TimelineRef,
        export_profile_id: str,
        output_config_hash: str,
    ) -> Result[ExportAuthorization, ReviewFailure]:
        """Whether this authorization lets THIS output of THIS version be exported now. The stored
        timeline is looked at first, so an edit made since is noticed here, not after the export."""
        synced = self.sync_timeline(session_id)
        if isinstance(synced, Err):
            return synced
        try:
            return Ok(
                workflow.verify_export_authorization(
                    synced.value,
                    authorization_id,
                    timeline=timeline,
                    export_profile_id=export_profile_id,
                    output_config_hash=output_config_hash,
                )
            )
        except ReviewError as exc:
            return Err(exc)

    def begin_export(
        self, session_id: str, authorization_id: str, export_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.begin_export(
                s, authorization_id, export_id, now=self._clock.now(), actor=actor.name,
                actor_kind=actor.kind,
            ),
        )  # fmt: skip

    def export_rendered(
        self, session_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.export_rendered(
                s, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    def export_failed(
        self, session_id: str, reason: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.export_failed(
                s, reason, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    def record_export_validation(
        self, session_id: str, status: ValidationStatus, validation_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.record_export_validation(
                s, status, validation_id, now=self._clock.now(), actor=actor.name,
                actor_kind=actor.kind,
            ),
        )  # fmt: skip

    def resolve_manual_validation(
        self, session_id: str, *, accept: bool, actor: Actor, note: str
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.resolve_manual_validation(
                s, accept, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind, note=note
            ),
        )

    def retry_export(
        self, session_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.retry_export(
                s, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    def mark_delivery_ready(
        self, session_id: str, *, actor: Actor
    ) -> Result[ReviewSession, ReviewFailure]:
        return self._mutate(
            session_id,
            lambda s: workflow.mark_delivery_ready(
                s, now=self._clock.now(), actor=actor.name, actor_kind=actor.kind
            ),
        )

    # --- plumbing -------------------------------------------------------------------------------
    def _latest_facts(
        self, anchor_asset_id: str, timeline_id: str | None
    ) -> Result[TimelineFacts, ReviewFailure]:
        latest = self._catalog.latest(anchor_asset_id, timeline_id=timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(
                NotFoundError(
                    "No built timeline for the recording",
                    user_message="There is no built timeline to review yet.",
                )
            )
        timeline, asset = latest.value
        return Ok(facts_of(timeline, asset.id))

    def _mutate(
        self, session_id: str, step: Callable[[ReviewSession], workflow.Update]
    ) -> Result[ReviewSession, ReviewFailure]:
        for _attempt in range(_ATTEMPTS):
            stored = self._store.get(session_id)
            if stored is None:
                return Err(_missing(session_id))
            try:
                update = step(stored.session)
            except ReviewError as exc:
                return Err(exc)
            if not update.changed and not update.entries:
                return Ok(stored.session)
            try:
                self._store.save(update.session, update.entries, expected_revision=stored.revision)
            except ConflictError:
                continue  # another writer got there first: apply the step to what they wrote
            self._publish(update.session, update.entries)
            return Ok(update.session)
        return Err(
            ConflictError(
                "The review kept changing under this step",
                user_message="The review is being changed by someone else; try again.",
            )
        )

    def _publish(
        self, session: ReviewSession, entries: tuple[AuditEntry, ...] | list[AuditEntry]
    ) -> None:
        for entry in entries:
            event = _event_of(session, entry)
            if event is not None:
                self._events.publish(event)


def _missing(session_id: str) -> NotFoundError:
    return NotFoundError(
        f"Unknown review session {session_id}", user_message="That review does not exist."
    )


def _event_of(session: ReviewSession, entry: AuditEntry) -> ReviewEvent | None:
    at, sid, version, summary = entry.at, session.session_id, entry.timeline_version, entry.summary
    data = dict(entry.data)
    match entry.kind:
        case "state":
            return ReviewStateChanged(
                occurred_at=at, session_id=sid, timeline_version=version, summary=summary,
                source=data.get("from", ""), target=data.get("to", ""),
            )  # fmt: skip
        case "timeline.changed" | "timeline.rebound":
            return TimelineVersionBound(
                occurred_at=at, session_id=sid, timeline_version=version, summary=summary,
                material=entry.kind == "timeline.changed",
            )  # fmt: skip
        case "decision":
            return DecisionRecorded(
                occurred_at=at, session_id=sid, timeline_version=version, summary=summary,
                kind=data.get("kind", ""), verdict=data.get("verdict", ""),
                subject=data.get("subject", ""),
            )  # fmt: skip
        case "approval.invalidated":
            return ApprovalInvalidated(
                occurred_at=at, session_id=sid, timeline_version=version, summary=summary,
                approval_id=entry.subject,
            )  # fmt: skip
        case "export.authorized":
            return ExportAuthorized(
                occurred_at=at, session_id=sid, timeline_version=version, summary=summary,
                authorization_id=entry.subject,
            )  # fmt: skip
        case _:
            return None
