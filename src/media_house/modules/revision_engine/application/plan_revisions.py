"""Planning a revision: read the quality report and the person's words, and propose changes.

What it never does is change the timeline. It reads the latest report of the exact version the
review is at, looks at what a person edited in Resolve first (their work is the baseline), turns
pending feedback into instructions (asking when the words are not clear), applies requests to lock
or keep, plans the changes with the owner, the level of approval and the conflicts of each, stores
the plan and hands it to the review for approval.
"""

from dataclasses import dataclass

from media_house.core.application.ports import Clock
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.quality_control.application.contracts import QualityCatalog, QualityReport
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    ReviewCatalog,
    ReviewFailure,
    ReviewSession,
    ReviewSessions,
    ReviewState,
    SectionStatus,
)
from media_house.modules.revision_engine.application.events import RevisionPlanCreated
from media_house.modules.revision_engine.application.mix_state import current_overrides
from media_house.modules.revision_engine.application.ports import ManualChangeSync
from media_house.modules.revision_engine.application.store import RevisionStore
from media_house.modules.revision_engine.application.views import (
    issue_facts,
    provenance_counts,
    review_view,
    timeline_view,
)
from media_house.modules.revision_engine.domain.errors import (
    NothingToRevise,
    PlanNotCurrent,
    RevisionError,
)
from media_house.modules.revision_engine.domain.facts import MixView, TimelineView
from media_house.modules.revision_engine.domain.feedback import (
    Context,
    answer,
    drafts_of,
    interpret,
)
from media_house.modules.revision_engine.domain.model import (
    Clarification,
    Instruction,
    RevisionPlan,
)
from media_house.modules.revision_engine.domain.plan import assemble_plan
from media_house.modules.revision_engine.domain.planner import PlanningInput
from media_house.modules.revision_engine.domain.planner import plan as plan_changes
from media_house.modules.revision_engine.domain.values import (
    ChangeStatus,
    InstructionKind,
    TargetKind,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_SYSTEM = Actor.system("revision_engine")
_PLANNABLE = (
    ReviewState.REVIEW_READY,
    ReviewState.AWAITING_HUMAN_REVIEW,
    ReviewState.FEEDBACK_RECEIVED,
    ReviewState.REVIEW_AGAIN_REQUIRED,
    ReviewState.ANALYSIS_COMPLETE,
    ReviewState.REVISION_REJECTED,
    ReviewState.REVISION_PROPOSED,
)

type RevisionFailure = RevisionError | MediaError | ReviewFailure


@dataclass(frozen=True, slots=True)
class PlanRevisionCommand:
    review_session_id: str
    #: Who is speaking (the reviewer by default).
    author: str = ""
    #: New feedback in the person's own words; it is recorded in the review first.
    feedback: tuple[str, ...] = ()
    #: Where the person was looking, so "this section" and "the music here" mean something.
    playhead_frame: int | None = None
    selected_clips: tuple[str, ...] = ()
    section_id: str | None = None
    #: Answers to earlier questions: ``(instruction id, index of the option chosen)``.
    answers: tuple[tuple[str, int], ...] = ()
    #: The report to plan from; the latest of this exact version by default.
    quality_report_id: str | None = None
    #: Propose again what a person rejected (they asked for another look).
    reconsider: bool = False
    include_optional: bool = False


@dataclass(frozen=True, slots=True)
class Question:
    instruction_id: str
    original: str
    clarification: Clarification


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan: RevisionPlan
    asset: MediaAssetDto
    #: Words that could not be acted on without asking.
    questions: tuple[Question, ...]
    #: ``False``: this exact plan already existed.
    created: bool


class PlanRevisions:
    def __init__(
        self,
        library: MediaLibrary,
        builds: BuiltTimelineCatalog,
        qualities: QualityCatalog,
        mixes: MixCatalog,
        sessions: ReviewSessions,
        reviews: ReviewCatalog,
        manual: ManualChangeSync,
        store: RevisionStore,
        events: EventPublisher,
        clock: Clock,
    ) -> None:
        self._library = library
        self._builds = builds
        self._qualities = qualities
        self._mixes = mixes
        self._sessions = sessions
        self._reviews = reviews
        self._manual = manual
        self._store = store
        self._events = events
        self._clock = clock

    def execute(
        self, command: PlanRevisionCommand, ctx: JobContext
    ) -> Result[PlanResult, RevisionFailure]:
        found = self._reviews.get(command.review_session_id)
        if isinstance(found, Err):
            return Err(NothingToRevise(str(found.error.user_message)))
        session = found.value
        warnings: list[str] = []
        changes = self._manual.read_back(session.anchor_asset_id, session.timeline.timeline_id, ctx)
        if not changes.checked:
            warnings.append(
                "A person's edits in Resolve could not be checked"
                + (f" ({changes.note})" if changes.note else "")
                + "; the plan assumes the built timeline is the current one."
            )
        elif changes.recorded or changes.unrecorded:
            warnings.append(
                f"{changes.recorded} edits made in Resolve were read back and are kept as "
                "a person's work."
            )
        synced = self._sessions.sync_timeline(session.session_id)
        if isinstance(synced, Err):
            return Err(NothingToRevise(str(synced.error.user_message)))
        session = synced.value
        for text in command.feedback:
            added = self._sessions.add_feedback(
                session.session_id,
                text,
                author=command.author or session.reviewer,
                section_id=command.section_id,
            )
            if isinstance(added, Err):
                return Err(NothingToRevise(str(added.error.user_message)))
            session = added.value
        if session.state not in _PLANNABLE:
            return Err(
                NothingToRevise(
                    f"A revision cannot be planned while the review is '{session.state.value}'."
                )
            )
        latest = self._builds.latest(
            session.anchor_asset_id, timeline_id=session.timeline.timeline_id
        )
        if isinstance(latest, Err):
            return latest
        if (
            latest.value is None
            or latest.value[0].build.build_hash != session.timeline.content_hash
        ):
            return Err(PlanNotCurrent(session.timeline.version, session.timeline.version + 1))
        timeline = latest.value[0]
        view = timeline_view(timeline)
        report = self._report(session, command.quality_report_id)
        if isinstance(report, Err):
            return report
        quality = report.value

        author = command.author or session.reviewer
        context = Context(
            view,
            playhead_frame=command.playhead_frame,
            selected_clips=command.selected_clips,
            section_id=command.section_id,
        )
        instructions = self._instructions(session, context, command, author)
        mix = self._mix_view(session)
        protected = self._protect(session, instructions, author, view)
        history = self._store.plans(session.anchor_asset_id, session_id=session.session_id)
        if isinstance(history, Err):
            return history
        earlier = [p for p, _ in history.value]
        for plan in earlier:
            protected = protected | frozenset(plan.protected_clips)
        extra = tuple(d for i in instructions for d in drafts_of(i, view, mix))
        planned = plan_changes(
            PlanningInput(
                timeline=view,
                issues=issue_facts(quality),
                review=review_view(session),
                mix=mix,
                protected_clips=protected,
                reconsider=command.reconsider,
                include_optional=command.include_optional,
                extra=extra,
            )
        )
        previous = earlier[0] if earlier else None
        plan = assemble_plan(
            timeline=view,
            review=review_view(session),
            report_id=quality.quality_report_id,
            anchor_asset_id=session.anchor_asset_id,
            changes=planned.changes,
            instructions=instructions,
            skipped=planned.skipped,
            created_at=self._clock.now().isoformat(),
            previous=previous if previous and previous.revision_plan_id else None,
            protected_clips=tuple(sorted(protected)),
            warnings=tuple(warnings),
            provenance=provenance_counts(view),
        )
        questions = tuple(
            Question(i.instruction_id, i.original, i.clarification)
            for i in instructions
            if i.clarification is not None
        )
        return self._finish(session, plan, questions, command, view.version)

    # --- the pieces ----------------------------------------------------------------------------
    def _mix_view(self, session: ReviewSession) -> MixView:
        return MixView(
            overrides=current_overrides(
                self._mixes, session.anchor_asset_id, session.timeline.timeline_id
            )
        )

    def _report(
        self, session: ReviewSession, report_id: str | None
    ) -> Result[QualityReport, RevisionFailure]:
        latest = self._qualities.latest(
            session.anchor_asset_id,
            timeline_id=session.timeline.timeline_id,
            timeline_content_hash=session.timeline.content_hash,
        )
        if isinstance(latest, Err):
            return latest
        if latest.value is None or (
            report_id is not None and latest.value[0].quality_report_id != report_id
        ):
            return Err(
                NothingToRevise(
                    "There is no quality report for this version of the timeline; run quality "
                    "control first, so the proposals rest on what is there now."
                )
            )
        return Ok(latest.value[0])

    def _instructions(
        self,
        session: ReviewSession,
        context: Context,
        command: PlanRevisionCommand,
        author: str,
    ) -> tuple[Instruction, ...]:
        found: list[Instruction] = []
        for item in session.pending_feedback():
            made = interpret(
                item.text, item.author or author, context, feedback_id=item.feedback_id
            )
            found.extend(made)
        chosen = dict(command.answers)
        settled: list[Instruction] = []
        for instruction in found:
            index = chosen.get(instruction.instruction_id)
            options = instruction.clarification.options if instruction.clarification else ()
            if index is not None and 0 <= index < len(options):
                settled.append(answer(instruction, options[index][1]))
            else:
                settled.append(instruction)
        return tuple(settled)

    def _protect(
        self,
        session: ReviewSession,
        instructions: tuple[Instruction, ...],
        author: str,
        view: TimelineView,
    ) -> frozenset[str]:
        """A request to leave something as it is is carried out at once: it only protects."""
        protected: set[str] = set()
        for instruction in instructions:
            if instruction.kind not in (InstructionKind.LOCK, InstructionKind.KEEP):
                continue
            target = instruction.target
            if target.kind is TargetKind.CLIPS:
                protected.update(target.clip_ids)
            elif target.kind is TargetKind.SECTION and instruction.kind is InstructionKind.LOCK:
                for section_id in target.section_ids:
                    record = session.section(section_id)
                    if record is not None and record.status is not SectionStatus.LOCKED:
                        locked = self._sessions.lock_section(
                            session.session_id,
                            section_id,
                            timeline=session.timeline,
                            actor=Actor.human(instruction.author or author),
                            note=instruction.original,
                        )
                        if isinstance(locked, Err):
                            _log.warning("Section not locked", reason=str(locked.error))
            elif target.kind is TargetKind.SECTION:
                protected.update(
                    c.clip_id for c in view.clips if c.section_id in target.section_ids
                )
        return frozenset(protected)

    def _finish(
        self,
        session: ReviewSession,
        plan: RevisionPlan,
        questions: tuple[Question, ...],
        command: PlanRevisionCommand,
        version: int,
    ) -> Result[PlanResult, RevisionFailure]:
        anchor = self._library.get(session.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        existing = self._store.find(
            session.anchor_asset_id, plan.revision_plan_id, session_id=session.session_id
        )
        if isinstance(existing, Err):
            return existing
        if existing.value is not None:
            held, asset = existing.value
            return Ok(PlanResult(held, asset, questions, created=False))
        saved = self._store.save(anchor.value, plan)
        if isinstance(saved, Err):
            return saved
        actor = _SYSTEM
        if plan.proposed_changes:
            attached = self._sessions.attach(
                session.session_id,
                ArtifactKind.REVISION_PLAN,
                plan.revision_plan_id,
                timeline=session.timeline,
                actor=actor,
            )
            if isinstance(attached, Err):
                return Err(NothingToRevise(str(attached.error.user_message)))
            moved = attached.value
            if moved.state is not ReviewState.REVISION_PROPOSED:
                step = self._sessions.advance(
                    session.session_id,
                    ReviewState.REVISION_PROPOSED,
                    actor=actor,
                    reason="a plan was made",
                )
                if isinstance(step, Err):
                    return Err(NothingToRevise(str(step.error.user_message)))
            step = self._sessions.advance(
                session.session_id,
                ReviewState.AWAITING_REVISION_APPROVAL,
                actor=actor,
                reason=f"plan {plan.revision_plan_id} awaits a decision",
            )
            if isinstance(step, Err):
                return Err(NothingToRevise(str(step.error.user_message)))
        handled = tuple(
            f.feedback_id for f in session.pending_feedback() if _answered(f.feedback_id, plan)
        )
        if handled:
            self._sessions.feedback_handled(session.session_id, handled, plan.revision_plan_id)
        self._events.publish(
            RevisionPlanCreated(
                occurred_at=self._clock.now(),
                revision_plan_id=plan.revision_plan_id,
                review_session_id=session.session_id,
                timeline_version=version,
                changes=len(plan.proposed_changes),
                conflicts=sum(
                    1 for c in plan.proposed_changes if c.status is ChangeStatus.CONFLICT
                ),
            )
        )
        del command
        return Ok(PlanResult(plan, saved.value, questions, created=True))


def _answered(feedback_id: str, plan: RevisionPlan) -> bool:
    """Feedback is handled once its instructions are in the plan and none waits for an answer."""
    mine = [i for i in plan.instructions if i.feedback_id == feedback_id]
    return bool(mine) and all(i.clarification is None for i in mine)
