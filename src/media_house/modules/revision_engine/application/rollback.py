"""Going back to a checkpoint: plan it like any other revision, and carry it out by restoring the
checkpoint's clips and mix through the owners' own contracts.

A rollback is a revision of its own: it is proposed, a person approves it (it replaces decisions, so
it needs them), a checkpoint is made first, it is carried out, and the result is checked. What is
restored is the state of the checkpoint, not a copy of a stored file laid over the project: the
timeline builder records the difference as the next version, the mix engine renders the overrides
of that time, and the review binds the result and withdraws what no longer applies.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    ReviewCatalog,
    ReviewSessions,
    ReviewState,
)
from media_house.modules.revision_engine.application.executors import (
    AudioMixChanges,
    ChangeOutcome,
    ExecutionContext,
    _failed,
    parse_overrides,
)
from media_house.modules.revision_engine.application.plan_revisions import RevisionFailure
from media_house.modules.revision_engine.application.store import RevisionStore
from media_house.modules.revision_engine.application.views import (
    EDITOR_PREFIX,
    provenance_counts,
    review_view,
    timeline_view,
)
from media_house.modules.revision_engine.domain.errors import NothingToRevise
from media_house.modules.revision_engine.domain.invalidation import invalidations
from media_house.modules.revision_engine.domain.model import ProposedChange, RevisionPlan
from media_house.modules.revision_engine.domain.plan import assemble_plan
from media_house.modules.revision_engine.domain.values import ChangeKind, ChangeStatus, Level
from media_house.modules.timeline_builder.application.contracts import (
    AudioClip,
    BuiltTimeline,
    BuiltTimelineCatalog,
    EditKind,
    HumanEdit,
    RecordEditsCommand,
    TimelineEditor,
    VideoClip,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def restore_edits(
    current: BuiltTimeline, target: BuiltTimeline, *, editor: str, note: str
) -> tuple[HumanEdit, ...]:
    """The edits that turn the clips of ``current`` into those of ``target``."""
    edits: list[HumanEdit] = []
    pairs: tuple[tuple[dict[str, VideoClip], dict[str, VideoClip], tuple[str, ...]], ...] = (
        (
            {c.clip_id: c for c in current.video_clips()},
            {c.clip_id: c for c in target.video_clips()},
            ("start_frame", "end_frame", "opacity", "enabled"),
        ),
    )
    audio_now = {c.clip_id: c for c in current.audio_clips()}
    audio_then = {c.clip_id: c for c in target.audio_clips()}
    for now, then, fields in pairs:
        for clip_id, clip in sorted(then.items()):
            held = now.get(clip_id)
            if held is None:
                edits.append(
                    HumanEdit(EditKind.CREATE, clip_id, clip=clip, editor=editor, note=note)
                )
                continue
            changes = tuple(
                (name, _text(getattr(clip, name)))
                for name in fields
                if getattr(clip, name) != getattr(held, name)
            )
            if changes:
                edits.append(HumanEdit(EditKind.MODIFY, clip_id, changes, editor=editor, note=note))
        edits.extend(
            HumanEdit(EditKind.DELETE, clip_id, editor=editor, note=note)
            for clip_id in sorted(set(now) - set(then))
        )
    edits.extend(_audio_edits(audio_now, audio_then, editor, note))
    return tuple(edits)


def _audio_edits(
    now: dict[str, AudioClip], then: dict[str, AudioClip], editor: str, note: str
) -> list[HumanEdit]:
    edits: list[HumanEdit] = []
    for clip_id, clip in sorted(then.items()):
        held = now.get(clip_id)
        if held is None:
            edits.append(HumanEdit(EditKind.CREATE, clip_id, clip=clip, editor=editor, note=note))
            continue
        changes = tuple(
            (name, _text(getattr(clip, name)))
            for name in ("start_sample", "end_sample", "gain_db", "enabled")
            if getattr(clip, name) != getattr(held, name)
        )
        if changes:
            edits.append(HumanEdit(EditKind.MODIFY, clip_id, changes, editor=editor, note=note))
    edits.extend(
        HumanEdit(EditKind.DELETE, clip_id, editor=editor, note=note)
        for clip_id in sorted(set(now) - set(then))
    )
    return edits


class RestoreCheckpoint:
    """Executor of ``ROLLBACK`` changes."""

    def __init__(
        self,
        builds: BuiltTimelineCatalog,
        editor: TimelineEditor,
        mixes: AudioMixChanges,
    ) -> None:
        self._builds = builds
        self._editor = editor
        self._mixes = mixes

    def handles(self, kind: ChangeKind) -> bool:
        return kind is ChangeKind.ROLLBACK

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]:
        outcomes: list[ChangeOutcome] = []
        for change in changes:
            outcomes.append(self._one(change, context, ctx))
        return tuple(outcomes)

    def _one(
        self, change: ProposedChange, context: ExecutionContext, ctx: JobContext
    ) -> ChangeOutcome:
        build_id = change.param("build_asset_id")
        target = self._builds.load(build_id) if build_id else None
        latest = self._builds.latest(context.anchor_asset_id, timeline_id=context.timeline_id)
        if (
            target is None
            or isinstance(target, Err)
            or isinstance(latest, Err)
            or latest.value is None
        ):
            return _failed([change], "the checkpoint's stored timeline could not be read")[0]
        edits = restore_edits(
            latest.value[0],
            target.value,
            editor=f"{EDITOR_PREFIX}{context.plan.revision_id}",
            note=change.title,
        )
        detail = []
        if edits:
            recorded = self._editor.execute(
                RecordEditsCommand(context.anchor_asset_id, edits, timeline_id=context.timeline_id),
                ctx,
            )
            if isinstance(recorded, Err):
                return _failed([change], str(recorded.error.user_message))[0]
            detail.append(f"{len(edits)} clip edits restored the checkpoint's timeline")
        else:
            detail.append("the clips already match the checkpoint")
        wanted = tuple(x for x in change.param("mix_overrides").split(",") if x)
        have, _, _ = self._mixes.current(context.anchor_asset_id, context.timeline_id)
        if sorted(have) != sorted(wanted):
            bus, clip = parse_overrides(wanted)
            mixed = self._mixes.restore(context, bus, clip, ctx)
            if isinstance(mixed, Err):
                return _failed([change], mixed.error)[0]
            detail.append("the mix of that time was rendered again")
        return ChangeOutcome(
            change.proposal_id,
            ChangeStatus.EXECUTED,
            "; ".join(detail),
            tuple(e.element_id for e in edits),
            extra=(("mix_overrides", ",".join(wanted)),),
        )


@dataclass(frozen=True, slots=True)
class PlanRollbackCommand:
    review_session_id: str
    checkpoint_id: str
    actor: Actor
    reason: str = ""


class PlanRollback:
    """Make the plan that restores a checkpoint (it still needs a person's approval)."""

    def __init__(
        self,
        library: MediaLibrary,
        sessions: ReviewSessions,
        reviews: ReviewCatalog,
        builds: BuiltTimelineCatalog,
        store: RevisionStore,
        clock: Clock,
    ) -> None:
        self._library = library
        self._sessions = sessions
        self._reviews = reviews
        self._builds = builds
        self._store = store
        self._clock = clock

    def execute(
        self, command: PlanRollbackCommand
    ) -> Result[tuple[RevisionPlan, MediaAssetDto], RevisionFailure]:
        found = self._reviews.get(command.review_session_id)
        if isinstance(found, Err):
            return Err(NothingToRevise(str(found.error.user_message)))
        session = found.value
        checkpoint = session.checkpoint(command.checkpoint_id)
        if checkpoint is None:
            return Err(NothingToRevise(f"There is no checkpoint {command.checkpoint_id}."))
        if not checkpoint.verified or not checkpoint.timeline.build_asset_id:
            return Err(
                NothingToRevise(
                    "That checkpoint could not be verified when it was made, so it is not restored "
                    "automatically: it may no longer be a consistent state."
                )
            )
        latest = self._builds.latest(
            session.anchor_asset_id, timeline_id=session.timeline.timeline_id
        )
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NothingToRevise("There is no built timeline."))
        view = timeline_view(latest.value[0])
        overrides = self._overrides_at(
            session.anchor_asset_id, session.session_id, checkpoint.created_at
        )
        if isinstance(overrides, Err):
            return overrides
        title = (
            f"Restore the checkpoint '{checkpoint.label}' "
            f"(timeline version {checkpoint.timeline.version})"
        )
        change = ProposedChange(
            proposal_id="p1",
            proposal_key="rk_rollback_" + checkpoint.checkpoint_id,
            kind=ChangeKind.ROLLBACK,
            owner_module="timeline_builder",
            operation="timeline_reconstruction",
            level=Level.MANUAL_ONLY,
            title=title,
            description=(
                f"{title}. {command.reason} The clips and the mix are put back as they were; "
                "everything decided since is withdrawn and the result is checked again."
            ),
            params=(
                ("build_asset_id", checkpoint.timeline.build_asset_id),
                ("checkpoint_id", checkpoint.checkpoint_id),
                ("mix_overrides", ",".join(overrides.value)),
            ),
            expected_improvement="The timeline and the mix are as they were at the checkpoint.",
            side_effects=("Approvals of anything changed since are withdrawn.",),
            invalidates=invalidations(
                ChangeKind.ROLLBACK, owner="timeline_builder", clip_ids=(), section_ids=()
            ),
        )
        plan = assemble_plan(
            timeline=view,
            review=review_view(session),
            report_id=session.quality_report_id,
            anchor_asset_id=session.anchor_asset_id,
            changes=[change],
            instructions=[],
            skipped=[],
            created_at=self._clock.now().isoformat(),
            previous=None,
            protected_clips=(),
            warnings=(),
            provenance=provenance_counts(view),
        )
        anchor = self._library.get(session.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        saved = self._store.save(anchor.value, plan)
        if isinstance(saved, Err):
            return saved
        for step in (ReviewState.REVISION_PROPOSED, ReviewState.AWAITING_REVISION_APPROVAL):
            attached = (
                self._sessions.attach(
                    session.session_id,
                    ArtifactKind.REVISION_PLAN,
                    plan.revision_plan_id,
                    timeline=session.timeline,
                    actor=command.actor,
                )
                if step is ReviewState.REVISION_PROPOSED
                else None
            )
            if isinstance(attached, Err):
                return Err(NothingToRevise(str(attached.error.user_message)))
            moved = self._sessions.advance(
                session.session_id, step, actor=command.actor, reason="rollback"
            )
            if isinstance(moved, Err):
                return Err(NothingToRevise(str(moved.error.user_message)))
        return Ok((plan, saved.value))

    def _overrides_at(
        self, anchor_id: str, session_id: str, moment: datetime
    ) -> Result[tuple[str, ...], RevisionFailure]:
        """The mix overrides in effect at a moment: those the last revision that had finished by
        then left (none if no revision had touched the mix yet)."""
        listed = self._store.plans(anchor_id, session_id=session_id)
        if isinstance(listed, Err):
            return listed
        best: tuple[str, tuple[str, ...]] = ("", ())
        for plan, _ in listed.value:
            record = plan.execution
            if record is None or datetime.fromisoformat(record.finished_at) > moment:
                continue
            if record.finished_at > best[0]:
                best = (record.finished_at, record.mix_overrides_after)
        return Ok(best[1])
