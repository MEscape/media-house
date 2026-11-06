"""Read what a person changed in Resolve and hand it to Media House as a person's edits.

This is what makes a small fix in Resolve cheap: move a clip, trim it, delete it, switch it off,
change its opacity - and the next run of the pipeline keeps it (the timeline builder records it as
``human_modified`` and applies its conflict policy) instead of rebuilding over it. Nothing is
re-run to correct a small mistake.

Only what the scripting API can show is compared (``unknown`` stays unknown), and only what the
builder can represent is forwarded (position, length, in/out point, track, enabled, opacity,
deletion). Anything else a person changed (a zoom, a clip of their own) is left in Resolve,
reported, and never touched by a later run: the adapter manages only the elements it placed.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction

from media_house.core.application.ports import Clock
from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.resolve_adapter.application.execute_timeline import ExecuteError
from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.application.reporting import make_report
from media_house.modules.resolve_adapter.application.session import open_bound
from media_house.modules.resolve_adapter.application.store import ResolveStore
from media_house.modules.resolve_adapter.domain.binding import ElementBinding, SyncState
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.errors import (
    NothingBound,
    ResolveAdapterError,
    ResolveConnectionLost,
    ResolveUnavailable,
)
from media_house.modules.resolve_adapter.domain.live import LiveItem, TimelineSnapshot
from media_house.modules.resolve_adapter.domain.reconcile import live_fields
from media_house.modules.resolve_adapter.domain.report import (
    ExecutionReport,
    SyncRecord,
)
from media_house.modules.resolve_adapter.domain.values import (
    Area,
    ExecutionState,
    OperationStatus,
    Strategy,
    Verification,
)
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
    EditKind,
    HumanEdit,
    RecordEditsCommand,
    TimelineEditor,
    TrackKind,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.types import new_id


@dataclass(frozen=True, slots=True)
class SyncCommand:
    anchor_asset_id: str
    timeline_id: str | None = None
    config: ResolveAdapterConfig = field(default_factory=ResolveAdapterConfig)
    #: Hand the changes to the timeline builder (off: only look and report).
    record: bool = True
    editor: str = "resolve"


@dataclass(frozen=True, slots=True)
class SyncResult:
    report: ExecutionReport
    report_asset: MediaAssetDto
    #: The changes handed to the builder, by element id.
    recorded: tuple[str, ...]
    #: Changes that stay in Resolve only (the builder has no field for them).
    unrecorded: tuple[str, ...]
    state: SyncState


class SyncFromResolve:
    def __init__(
        self,
        backend: ResolveBackend,
        library: MediaLibrary,
        catalog: BuiltTimelineCatalog,
        editor: TimelineEditor,
        store: ResolveStore,
        clock: Clock,
    ) -> None:
        self._backend = backend
        self._library = library
        self._catalog = catalog
        self._editor = editor
        self._store = store
        self._clock = clock

    def execute(self, command: SyncCommand, ctx: JobContext) -> Result[SyncResult, ExecuteError]:
        anchor = self._library.get(command.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        latest = self._catalog.latest(command.anchor_asset_id, timeline_id=command.timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NothingBound())
        timeline = latest.value[0]
        bound = self._store.latest_binding(command.anchor_asset_id, timeline.timeline_id)
        if isinstance(bound, Err):
            return bound
        if bound.value is None:
            return Err(NothingBound())
        state = bound.value[0]

        status = self._backend.connect()
        if not status.connected:
            return Err(ResolveUnavailable(status.state.value, status.detail))
        started = self._clock.now()
        ledger = Ledger()
        ledger.enter(ExecutionState.INITIALIZING)
        changes = _Changes()
        new_state: SyncState = state
        try:
            with self._backend.exclusive():
                snapshot = open_bound(self._backend, state, command.config, ledger)
                changes = self._compare(state, snapshot, timeline, ledger)
                ctx.raise_if_cancelled()
                recorded_ok = self._record(command, state.project.timeline_id, changes, ledger, ctx)
                new_state = self._advance(state, snapshot, changes, recorded_ok)
            ledger.enter(ExecutionState.COMPLETED)
        except ResolveConnectionLost as exc:
            ledger.fail(str(exc))
            ledger.enter(ExecutionState.INTERRUPTED)
        except ResolveAdapterError as exc:
            ledger.fail(str(exc))
            ledger.enter(ExecutionState.FAILED)
        sync = SyncRecord(
            status="changes_found" if changes.any else "in_sync",
            human_modified=tuple(sorted(changes.modified)),
            human_deleted=tuple(sorted(changes.deleted)),
            human_created=tuple(sorted(changes.created)),
            conflicts=state.conflicts,
            recorded=tuple(sorted(changes.recorded)),
            detail=f"{len(changes.unrecorded)} change(s) stay in Resolve only"
            if changes.unrecorded
            else "",
        )
        report = make_report(
            ledger=ledger,
            status=status,
            execution_id=new_id(),
            started=started,
            finished=self._clock.now(),
            anchor_asset_id=command.anchor_asset_id,
            timeline_id=state.project.timeline_id,
            project_id=timeline.project_id,
            build_version=timeline.build.build_version,
            build_hash=timeline.build.build_hash,
            schema=timeline.schema_version,
            policy=command.config.policy,
            config_hash="",
            project_name=state.project.project_name,
            project_resolve_id=state.project.project_id or "",
            database=state.project.database or "",
            timeline_name=state.project.timeline_name,
            timeline_resolve_id=state.project.timeline_resolve_id or "",
            capabilities=(),
            media=(),
            sync=sync,
        )
        stored = self._store.save_report(anchor.value, report)
        if isinstance(stored, Err):
            return stored
        if new_state is not state:
            saved = self._store.save_binding(anchor.value, new_state)
            if isinstance(saved, Err):
                return saved
        return Ok(
            SyncResult(
                report=report,
                report_asset=stored.value,
                recorded=tuple(sorted(changes.recorded)),
                unrecorded=tuple(sorted(changes.unrecorded)),
                state=new_state,
            )
        )

    def _compare(
        self, state: SyncState, snapshot: TimelineSnapshot, timeline: BuiltTimeline, ledger: Ledger
    ) -> "_Changes":
        changes = _Changes()
        assets = {m.pool_item_id: m.asset_id for m in state.media}
        fps = Fraction(
            timeline.settings.frame_rate.numerator, timeline.settings.frame_rate.denominator
        )
        track_ids = _track_ids(timeline)
        known_items = {e.item_id for e in state.elements}
        for binding in state.elements:
            live = snapshot.item(binding.item_id) or snapshot.item_for(binding.element_id)
            if live is None:
                changes.deleted.append(binding.element_id)
                changes.edits.append(HumanEdit(EditKind.DELETE, binding.element_id))
                ledger.op(
                    binding.element_id,
                    Area.SYNC,
                    "detect deletion",
                    OperationStatus.EXECUTED,
                    verification=Verification.VERIFIED,
                    detail="deleted in Resolve",
                )
                continue
            base = dict(binding.fields)
            theirs = live_fields(live, assets.get(live.media_item_id or ""), frozenset(base))
            moved = {k: theirs[k] for k, v in base.items() if k in theirs and theirs[k] != v}
            if not moved:
                continue
            changes.modified.append(binding.element_id)
            edit, kept_back = _edit(binding, live, moved, timeline, fps, track_ids)
            if edit is not None:
                changes.edits.append(edit)
                changes.fields[binding.element_id] = tuple(k for k in moved if k not in kept_back)
            for name in kept_back:
                changes.unrecorded.append(f"{binding.element_id}:{name}")
            ledger.op(
                binding.element_id,
                Area.SYNC,
                "detect change",
                OperationStatus.EXECUTED,
                verification=Verification.VERIFIED,
                detail="changed in Resolve: " + ", ".join(sorted(moved)),
            )
        for item in snapshot.items:
            if item.item_id not in known_items and not item.tag:
                changes.created.append(item.item_id)
        if changes.created:
            ledger.note(f"{len(changes.created)} clip(s) a person added in Resolve are left alone")
        return changes

    def _record(
        self,
        command: SyncCommand,
        timeline_id: str,
        changes: "_Changes",
        ledger: Ledger,
        ctx: JobContext,
    ) -> bool:
        if not command.record or not changes.edits:
            return True
        result = self._editor.execute(
            RecordEditsCommand(
                anchor_asset_id=command.anchor_asset_id,
                edits=tuple(changes.edits),
                timeline_id=timeline_id,
            ),
            ctx,
        )
        if isinstance(result, Err):
            ledger.fail(f"the builder refused the changes: {result.error}")
            ledger.op(
                "timeline",
                Area.SYNC,
                "record the changes",
                OperationStatus.FAILED,
                detail=str(result.error),
            )
            return False
        changes.recorded.extend(e.element_id for e in changes.edits)
        ledger.op(
            "timeline",
            Area.SYNC,
            "record the changes",
            OperationStatus.EXECUTED,
            strategy=Strategy.NATIVE_API,
            detail=f"{len(changes.edits)} change(s) are now part of the built timeline",
        )
        return True

    @staticmethod
    def _advance(
        state: SyncState, snapshot: TimelineSnapshot, changes: "_Changes", recorded: bool
    ) -> SyncState:
        """The new base: what was recorded is now in sync; the rest is still a person's."""
        if not recorded or not changes.recorded:
            return state
        assets = {m.pool_item_id: m.asset_id for m in state.media}
        deleted = set(changes.deleted)
        elements: list[ElementBinding] = []
        for binding in state.elements:
            if binding.element_id in deleted:
                continue
            names = set(changes.fields.get(binding.element_id, ()))
            live = snapshot.item(binding.item_id) or snapshot.item_for(binding.element_id)
            if not names or live is None:
                elements.append(binding)
                continue
            theirs = live_fields(live, assets.get(live.media_item_id or ""), frozenset(names))
            fields = tuple((k, theirs.get(k, v) if k in names else v) for k, v in binding.fields)
            elements.append(replace(binding, fields=fields))
        return replace(state, elements=tuple(elements), sequence=state.sequence + 1)


@dataclass(slots=True)
class _Changes:
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    recorded: list[str] = field(default_factory=list)
    unrecorded: list[str] = field(default_factory=list)
    edits: list[HumanEdit] = field(default_factory=list)
    #: element id -> the fields that were handed to the builder.
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return bool(self.modified or self.deleted or self.created)


def _track_ids(timeline: BuiltTimeline) -> Mapping[str, str]:
    """``video:2`` -> the builder's track id: the same order the translation numbered them in."""
    found: dict[str, str] = {}
    for kind in (TrackKind.VIDEO, TrackKind.AUDIO):
        ordered = sorted(
            (t for t in timeline.tracks if t.kind is kind),
            key=lambda t: (t.index, t.lane, t.track_id),
        )
        for position, track in enumerate(ordered, start=1):
            found[f"{kind.value}:{position}"] = track.track_id
    return found


def _edit(
    binding: ElementBinding,
    live: LiveItem,
    moved: Mapping[str, str],
    timeline: BuiltTimeline,
    fps: Fraction,
    track_ids: Mapping[str, str],
) -> tuple[HumanEdit | None, set[str]]:
    """The builder's field changes for what a person changed, and the names it cannot hold."""
    clip_v = timeline.video_clip(binding.element_id)
    clip_a = timeline.audio_clip(binding.element_id)
    audio = clip_a is not None
    changes: list[tuple[str, str]] = []
    kept_back: set[str] = set()
    start = int(moved.get("start", dict(binding.fields).get("start", live.start)))
    duration = int(moved.get("duration", dict(binding.fields).get("duration", live.duration)))
    if "start" in moved or "duration" in moved:
        if audio and clip_a is not None:
            rate = clip_a.sample_rate
            changes += [
                ("start_sample", str(round(Fraction(start) / fps * rate))),
                ("end_sample", str(round(Fraction(start + duration) / fps * rate))),
            ]
        else:
            changes += [("start_frame", str(start)), ("end_frame", str(start + duration))]
    if "src_start" in moved or "duration" in moved:
        speed = clip_v.speed if clip_v else clip_a.speed if clip_a else Rational(1, 1)
        speed_one = speed == Rational(1, 1)
        if speed_one:
            first = int(
                moved.get("src_start", dict(binding.fields).get("src_start", live.left_offset))
            )
            file_fps = _file_fps(binding.element_id, timeline, fps)
            seconds_in = Fraction(first) / file_fps
            changes += [
                ("source_in", _text(seconds_in)),
                ("source_out", _text(seconds_in + Fraction(duration) / fps)),
            ]
        else:
            kept_back |= {"src_start"} & set(moved)
    if "enabled" in moved:
        changes.append(("enabled", "true" if moved["enabled"] == "1" else "false"))
    if "track" in moved:
        target = track_ids.get(moved["track"])
        if target is None:
            kept_back.add("track")
        else:
            changes.append(("track_id", target))
    if "prop:Opacity" in moved and not audio:
        changes.append(("opacity", f"{float(moved['prop:Opacity']) / 100.0:g}"))
    for name in moved:
        if name == "asset" or (name.startswith("prop:") and name != "prop:Opacity"):
            kept_back.add(name)
    if not changes:
        return None, kept_back
    return HumanEdit(
        EditKind.MODIFY, binding.element_id, tuple(changes), editor="resolve"
    ), kept_back


def _file_fps(element_id: str, timeline: BuiltTimeline, fps: Fraction) -> Fraction:
    clip = timeline.video_clip(element_id) or timeline.audio_clip(element_id)
    if clip is None or clip.source is None:
        return fps
    media = timeline.source(clip.source.source_id)
    if media is None or media.frame_rate is None:
        return fps
    return Fraction(media.frame_rate.numerator, media.frame_rate.denominator)


def _text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"
