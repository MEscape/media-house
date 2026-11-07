"""Carrying out an approved change, through the module that owns it.

Each executor drives one owner's own public contract and nothing else: the timeline builder for
clip edits and rebuilds, the audio mix engine for levels, the Resolve adapter for synchronisation.
A change nobody here can drive is a hand-off: it is passed on, said to be passed on, and never
reported as done. A returned outcome is what the command said; whether the problem is really gone
is decided afterwards, on the next quality report.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from media_house.core.application.ports import Clock
from media_house.modules.audio_mix_engine.application.contracts import (
    AudioMixer,
    MixCatalog,
    MixConfig,
    MixOverrides,
    RenderMixCommand,
)
from media_house.modules.resolve_adapter.application.contracts import (
    ExecuteTimelineCommand,
    ExecutionPolicy,
    ResolveAdapterConfig,
    ResolveExecutor,
    ResolveSynchronizer,
    SyncCommand,
)
from media_house.modules.revision_engine.application.events import RevisionHandedOff
from media_house.modules.revision_engine.application.mix_state import current_spec
from media_house.modules.revision_engine.application.views import EDITOR_PREFIX
from media_house.modules.revision_engine.domain.model import ProposedChange, RevisionPlan
from media_house.modules.revision_engine.domain.values import ChangeKind, ChangeStatus
from media_house.modules.timeline_builder.application.contracts import (
    BuildTimelineCommand,
    EditKind,
    HumanEdit,
    RecordEditsCommand,
    TimelineBuilder,
    TimelineEditor,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChangeOutcome:
    proposal_id: str
    status: ChangeStatus
    detail: str
    changed_elements: tuple[str, ...] = ()
    #: Named values for the execution record (``mix_overrides``).
    extra: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    plan: RevisionPlan
    anchor_asset_id: str
    timeline_id: str
    extra: dict[str, str] = field(default_factory=dict)


class RevisionExecutor(Protocol):
    def handles(self, kind: ChangeKind) -> bool: ...

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]: ...


def _failed(changes: Sequence[ProposedChange], detail: str) -> tuple[ChangeOutcome, ...]:
    return tuple(ChangeOutcome(c.proposal_id, ChangeStatus.FAILED, detail) for c in changes)


# --- clip edits ------------------------------------------------------------------------------


class TimelineEdits:
    """Trims, removals and switches of clips: recorded in the built timeline as the next version.
    The edit is marked as made by this module on a person's approval, so a later build keeps it."""

    _KINDS = frozenset(
        {
            ChangeKind.TRIM_CLIP,
            ChangeKind.DELETE_CLIP,
            ChangeKind.DISABLE_CLIP,
            ChangeKind.SET_OPACITY,
        }
    )

    def __init__(self, editor: TimelineEditor) -> None:
        self._editor = editor

    def handles(self, kind: ChangeKind) -> bool:
        return kind in self._KINDS

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]:
        editor = f"{EDITOR_PREFIX}{context.plan.revision_id}"
        edits = {c.proposal_id: self._edit(c, editor) for c in changes}
        batch = tuple(e for e in edits.values() if e is not None)
        if not batch:
            return _failed(changes, "the change names no clip")
        recorded = self._editor.execute(
            RecordEditsCommand(context.anchor_asset_id, batch, timeline_id=context.timeline_id), ctx
        )
        if not isinstance(recorded, Err):
            return tuple(
                ChangeOutcome(
                    c.proposal_id,
                    ChangeStatus.EXECUTED,
                    f"recorded in timeline version {recorded.value.timeline.build.build_version}",
                    c.clip_ids,
                )
                for c in changes
                if edits[c.proposal_id] is not None
            )
        if len(batch) == 1:
            return _failed(changes, str(recorded.error.user_message))
        # Refused as a whole: find which edits stand on their own (one bad edit costs only itself).
        outcomes: list[ChangeOutcome] = []
        for change in changes:
            edit = edits[change.proposal_id]
            if edit is None:
                outcomes.append(
                    ChangeOutcome(
                        change.proposal_id, ChangeStatus.FAILED, "the change names no clip"
                    )
                )
                continue
            one = self._editor.execute(
                RecordEditsCommand(
                    context.anchor_asset_id, (edit,), timeline_id=context.timeline_id
                ),
                ctx,
            )
            if isinstance(one, Err):
                outcomes.append(
                    ChangeOutcome(
                        change.proposal_id, ChangeStatus.FAILED, str(one.error.user_message)
                    )
                )
            else:
                outcomes.append(
                    ChangeOutcome(
                        change.proposal_id,
                        ChangeStatus.EXECUTED,
                        f"recorded in timeline version {one.value.timeline.build.build_version}",
                        change.clip_ids,
                    )
                )
        return tuple(outcomes)

    @staticmethod
    def _edit(change: ProposedChange, editor: str) -> HumanEdit | None:
        clip = change.param("clip") or (change.clip_ids[0] if change.clip_ids else "")
        if not clip:
            return None
        if change.kind is ChangeKind.DELETE_CLIP:
            return HumanEdit(EditKind.DELETE, clip, editor=editor, note=change.title)
        fields: list[tuple[str, str]] = []
        if change.kind is ChangeKind.TRIM_CLIP:
            for name in ("start_frame", "end_frame"):
                if change.param(name):
                    fields.append((name, change.param(name)))
        elif change.kind is ChangeKind.DISABLE_CLIP:
            fields.append(("enabled", "false"))
        elif change.kind is ChangeKind.SET_OPACITY:
            fields.append(("opacity", change.param("opacity", "1")))
        if not fields:
            return None
        return HumanEdit(EditKind.MODIFY, clip, tuple(fields), editor=editor, note=change.title)


# --- rebuild ---------------------------------------------------------------------------------


class TimelineRebuild:
    """Build again from the stored specification: the files are looked up now, an unchanged element
    is carried over and a person's edits are kept by the builder's own conflict policy."""

    def __init__(self, builder: TimelineBuilder) -> None:
        self._builder = builder

    def handles(self, kind: ChangeKind) -> bool:
        return kind is ChangeKind.REBUILD_TIMELINE

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]:
        built = self._builder.execute(
            BuildTimelineCommand(anchor_asset_id=context.anchor_asset_id), ctx
        )
        if isinstance(built, Err):
            return _failed(changes, str(built.error.user_message))
        version = built.value.timeline.build.build_version
        detail = (
            f"built again as timeline version {version}"
            if built.value.created
            else "the build was identical to the stored one: nothing changed (same inputs)"
        )
        return tuple(
            ChangeOutcome(c.proposal_id, ChangeStatus.EXECUTED, detail, c.clip_ids) for c in changes
        )


# --- audio -----------------------------------------------------------------------------------


def parse_overrides(labels: Sequence[str]) -> tuple[dict[str, float], dict[str, float]]:
    """``bus:music=-3`` / ``clip:a:aW=2`` as the mix engine records them."""
    bus: dict[str, float] = {}
    clip: dict[str, float] = {}
    for label in labels:
        head, _, value = label.rpartition("=")
        kind, _, name = head.partition(":")
        try:
            number = float(value)
        except ValueError:
            continue
        (bus if kind == "bus" else clip)[name] = number
    return bus, clip


def overrides_of(bus: dict[str, float], clip: dict[str, float]) -> MixOverrides:
    return MixOverrides(
        bus_gain_db=tuple(sorted(bus.items())), clip_gain_db=tuple(sorted(clip.items()))
    )


class AudioMixChanges:
    """Levels of a bus or a clip, and a new mix: recorded as the mix engine's own overrides (added
    to the ones already in effect) and rendered again; the engine reuses every stem a change does
    not touch."""

    _KINDS = frozenset({ChangeKind.MIX_BUS_GAIN, ChangeKind.MIX_CLIP_GAIN, ChangeKind.REMIX})

    def __init__(self, mixer: AudioMixer, mixes: MixCatalog) -> None:
        self._mixer = mixer
        self._mixes = mixes

    def handles(self, kind: ChangeKind) -> bool:
        return kind in self._KINDS

    def current(
        self, anchor: str, timeline_id: str
    ) -> tuple[tuple[str, ...], str | None, str | None]:
        spec = current_spec(self._mixes, anchor, timeline_id)
        if spec is None:
            return (), None, None
        return spec.provenance.overrides, spec.provenance.profile, spec.provenance.delivery

    def restore(
        self,
        context: ExecutionContext,
        bus: dict[str, float],
        clip: dict[str, float],
        ctx: JobContext,
    ) -> Result[None, str]:
        """Render the mix with exactly these overrides (a rollback puts the old ones back)."""
        _, profile, delivery = self.current(context.anchor_asset_id, context.timeline_id)
        done = self._mixer.execute(
            RenderMixCommand(
                anchor_asset_id=context.anchor_asset_id,
                timeline_id=context.timeline_id,
                config=MixConfig(
                    profile=profile, delivery=delivery, overrides=overrides_of(bus, clip)
                ),
            ),
            ctx,
        )
        if isinstance(done, Err):
            return Err(str(done.error.user_message))
        return Ok(None)

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]:
        labels, profile, delivery = self.current(context.anchor_asset_id, context.timeline_id)
        bus, clip = parse_overrides(labels)
        for change in changes:
            try:
                delta = float(change.param("delta_db", "0"))
            except ValueError:
                return _failed(changes, f"'{change.param('delta_db')}' is not a level in dB")
            if change.kind is ChangeKind.MIX_BUS_GAIN:
                bus[change.param("bus")] = bus.get(change.param("bus"), 0.0) + delta
            elif change.kind is ChangeKind.MIX_CLIP_GAIN:
                clip[change.param("clip")] = clip.get(change.param("clip"), 0.0) + delta
        overrides = overrides_of(bus, clip)
        done = self._mixer.execute(
            RenderMixCommand(
                anchor_asset_id=context.anchor_asset_id,
                timeline_id=context.timeline_id,
                config=MixConfig(profile=profile, delivery=delivery, overrides=overrides),
            ),
            ctx,
        )
        if isinstance(done, Err):
            return _failed(changes, str(done.error.user_message))
        result = done.value.result
        now = ", ".join(overrides.labels()) or "none"
        detail = f"mix rendered ({result.state.value}); overrides now {now}"
        return tuple(
            ChangeOutcome(
                c.proposal_id,
                ChangeStatus.EXECUTED,
                detail,
                c.clip_ids,
                extra=(("mix_overrides", ",".join(overrides.labels())),),
            )
            for c in changes
        )


# --- Resolve ---------------------------------------------------------------------------------


class ResolveChanges:
    """Bring Resolve and the built timeline together, never at a person's expense: their edits are
    read back first and a conflict is kept, not decided here."""

    def __init__(self, synchronizer: ResolveSynchronizer, executor: ResolveExecutor) -> None:
        self._synchronizer = synchronizer
        self._executor = executor

    def handles(self, kind: ChangeKind) -> bool:
        return kind is ChangeKind.RESYNC_RESOLVE

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]:
        outcomes: list[ChangeOutcome] = []
        for change in changes:
            if change.param("direction") == "read_back":
                read = self._synchronizer.execute(
                    SyncCommand(
                        anchor_asset_id=context.anchor_asset_id,
                        timeline_id=context.timeline_id,
                        record=True,
                        editor=f"{EDITOR_PREFIX}{context.plan.revision_id}:read_back",
                    ),
                    ctx,
                )
                if isinstance(read, Err):
                    outcomes.append(
                        ChangeOutcome(
                            change.proposal_id, ChangeStatus.FAILED, str(read.error.user_message)
                        )
                    )
                else:
                    outcomes.append(
                        ChangeOutcome(
                            change.proposal_id,
                            ChangeStatus.EXECUTED,
                            f"{len(read.value.recorded)} edits read back, "
                            f"{len(read.value.unrecorded)} stay in Resolve",
                            read.value.recorded,
                        )
                    )
                continue
            written = self._executor.execute(
                ExecuteTimelineCommand(
                    anchor_asset_id=context.anchor_asset_id,
                    timeline_id=context.timeline_id,
                    config=ResolveAdapterConfig(policy=ExecutionPolicy.PRESERVE_MANUAL_EDITS),
                ),
                ctx,
            )
            if isinstance(written, Err):
                outcomes.append(
                    ChangeOutcome(
                        change.proposal_id, ChangeStatus.FAILED, str(written.error.user_message)
                    )
                )
            else:
                outcomes.append(
                    ChangeOutcome(
                        change.proposal_id,
                        ChangeStatus.EXECUTED,
                        f"Resolve execution: {written.value.report.outcome.value}",
                    )
                )
        return tuple(outcomes)


# --- the rest are hand-offs ------------------------------------------------------------------


class Handoff:
    """A change for a module whose re-planning this module does not drive: it is passed on as an
    event, listed as handed off, and checked afterwards like everything else."""

    def __init__(self, events: EventPublisher, clock: Clock) -> None:
        self._events = events
        self._clock = clock

    def handles(self, kind: ChangeKind) -> bool:
        return kind is ChangeKind.HANDOFF

    def execute(
        self, changes: Sequence[ProposedChange], context: ExecutionContext, ctx: JobContext
    ) -> tuple[ChangeOutcome, ...]:
        del ctx
        outcomes = []
        for change in changes:
            intent = change.param("intent")
            self._events.publish(
                RevisionHandedOff(
                    occurred_at=self._clock.now(),
                    revision_plan_id=context.plan.revision_plan_id,
                    review_session_id=context.plan.review_session_id,
                    timeline_version=context.plan.source_timeline_version,
                    proposal_id=change.proposal_id,
                    owner_module=change.owner_module,
                    intent=intent,
                )
            )
            outcomes.append(
                ChangeOutcome(
                    change.proposal_id,
                    ChangeStatus.HANDED_OFF,
                    f"requested of {change.owner_module} ({intent}); it was not carried out here",
                )
            )
        _log.info("Changes handed off", count=len(outcomes), plan=context.plan.revision_plan_id)
        return tuple(outcomes)
