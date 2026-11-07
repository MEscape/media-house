"""The human side of the final export: the final review, the final approval, the overrides, the
authorization.

Nothing here exports. ``present`` prepares the final review and asks the review to wait for a
person; ``approve_final`` and ``authorize`` record that a person said yes, for exactly the version
they looked at; ``override`` records that a person knowingly accepts an issue that would otherwise
stop the export. What can never be overridden (a critical problem, a file that is offline, a
report of another version) stops ``authorize`` before the review is even asked. A successful
quality report, a ready final polish or a good review render is never an authorization."""

from dataclasses import dataclass

from media_house.modules.final_polish.application.contracts import FinalReviewPackage
from media_house.modules.resolve_adapter.application.contracts import (
    ExecuteTimelineCommand,
    ExecutionPolicy,
    ResolveAdapterConfig,
    ResolveExecutor,
)
from media_house.modules.resolve_export.application.inputs import InputsReader
from media_house.modules.resolve_export.domain.errors import ExportRefused, NotReadyForExport
from media_house.modules.resolve_export.domain.readiness import ExportReadiness, assess
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ApprovalKind,
    AuthorizeExportCommand,
    DecideCommand,
    Operation,
    ReviewFailure,
    ReviewSessions,
    ReviewState,
    TimelineRef,
    Verdict,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result

_SYSTEM = Actor.system("resolve_export")

type ApprovalFailure = NotReadyForExport | ExportRefused | ReviewFailure


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    readiness: ExportReadiness
    profile_id: str
    #: What an authorization for this output is bound to (empty: the profile is unusable).
    output_config_hash: str
    timeline: TimelineRef


@dataclass(frozen=True, slots=True)
class FinalReview:
    """What a person needs to approve the final video: look at it in Resolve, then decide."""

    package: FinalReviewPackage | None
    readiness: ExportReadiness
    #: Where it is in Resolve (empty: not carried out there).
    resolve_project: str
    resolve_timeline: str
    #: Resolve holds this exact version (``False``: carry it out again first).
    resolve_is_current: bool
    timeline: TimelineRef
    state: ReviewState


class ExportApprovals:
    def __init__(
        self,
        inputs: InputsReader,
        sessions: ReviewSessions,
        resolve: ResolveExecutor,
    ) -> None:
        self._inputs = inputs
        self._sessions = sessions
        self._resolve = resolve

    # --- readiness ------------------------------------------------------------------------------
    def readiness(
        self, session_id: str, profile_id: str
    ) -> Result[ReadinessReport, ApprovalFailure]:
        read = self._inputs.read(session_id, profile_id)
        if isinstance(read, Err):
            return read
        data = read.value
        return Ok(
            ReadinessReport(assess(data.facts), profile_id, data.config_hash, data.session.timeline)
        )

    # --- the final review ------------------------------------------------------------------------
    def present(
        self,
        session_id: str,
        profile_id: str,
        ctx: JobContext,
        *,
        ensure_in_resolve: bool = False,
    ) -> Result[FinalReview, ApprovalFailure]:
        """Prepare the final review. With ``ensure_in_resolve`` the timeline is carried out in
        Resolve again when Resolve holds another version (a person's edits there are kept)."""
        synced = self._sessions.sync_timeline(session_id)
        if isinstance(synced, Err):
            return synced
        read = self._inputs.read(session_id, profile_id)
        if isinstance(read, Err):
            return read
        data = read.value
        if ensure_in_resolve and data.facts.resolve_hash != data.facts.timeline_hash:
            self._resolve.execute(
                ExecuteTimelineCommand(
                    anchor_asset_id=data.session.anchor_asset_id,
                    timeline_id=data.session.timeline.timeline_id,
                    config=ResolveAdapterConfig(policy=ExecutionPolicy.PRESERVE_MANUAL_EDITS),
                ),
                ctx,
            )
            read = self._inputs.read(session_id, profile_id)
            if isinstance(read, Err):
                return read
            data = read.value
        session = data.session
        if session.state is ReviewState.FINAL_REVIEW_READY:
            moved = self._sessions.advance(
                session_id,
                ReviewState.AWAITING_FINAL_APPROVAL,
                actor=_SYSTEM,
                reason="the final review is ready for a person",
            )
            if isinstance(moved, Err):
                return moved
            session = moved.value
        return Ok(
            FinalReview(
                package=data.polish.final_review_package if data.polish else None,
                readiness=assess(data.facts),
                resolve_project=session.resolve_project_ref,
                resolve_timeline=session.resolve_timeline_ref,
                resolve_is_current=data.facts.resolve_hash == data.facts.timeline_hash,
                timeline=session.timeline,
                state=session.state,
            )
        )

    # --- a person's decisions -------------------------------------------------------------------
    def approve_final(
        self, session_id: str, actor: Actor, timeline: TimelineRef, *, note: str = ""
    ) -> Result[ReviewState, ApprovalFailure]:
        """The person watched the final version and approves it (not yet the export)."""
        decided = self._sessions.decide(
            DecideCommand(
                session_id=session_id,
                kind=ApprovalKind.FINAL,
                subject_id="final",
                verdict=Verdict.APPROVED,
                actor=actor,
                timeline=timeline,
                note=note,
            )
        )
        if isinstance(decided, Err):
            return decided
        return Ok(decided.value.state)

    def reject_final(
        self, session_id: str, actor: Actor, timeline: TimelineRef, *, note: str
    ) -> Result[ReviewState, ApprovalFailure]:
        decided = self._sessions.decide(
            DecideCommand(
                session_id=session_id,
                kind=ApprovalKind.FINAL,
                subject_id="final",
                verdict=Verdict.REJECTED,
                actor=actor,
                timeline=timeline,
                note=note,
            )
        )
        if isinstance(decided, Err):
            return decided
        return Ok(decided.value.state)

    def override(
        self, session_id: str, actor: Actor, blocker_id: str, timeline: TimelineRef, *, note: str
    ) -> Result[ReviewState, ApprovalFailure]:
        """A person knowingly accepts an open issue. It is on record with their name; a hard blocker
        cannot be overridden (``authorize`` refuses it regardless)."""
        decided = self._sessions.decide(
            DecideCommand(
                session_id=session_id,
                kind=ApprovalKind.OVERRIDE,
                subject_id=blocker_id,
                verdict=Verdict.APPROVED,
                actor=actor,
                timeline=timeline,
                note=note,
                operation=Operation.OVERRIDE_BLOCKER,
            )
        )
        if isinstance(decided, Err):
            return decided
        return Ok(decided.value.state)

    def authorize(
        self, session_id: str, actor: Actor, timeline: TimelineRef, profile_id: str
    ) -> Result[str, ApprovalFailure]:
        """Authorize the final export of this version with this output profile. Returns the
        authorization id the export runs under."""
        read = self._inputs.read(session_id, profile_id)
        if isinstance(read, Err):
            return read
        data = read.value
        readiness = assess(data.facts)
        if not readiness.can_authorize:
            return Err(NotReadyForExport([b.message for b in readiness.hard]))
        authorized = self._sessions.authorize_export(
            AuthorizeExportCommand(
                session_id=session_id,
                actor=actor,
                timeline=timeline,
                export_profile_id=profile_id,
                output_config_hash=data.config_hash,
                quality_report_id=data.facts.quality_report_id,
                polish_report_id=data.facts.polish_report_id,
                blocking_unresolved=tuple(b.blocker_id for b in readiness.soft),
            )
        )
        if isinstance(authorized, Err):
            return authorized
        return Ok(authorized.value.export_authorization_id)

    def approve_delivery(
        self,
        session_id: str,
        actor: Actor,
        export_id: str,
        timeline: TimelineRef,
        *,
        note: str = "",
    ) -> Result[ReviewState, ApprovalFailure]:
        """A person approves the validated file for delivery (nothing is published by this)."""
        decided = self._sessions.decide(
            DecideCommand(
                session_id=session_id,
                kind=ApprovalKind.DELIVERY,
                subject_id=export_id,
                verdict=Verdict.APPROVED,
                actor=actor,
                timeline=timeline,
                note=note,
                operation=Operation.DELIVERY_APPROVAL,
            )
        )
        if isinstance(decided, Err):
            return decided
        return Ok(decided.value.state)
