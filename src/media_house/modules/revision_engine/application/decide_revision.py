"""A person's decision on a plan: approve, reject or modify each proposal, and the plan as a whole.

Nothing is executed here. A decision is bound to the version of the timeline the person looked at;
a proposal that conflicts with a locked section or with their own work is approved only when they
say so for exactly that conflict (and an unlock is a recorded act of its own). A rejection is kept
by the review so the same proposal is not made again; a modification is kept as feedback and
planned again, never applied as if it had been agreed.
"""

from dataclasses import dataclass, replace

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ApprovalKind,
    DecideCommand,
    Operation,
    ReviewCatalog,
    ReviewSessions,
    TimelineRef,
    Verdict,
)
from media_house.modules.revision_engine.application.plan_revisions import RevisionFailure
from media_house.modules.revision_engine.application.store import RevisionStore
from media_house.modules.revision_engine.domain.errors import NothingToRevise, PlanNotCurrent
from media_house.modules.revision_engine.domain.model import ProposedChange, RevisionPlan
from media_house.modules.revision_engine.domain.plan import seal
from media_house.modules.revision_engine.domain.values import ChangeStatus, PlanStatus
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProposalDecision:
    proposal_id: str
    verdict: Verdict
    #: Why (a rejection) or what was changed (a modification).
    note: str = ""


@dataclass(frozen=True, slots=True)
class DecideRevisionCommand:
    review_session_id: str
    revision_plan_id: str
    actor: Actor
    #: The version of the timeline the person looked at.
    timeline: TimelineRef
    decisions: tuple[ProposalDecision, ...]
    note: str = ""
    #: Proposals whose conflicts (a locked section, the person's own edit) the person overrides.
    override_conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionResult:
    plan: RevisionPlan
    asset: MediaAssetDto
    approved: tuple[str, ...]
    rejected: tuple[str, ...]
    modified: tuple[str, ...]


class DecideRevisions:
    def __init__(
        self,
        library: MediaLibrary,
        sessions: ReviewSessions,
        reviews: ReviewCatalog,
        store: RevisionStore,
        clock: Clock,
    ) -> None:
        self._library = library
        self._sessions = sessions
        self._reviews = reviews
        self._store = store
        self._clock = clock

    def execute(self, command: DecideRevisionCommand) -> Result[DecisionResult, RevisionFailure]:
        found = self._reviews.get(command.review_session_id)
        if isinstance(found, Err):
            return Err(NothingToRevise(str(found.error.user_message)))
        session = found.value
        stored = self._store.find(
            session.anchor_asset_id, command.revision_plan_id, session_id=session.session_id
        )
        if isinstance(stored, Err):
            return stored
        if stored.value is None:
            return Err(
                NothingToRevise(f"There is no plan {command.revision_plan_id} in this review.")
            )
        plan, _ = stored.value
        if plan.source_timeline_hash != session.timeline.content_hash:
            return Err(PlanNotCurrent(plan.source_timeline_version, session.timeline.version))
        by_id = {d.proposal_id: d for d in command.decisions}
        unknown = [d for d in by_id if plan.change(d) is None]
        if unknown:
            return Err(NothingToRevise(f"The plan has no proposal {unknown[0]}."))
        approved: list[ProposedChange] = []
        rejected: list[ProposedChange] = []
        modified: list[ProposedChange] = []
        for change in plan.proposed_changes:
            decision = by_id.get(change.proposal_id)
            if decision is None:
                continue
            if decision.verdict is Verdict.REJECTED:
                rejected.append(change)
            elif decision.verdict is Verdict.MODIFIED:
                modified.append(change)
            else:
                approved.append(change)
        refused = self._refuse_unoverridden(command, approved)
        if refused is not None:
            return Err(refused)
        unlock = tuple(
            dict.fromkeys(
                s
                for c in approved
                if c.proposal_id in command.override_conflicts
                for s in c.section_ids
                if s in session.locked_sections()
            )
        )
        recorded = self._record(command, session.session_id, approved, rejected, modified, by_id)
        if isinstance(recorded, Err):
            return recorded
        verdict = (
            Verdict.MODIFIED if modified else Verdict.APPROVED if approved else Verdict.REJECTED
        )
        sections = tuple(dict.fromkeys(s for c in approved for s in c.section_ids))
        operation = _strictest_operation(approved)
        decided = self._sessions.decide(
            DecideCommand(
                session_id=session.session_id,
                kind=ApprovalKind.REVISION,
                subject_id=plan.revision_plan_id,
                verdict=verdict,
                actor=command.actor,
                timeline=command.timeline,
                sections=sections,
                note=command.note,
                modification="; ".join(
                    d.note for d in command.decisions if d.verdict is Verdict.MODIFIED
                ),
                operation=operation,
                unlock_sections=unlock,
            )
        )
        if isinstance(decided, Err):
            return decided
        for d in command.decisions:
            if d.verdict is Verdict.MODIFIED and d.note:
                self._sessions.add_feedback(session.session_id, d.note, author=command.actor.name)
        return self._store_decision(
            plan, approved, rejected, modified, verdict, session.anchor_asset_id
        )

    # --- the pieces ----------------------------------------------------------------------------
    @staticmethod
    def _refuse_unoverridden(
        command: DecideRevisionCommand, approved: list[ProposedChange]
    ) -> NothingToRevise | None:
        for change in approved:
            if (
                change.status is ChangeStatus.CONFLICT
                and change.proposal_id not in command.override_conflicts
            ):
                return NothingToRevise(
                    f"Proposal {change.proposal_id} conflicts with {'; '.join(change.conflicts)} "
                    "Approve it only by overriding that conflict."
                )
        return None

    def _record(
        self,
        command: DecideRevisionCommand,
        session_id: str,
        approved: list[ProposedChange],
        rejected: list[ProposedChange],
        modified: list[ProposedChange],
        by_id: dict[str, ProposalDecision],
    ) -> Result[None, RevisionFailure]:
        for group, verdict in (
            (approved, Verdict.APPROVED),
            (rejected, Verdict.REJECTED),
            (modified, Verdict.MODIFIED),
        ):
            for change in group:
                if verdict is Verdict.APPROVED and change.conflicts:
                    override = self._sessions.decide(
                        DecideCommand(
                            session_id=session_id,
                            kind=ApprovalKind.OVERRIDE,
                            subject_id=f"conflict:{change.proposal_key}",
                            verdict=Verdict.APPROVED,
                            actor=command.actor,
                            timeline=command.timeline,
                            sections=change.section_ids,
                            note="; ".join(change.conflicts),
                        )
                    )
                    if isinstance(override, Err):
                        return override
                result = self._sessions.decide(
                    DecideCommand(
                        session_id=session_id,
                        kind=ApprovalKind.PROPOSAL,
                        subject_id=change.proposal_key,
                        verdict=verdict,
                        actor=command.actor,
                        timeline=command.timeline,
                        sections=change.section_ids,
                        note=by_id[change.proposal_id].note,
                        modification=by_id[change.proposal_id].note
                        if verdict is Verdict.MODIFIED
                        else "",
                        operation=Operation(change.operation),
                    )
                )
                if isinstance(result, Err):
                    return result
        return Ok(None)

    def _store_decision(
        self,
        plan: RevisionPlan,
        approved: list[ProposedChange],
        rejected: list[ProposedChange],
        modified: list[ProposedChange],
        verdict: Verdict,
        anchor_id: str,
    ) -> Result[DecisionResult, RevisionFailure]:
        status = {c.proposal_id: ChangeStatus.APPROVED for c in approved}
        status.update({c.proposal_id: ChangeStatus.REJECTED for c in rejected})
        status.update({c.proposal_id: ChangeStatus.REJECTED for c in modified})
        changes = tuple(
            replace(c, status=status.get(c.proposal_id, c.status)) for c in plan.proposed_changes
        )
        decided = seal(
            replace(
                plan,
                proposed_changes=changes,
                approval_status={
                    Verdict.APPROVED: "approved"
                    if len(approved) == len(plan.proposed_changes)
                    else "partially_approved",
                    Verdict.MODIFIED: "modified",
                    Verdict.REJECTED: "rejected",
                }[verdict],
                execution_status={
                    Verdict.APPROVED: PlanStatus.APPROVED,
                    Verdict.MODIFIED: PlanStatus.PROPOSED,
                    Verdict.REJECTED: PlanStatus.REJECTED,
                }[verdict],
            )
        )
        anchor = self._library.get(anchor_id)
        if isinstance(anchor, Err):
            return anchor
        saved = self._store.save(anchor.value, decided)
        if isinstance(saved, Err):
            return saved
        _log.info("Revision decided", plan=plan.revision_plan_id, verdict=verdict.value)
        return Ok(
            DecisionResult(
                decided,
                saved.value,
                tuple(c.proposal_id for c in approved),
                tuple(c.proposal_id for c in rejected),
                tuple(c.proposal_id for c in modified),
            )
        )


_STRICTNESS = {"automatic": 0, "suggested": 1, "manual_only": 2}


def _strictest_operation(changes: list[ProposedChange]) -> Operation | None:
    """The operation with the strictest permission: it decides who may approve."""
    if not changes:
        return None
    order = {
        Operation.TECHNICAL_REPAIR: 0,
        Operation.ASSET_RECONNECTION: 0,
        Operation.DERIVATIVE_REBUILD: 0,
        Operation.RENDER_PREPARATION: 0,
        Operation.FINAL_POLISH_CHANGE: 1,
        Operation.LOCAL_CREATIVE_REVISION: 2,
        Operation.TIMELINE_RECONSTRUCTION: 3,
        Operation.GLOBAL_CREATIVE_REVISION: 3,
        Operation.OVERRIDE_DECISION: 4,
    }
    return max((Operation(c.operation) for c in changes), key=lambda op: order.get(op, 2))
