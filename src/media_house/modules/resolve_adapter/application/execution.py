"""One execution of a built timeline in Resolve, phase by phase.

The order is the state machine of the report: project, settings, media, timeline, effects (what
scripting cannot do), verification. Every phase writes to the ledger what it did and what it read
back. The same comparison (``domain/reconcile.py``) serves a first build, an incremental update and
a retry after an interruption: what Resolve holds decides what is still to do, never what was
intended, so an operation is never repeated blindly.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.application.sources import SourceFile
from media_house.modules.resolve_adapter.domain.binding import (
    ConflictRecord,
    ElementBinding,
    MarkerBinding,
    MediaBinding,
    ProjectBinding,
    SyncState,
)
from media_house.modules.resolve_adapter.domain.capabilities import CapabilityMatrix, detect
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.environment import ResolveEnvironment
from media_house.modules.resolve_adapter.domain.errors import (
    ConfigurationNotApplied,
    ProjectConflict,
    ResolveCallFailed,
)
from media_house.modules.resolve_adapter.domain.facts import MediaFact, TimelineFacts
from media_house.modules.resolve_adapter.domain.live import (
    LiveItem,
    PlaceRequest,
    PoolItem,
    ProjectInfo,
    TimelineSnapshot,
)
from media_house.modules.resolve_adapter.domain.reconcile import (
    Action,
    Decision,
    MarkerAction,
    decide,
    decide_markers,
    live_fields,
    marker_digest,
)
from media_house.modules.resolve_adapter.domain.report import (
    AlternativeRecord,
    CheckRecord,
    MediaRecord,
    SettingRecord,
    SyncRecord,
)
from media_house.modules.resolve_adapter.domain.translate import (
    CRITICAL_SETTINGS,
    Expected,
    ExpectedItem,
    Gap,
    folder_for,
    translate,
)
from media_house.modules.resolve_adapter.domain.values import (
    TAG_PREFIX,
    Area,
    ExecutionPolicy,
    ExecutionState,
    OperationStatus,
    Resolution,
    Strategy,
    Verification,
)
from media_house.modules.resolve_adapter.domain.verify import (
    summarize,
    verify_duration,
    verify_items,
    verify_markers,
    verify_tracks,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import InvariantViolation

_DEFAULT_TRACK_NAME = re.compile(r"(Video|Audio) \d+")
_UNSAFE = re.compile(r'[\\/:*?"<>|]+')
_DEFAULT_PROJECT = "Untitled Project"


def norm(path: str) -> str:
    """Paths compared the way Resolve shows them (separators and case do not tell files apart)."""
    return path.replace("\\", "/").casefold()


def project_name_for(config: ResolveAdapterConfig, facts: TimelineFacts) -> str:
    name = config.project_name or facts.name
    return _UNSAFE.sub(" ", name).strip()[:120] or "Media House"


@dataclass(slots=True)
class Execution:
    backend: ResolveBackend
    config: ResolveAdapterConfig
    facts: TimelineFacts
    files: Mapping[str, SourceFile]
    previous: SyncState | None
    ledger: Ledger
    #: Where a subtitle file is written (it must persist: Resolve points at the path).
    export_dir: Path
    env: ResolveEnvironment | None = None
    matrix: CapabilityMatrix | None = None
    expected: Expected | None = None
    project_name: str = ""
    project_resolve_id: str = ""
    database: str = ""
    timeline_name: str = ""
    timeline_resolve_id: str = ""
    fresh: bool = False
    pool: dict[str, PoolItem] = field(default_factory=dict)  # asset id -> pool item
    media_records: dict[str, MediaRecord] = field(default_factory=dict)
    folders: dict[str, tuple[str, ...]] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    element_item: dict[str, str] = field(default_factory=dict)  # element id -> item id
    conflicts: list[ConflictRecord] = field(default_factory=list)
    marker_state: list[MarkerBinding] = field(default_factory=list)
    human_created: list[str] = field(default_factory=list)
    snapshot: TimelineSnapshot | None = None
    sequence: int = 0
    execution_id: str = ""
    markers_done: bool = False
    #: Elements whose clip is in Resolve without our tag (placed by a run that was interrupted).
    untagged: set[str] = field(default_factory=set)
    #: Called with the state worth keeping if the run is interrupted from here on.
    on_checkpoint: Callable[[SyncState], None] | None = None

    # --- helpers --------------------------------------------------------------------------------
    @property
    def policy(self) -> ExecutionPolicy:
        return self.config.policy

    def capabilities(self) -> CapabilityMatrix:
        if self.matrix is None:
            raise InvariantViolation("the capabilities are read before the project is opened")
        return self.matrix

    def refresh_capabilities(self) -> None:
        self.env = self.backend.probe()
        observed = set(self.ledger.observed) | {"connection.external_scripting"}
        if self.previous and self.previous.environment_key == self.env.key:
            observed |= set(self.previous.observed)
        self.matrix = detect(self.env, frozenset(observed))

    def run(self, execution_id: str, ctx: JobContext) -> SyncState:
        """All the phases, in the order of the report's states. Cancellation is honoured between
        phases; whatever was done by then is in the ledger."""
        self.execution_id = execution_id
        self.ledger.enter(ExecutionState.CONNECTING)
        self.refresh_capabilities()
        ctx.raise_if_cancelled()
        self.open_project()
        expected = translate(self.facts, self.config, self.capabilities())
        self.expected = expected
        ctx.raise_if_cancelled()
        self.configure(expected)
        ctx.raise_if_cancelled()
        self.import_media(expected)
        ctx.raise_if_cancelled()
        self.open_timeline(expected)
        self.build(expected)
        ctx.raise_if_cancelled()
        snapshot = self.verify(expected)
        self.ledger.enter(ExecutionState.READY_TO_RENDER)
        return self.new_state(execution_id, snapshot)

    # --- phase: project -------------------------------------------------------------------------
    def open_project(self) -> None:
        self.ledger.enter(ExecutionState.CONFIGURING_PROJECT)
        self.refresh_capabilities()
        bound = self.previous.project if self.previous else None
        name = bound.project_name if bound else project_name_for(self.config, self.facts)
        projects = self.backend.list_projects()
        current = self.backend.current_project()
        exists = name in projects
        if bound is not None and not exists:
            self.ledger.warn(
                f"the bound project {name!r} no longer exists in Resolve; a new one is created"
            )
            self.previous = None
            bound = None
        if bound is None and exists and not self.config.adopt_existing_project:
            raise ProjectConflict(
                f"a project named {name!r} already exists and was not made by Media House. "
                "Choose another name, or allow using an existing project"
            )
        already_open = current is not None and current.name == name
        if not already_open:
            self._leave_current(current)
            if exists:
                info = self.backend.open_project(name)
                action = "open"
            else:
                info = self.backend.create_project(name)
                action = "create"
            now = self.backend.current_project()
            ok = now is not None and now.name == name
            self.ledger.op(
                "project",
                Area.PROJECT,
                action,
                OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
                verification=Verification.VERIFIED if ok else Verification.FAILED,
                detail=f"project {name!r}",
                ref=info.unique_id or name,
            )
            if not ok:
                raise ResolveCallFailed(f"{action} the project {name!r}", "it is not the open one")
            self.ledger.confirm("project.create" if action == "create" else "project.open")
            self.project_resolve_id = info.unique_id or ""
            self.database = info.database or ""
        elif current is not None:
            self.ledger.op(
                "project",
                Area.PROJECT,
                "use the open project",
                OperationStatus.SKIPPED,
                verification=Verification.VERIFIED,
                detail=f"project {name!r} was already open",
                ref=current.unique_id or name,
            )
            self.project_resolve_id = current.unique_id or ""
            self.database = current.database or ""
        self.project_name = name
        self.refresh_capabilities()

    def _leave_current(self, current: ProjectInfo | None) -> None:
        """Another project is open: only with permission, and saved first (nothing is lost)."""
        if current is None or current.name == _DEFAULT_PROJECT:
            return
        if not self.config.allow_project_switch:
            raise ProjectConflict(
                f"another project, {current.name!r}, is open in Resolve. Close it, or allow "
                "switching projects (it is saved first)"
            )
        self.backend.save_project()
        self.ledger.op(
            "project",
            Area.PROJECT,
            "save the project that was open",
            OperationStatus.EXECUTED,
            detail=f"{current.name!r} saved before switching",
        )

    # --- phase: settings ------------------------------------------------------------------------
    def configure(self, expected: Expected) -> None:
        wanted = dict(expected.settings)
        applied = self.backend.set_settings(wanted)
        actual = self.backend.get_settings(sorted(wanted))
        problems: list[str] = []
        for name, value in sorted(wanted.items()):
            now = actual.get(name, "")
            same = _same_setting(name, value, now)
            critical = name in CRITICAL_SETTINGS
            self.ledger.settings.append(
                SettingRecord(name, value, now, applied.get(name, False) and same, critical)
            )
            if not same and critical:
                problems.append(f"{name}: wanted {value}, Resolve has {now or 'nothing'}")
            elif not same:
                self.ledger.warn(f"setting {name}: wanted {value}, Resolve has {now or 'nothing'}")
        ok = not problems
        self.ledger.op(
            "timeline",
            Area.SETTING,
            "configure the project",
            OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
            strategy=Strategy.PROJECT_SETTING,
            verification=Verification.VERIFIED if ok else Verification.FAILED,
            detail=f"{len(wanted)} settings written and read back",
        )
        if not ok:
            raise ConfigurationNotApplied(problems)
        self.ledger.confirm("project.settings")

    # --- phase: media ---------------------------------------------------------------------------
    def import_media(self, expected: Expected) -> None:
        self.ledger.enter(ExecutionState.IMPORTING_MEDIA)
        needed = sorted({i.asset_id for i in expected.items})
        facts_of = {m.asset_id: m for m in self.facts.media}
        pool = self.backend.pool_items()
        by_path = {norm(p.path): p for p in pool}
        pending: dict[tuple[str, ...], list[tuple[str, Path]]] = {}
        for asset_id in needed:
            media = facts_of[asset_id]
            file = self.files[asset_id]
            folder = folder_for(media, self.config.root_folder)
            self.folders[asset_id] = folder
            if file.path is None or not file.exists:
                self.ledger.op(
                    asset_id,
                    Area.MEDIA,
                    "import",
                    OperationStatus.FAILED,
                    verification=Verification.FAILED,
                    detail=f"{media.name} is not available, so nothing that plays it can be placed",
                )
                self.ledger.fail(f"{media.name} is not available")
                self.media_records[asset_id] = MediaRecord(
                    asset_id, media.name, "", "", "unavailable", folder, "none"
                )
                continue
            item = by_path.get(norm(str(file.path)))
            if item is None:
                pending.setdefault(folder, []).append((asset_id, file.path))
                continue
            if item.offline:
                item = self._relink(asset_id, item, file.path)
            self.pool[asset_id] = item
            self._media_op(asset_id, media, item, "reused", file)
        for folder, entries in sorted(pending.items()):
            self.backend.ensure_folder(folder)
            self.ledger.confirm("media.folders")
            imported = self.backend.import_media([p for _, p in entries], folder)
            got = {norm(i.path): i for i in imported}
            for asset_id, path in entries:
                media = facts_of[asset_id]
                item = got.get(norm(str(path)))
                if item is None:
                    self.ledger.op(
                        asset_id,
                        Area.MEDIA,
                        "import",
                        OperationStatus.FAILED,
                        verification=Verification.FAILED,
                        detail=f"Resolve did not import {media.name}",
                    )
                    self.ledger.fail(f"{media.name} could not be imported")
                    self.media_records[asset_id] = MediaRecord(
                        asset_id, media.name, "", str(path), "missing", folder, "none"
                    )
                    continue
                self.pool[asset_id] = item
                self._media_op(asset_id, media, item, "imported", self.files[asset_id])
        if needed:
            self.ledger.confirm("media.import")

    def _relink(self, asset_id: str, item: PoolItem, path: Path) -> PoolItem:
        self.backend.relink([item.item_id], path.parent)
        after = next((p for p in self.backend.pool_items() if p.item_id == item.item_id), item)
        ok = not after.offline
        self.ledger.op(
            asset_id,
            Area.MEDIA,
            "relink",
            OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
            verification=Verification.VERIFIED if ok else Verification.FAILED,
            detail="the file was offline",
            ref=item.item_id,
        )
        if ok:
            self.ledger.confirm("media.relink")
        return after

    def _media_op(
        self, asset_id: str, media: MediaFact, item: PoolItem, status: str, file: SourceFile
    ) -> None:
        proxy = "not_requested"
        if self.config.link_proxies and media.media_type == "video":
            proxy = "none"
            if file.proxy_path is not None:
                proxy = item.proxy
                if item.proxy != "linked":
                    proxy = (
                        "linked"
                        if self.backend.link_proxy(item.item_id, file.proxy_path)
                        else "none"
                    )
                    self.ledger.op(
                        asset_id,
                        Area.PROXY,
                        "link proxy",
                        OperationStatus.EXECUTED if proxy == "linked" else OperationStatus.FAILED,
                        verification=Verification.NOT_VERIFIABLE,
                        detail="whether the proxy is active is a playback setting",
                        ref=item.item_id,
                    )
        ok = not item.offline
        self.ledger.op(
            asset_id,
            Area.MEDIA,
            status if status == "imported" else "reuse",
            OperationStatus.EXECUTED if status == "imported" else OperationStatus.SKIPPED,
            verification=Verification.VERIFIED if ok else Verification.FAILED,
            detail=f"{media.name} · {'/'.join(self.folders[asset_id])}",
            ref=item.item_id,
        )
        self.media_records[asset_id] = MediaRecord(
            asset_id=asset_id,
            name=media.name,
            pool_item_id=item.item_id,
            path=item.path,
            status=status if ok else "offline",
            folder=self.folders[asset_id],
            proxy=proxy,
            role=",".join(media.roles),
        )

    # --- phase: timeline ------------------------------------------------------------------------
    def open_timeline(self, expected: Expected) -> None:
        self.ledger.enter(ExecutionState.BUILDING_TIMELINE)
        bound = self.previous.project if self.previous else None
        existing = {t.name: t for t in self.backend.list_timelines()}
        name = self.facts.name
        self.fresh = bound is None or self.policy in {
            ExecutionPolicy.CREATE_NEW,
            ExecutionPolicy.FULL_REBUILD_WITH_BACKUP,
        }
        if bound is not None and bound.timeline_name not in existing and not self.fresh:
            self.ledger.warn(
                f"the timeline {bound.timeline_name!r} is gone from Resolve; a new one is built"
            )
            self.fresh = True
        if (
            self.fresh
            and bound is not None
            and self.policy is ExecutionPolicy.FULL_REBUILD_WITH_BACKUP
        ):
            name = bound.timeline_name
            if name in existing:
                backup = _free_name(f"{name} (before rebuild)", existing)
                self.backend.duplicate_timeline(name, backup)
                self.backend.rename_timeline(name, _free_name(f"{name} (replaced)", existing))
                self.ledger.op(
                    "timeline",
                    Area.PROJECT,
                    "back up the timeline",
                    OperationStatus.EXECUTED,
                    detail=f"copy {backup!r}; the old timeline was renamed, not deleted",
                )
                self.ledger.confirm("timeline.duplicate")
                existing = {t.name: t for t in self.backend.list_timelines()}
        if self.fresh:
            if name in existing:
                name = _free_name(f"{name} v{self.facts.build_version}", existing)
            info = self.backend.create_timeline(name, self.config.quirks.timeline_start_timecode)
            ok = any(t.name == name for t in self.backend.list_timelines())
            self.ledger.op(
                "timeline",
                Area.PROJECT,
                "create the timeline",
                OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
                verification=Verification.VERIFIED if ok else Verification.FAILED,
                detail=name,
                ref=info.unique_id or name,
            )
            if not ok:
                raise ResolveCallFailed("create the timeline", name)
            self.ledger.confirm("timeline.create")
            self.timeline_resolve_id = info.unique_id or ""
        elif bound is not None:
            info = self.backend.open_timeline(bound.timeline_name)
            name = bound.timeline_name
            self.timeline_resolve_id = info.unique_id or bound.timeline_resolve_id or ""
            self.ledger.op(
                "timeline",
                Area.PROJECT,
                "open the timeline",
                OperationStatus.SKIPPED,
                verification=Verification.VERIFIED,
                detail=name,
                ref=self.timeline_resolve_id,
            )
        self.timeline_name = name
        self.refresh_capabilities()
        self._tracks(expected)
        self.checkpoint()

    def _tracks(self, expected: Expected) -> None:
        info = self.backend.list_timelines()
        have = next((t for t in info if t.name == self.timeline_name), None)
        before_v = have.video_tracks if have else 1
        before_a = have.audio_tracks if have else 1
        want_v, want_a = expected.track_count("video"), expected.track_count("audio")
        layout = _audio_layout(self.facts.settings.audio_channels)
        self.backend.ensure_tracks(want_v, want_a, layout)
        now = self.backend.read_timeline()
        for track in expected.tracks:
            created = track.index > (before_v if track.kind == "video" else before_a)
            live = now.track(track.kind, track.index)
            # A track a person renamed keeps their name; Resolve's own names ("Video 1") are ours.
            unnamed = live is None or not live.name or _DEFAULT_TRACK_NAME.fullmatch(live.name)
            if track.name and (created or unnamed):
                self.backend.name_track(track.kind, track.index, track.name)
        snapshot = self.backend.read_timeline()
        ok = snapshot.info.video_tracks >= want_v and snapshot.info.audio_tracks >= want_a
        self.ledger.op(
            "timeline",
            Area.TRACK,
            "create the tracks",
            OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
            verification=Verification.VERIFIED if ok else Verification.FAILED,
            detail=f"{want_v} video, {want_a} audio ({layout})",
        )
        if ok:
            self.ledger.confirm("timeline.tracks", "timeline.track_names")

    def build(self, expected: Expected) -> None:
        snapshot = self.backend.read_timeline()
        asset_of_media = self._asset_of_media()
        wanted = {i.element_id: i for i in expected.items}
        base = {
            e.element_id: e
            for e in (self.previous.elements if self.previous and not self.fresh else ())
        }
        policy = ExecutionPolicy.UPDATE_MANAGED_ELEMENTS if self.fresh else self.policy
        used: set[str] = set()
        decisions: dict[str, Decision] = {}
        for element_id in sorted(set(wanted) | set(base)):
            item = wanted.get(element_id)
            live = None
            if not self.fresh:
                bound = base.get(element_id)
                live = snapshot.item_for(element_id) or (
                    snapshot.item(bound.item_id) if bound else None
                )
                if live is None and item is not None and bound is None:
                    live = self._adopt(snapshot, item, used, asset_of_media)
            if live is not None:
                used.add(live.item_id)
                if not live.tag and item is not None:
                    self.untagged.add(element_id)
            binding = base.get(element_id)
            if binding is None and live is not None and item is not None:
                # Placed by a run that never recorded it: what is there now is the starting point.
                binding = self._adopted(item, live, asset_of_media)
            decisions[element_id] = decide(element_id, item, binding, live, asset_of_media, policy)
            if live is not None:
                self.element_item[element_id] = live.item_id
        self.decisions = decisions
        self.human_created = [
            i.item_id
            for i in snapshot.items
            if i.item_id not in used
            and not any(b.item_id == i.item_id for b in base.values())
            and not self.fresh
        ]
        for d in decisions.values():
            if d.conflicts:
                self.conflicts.append(
                    ConflictRecord(
                        d.element_id,
                        d.conflicts,
                        d.origin,
                        d.resolution or Resolution.OPEN,
                        d.note,
                    )
                )
        self._apply(expected, wanted, snapshot)

    @staticmethod
    def _adopted(
        item: ExpectedItem, live: LiveItem, asset_of_media: Mapping[str, str]
    ) -> ElementBinding:
        names = frozenset(k for k, _ in item.fields)
        found = live_fields(live, asset_of_media.get(live.media_item_id or ""), names)
        return ElementBinding(
            item.element_id, item.kind, live.item_id, tuple(sorted(found.items())), ""
        )

    def _adopt(
        self,
        snapshot: TimelineSnapshot,
        item: ExpectedItem,
        used: set[str],
        asset_of_media: Mapping[str, str],
    ) -> LiveItem | None:
        """After an interruption a clip may be placed but not yet tagged or bound: take it back
        (same track, place, length and file) instead of placing a second one."""
        for live in snapshot.items:
            if (
                not live.tag
                and live.item_id not in used
                and live.track_kind == item.kind
                and live.track_index == item.track_index
                and live.start == item.start
                and live.duration == item.duration
                and asset_of_media.get(live.media_item_id or "") == item.asset_id
            ):
                self.ledger.note(f"{item.element_id}: found an untagged clip already in place")
                return live
        return None

    def _asset_of_media(self) -> dict[str, str]:
        found = {item.item_id: asset for asset, item in self.pool.items()}
        if self.previous:
            for m in self.previous.media:
                found.setdefault(m.pool_item_id, m.asset_id)
        return found

    def _apply(
        self, expected: Expected, wanted: Mapping[str, ExpectedItem], snapshot: TimelineSnapshot
    ) -> None:
        by_action: dict[Action, list[str]] = {}
        for element_id, d in self.decisions.items():
            by_action.setdefault(d.action, []).append(element_id)

        # 1. remove what must go (changed in place by replacement, or no longer wanted)
        removals = [
            self.element_item[e]
            for e in by_action.get(Action.DELETE, []) + by_action.get(Action.REPLACE, [])
            if e in self.element_item
        ]
        if removals:
            self.backend.delete_items(removals)
            self.ledger.confirm("clip.delete")
        for e in by_action.get(Action.DELETE, []):
            self.ledger.op(
                e,
                Area.CLIP,
                "remove",
                OperationStatus.EXECUTED,
                detail="Media House no longer wants it",
                ref=self.element_item.get(e, ""),
            )
            self.element_item.pop(e, None)

        # 2. place what is new or has to be placed again
        place = sorted(
            by_action.get(Action.CREATE, []) + by_action.get(Action.REPLACE, []),
            key=lambda e: (wanted[e].start, e),
        )
        requests: list[PlaceRequest] = []
        for e in place:
            request = self._request(wanted[e], self.decisions[e])
            if request is None:
                self.ledger.op(
                    e,
                    Area.CLIP,
                    "place",
                    OperationStatus.FAILED,
                    verification=Verification.FAILED,
                    detail="its file is not in the media pool",
                )
                self.ledger.fail(f"{e}: its file could not be imported, so it was not placed")
                continue
            requests.append(request)
        placed = self.backend.place(requests) if requests else ()
        placed_ids = {p.element_id: p.item_id for p in placed}
        for r in requests:
            item_id = placed_ids.get(r.element_id)
            area = Area.CLIP if wanted[r.element_id].kind == "video" else Area.AUDIO
            if item_id is None:
                self.ledger.op(
                    r.element_id,
                    area,
                    "place",
                    OperationStatus.FAILED,
                    verification=Verification.FAILED,
                    detail="Resolve did not place the clip",
                )
                self.ledger.fail(f"{r.element_id} was not placed")
                continue
            self.element_item[r.element_id] = item_id
            self.ledger.op(
                r.element_id,
                area,
                "place",
                OperationStatus.EXECUTED,
                ref=item_id,
                detail=f"{wanted[r.element_id].name} · frame {r.record_frame}",
            )
        if requests:
            self.ledger.confirm("clip.place")

        # 3. properties and state of what was placed or updated; tags
        self.ledger.enter(ExecutionState.APPLYING_EFFECTS)
        for e in place + by_action.get(Action.UPDATE, []):
            item_id = self.element_item.get(e)
            if item_id is None:
                continue
            self._properties(wanted[e], self.decisions[e], item_id)
            if e in place:
                self._tag(wanted[e], item_id)
        for e in sorted(self.untagged):
            item_id = self.element_item.get(e)
            if item_id and e in wanted and self.decisions[e].action is not Action.DELETE:
                self._tag(wanted[e], item_id)
        self._link(wanted, set(place))
        self.checkpoint()

        for e in by_action.get(Action.KEEP, []):
            self.ledger.op(
                e,
                Area.CLIP if wanted[e].kind == "video" else Area.AUDIO,
                "keep",
                OperationStatus.SKIPPED,
                detail="already as wanted",
                ref=self.element_item.get(e, ""),
            )
        for e in by_action.get(Action.LEAVE, []):
            d = self.decisions[e]
            self.ledger.op(
                e,
                Area.SYNC,
                "leave",
                OperationStatus.SKIPPED,
                verification=Verification.NOT_CHECKED,
                detail=d.note or "a person's version is kept",
                ref=self.element_item.get(e, ""),
            )
        self._markers(expected)
        self._gaps(expected)
        self._subtitles(expected)

    def _tag(self, item: ExpectedItem, item_id: str) -> None:
        """A marker on the clip names the timeline element it carries out, so it is recognised
        after a person moved it (Resolve's own id is kept in the link as well)."""
        if not self.config.tag_items:
            return
        tagged = self.backend.add_marker(
            item_id, 0, "Cream", item.name[:60], f"Media House · {item.element_id}", 1,
            f"{TAG_PREFIX}clip:{item.element_id}",
        )  # fmt: skip
        if not tagged:
            self.ledger.warn(
                f"{item.element_id}: the clip could not be tagged; its id is kept in the link"
            )

    def _request(self, item: ExpectedItem, decision: Decision) -> PlaceRequest | None:
        final = dict(decision.final)
        pool = self.pool.get(final.get("asset", item.asset_id))
        if pool is None:
            return None
        kind, _, index = final.get("track", f"{item.kind}:{item.track_index}").partition(":")
        duration = int(final.get("duration", item.duration))
        src_start = int(final.get("src_start", item.src_start))
        frames = item.src_frames if duration == item.duration else duration
        tail = 1 if self.config.quirks.end_frame_inclusive else 0
        return PlaceRequest(
            element_id=item.element_id,
            pool_item_id=pool.item_id,
            media_type=1 if kind == "video" else 2,
            track_index=int(index),
            record_frame=int(final.get("start", item.start)),
            start_frame=src_start,
            end_frame=src_start + frames - tail,
        )

    def _properties(self, item: ExpectedItem, decision: Decision, item_id: str) -> None:
        final = dict(decision.final)
        written = dict(decision.write) if decision.action is Action.UPDATE else final
        props = {k[5:]: float(v) for k, v in written.items() if k.startswith("prop:")}
        enabled = None
        if "enabled" in written and (decision.action is Action.UPDATE or written["enabled"] == "0"):
            enabled = written["enabled"] == "1"
        if not props and enabled is None:
            return
        area = Area.CLIP if item.kind == "video" else Area.AUDIO
        result = self.backend.set_item(item_id, props, enabled)
        failed = sorted(k for k, ok in result.items() if not ok)
        self.ledger.op(
            item.element_id,
            area,
            "set properties",
            OperationStatus.PARTIAL if failed else OperationStatus.EXECUTED,
            detail=("not accepted: " + ", ".join(failed)) if failed else ", ".join(sorted(props)),
            ref=item_id,
        )
        if props and not failed:
            self.ledger.confirm("clip.transform")
            if "Opacity" in props:
                self.ledger.confirm("clip.opacity")
        if enabled is not None and "enabled" not in failed:
            self.ledger.confirm("clip.enable")

    def _link(self, wanted: Mapping[str, ExpectedItem], created: set[str]) -> None:
        done: set[frozenset[str]] = set()
        for element_id in sorted(created):
            partner = wanted[element_id].link
            if partner is None or partner not in self.element_item:
                continue
            pair = frozenset({element_id, partner})
            if pair in done:
                continue
            done.add(pair)
            ok = self.backend.link_items(
                [self.element_item[element_id], self.element_item[partner]]
            )
            self.ledger.op(
                element_id,
                Area.AUDIO,
                "link picture and sound",
                OperationStatus.EXECUTED if ok else OperationStatus.PARTIAL,
                verification=Verification.NOT_VERIFIABLE,
                detail=f"with {partner}; the link cannot be read back",
            )
            if ok:
                self.ledger.confirm("clip.link")

    # --- markers, gaps, captions ----------------------------------------------------------------
    def _markers(self, expected: Expected) -> None:
        snapshot = self.backend.read_timeline()
        base = self.previous.markers if self.previous and not self.fresh else ()
        decisions = decide_markers(
            expected.markers,
            base if not self.fresh else (),
            snapshot.markers if not self.fresh else (),
        )
        by_custom = {m.custom: m for m in expected.markers}
        for d in decisions:
            marker = by_custom.get(d.custom)
            if d.action in {MarkerAction.REMOVE, MarkerAction.REPLACE}:
                self.backend.remove_marker("timeline", d.custom)
            if d.action in {MarkerAction.ADD, MarkerAction.REPLACE} and marker is not None:
                ok = self.backend.add_marker(
                    "timeline",
                    marker.frame,
                    marker.color,
                    marker.name,
                    marker.note,
                    marker.duration,
                    marker.custom,
                )
                self.ledger.op(
                    marker.custom,
                    Area.MARKER,
                    "add marker",
                    OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
                    detail=f"{marker.name} · frame {marker.frame}",
                )
        self.ledger.confirm("marker.timeline", "marker.custom_data")
        self.markers_done = True
        self.marker_state = [
            MarkerBinding(m.custom, marker_digest(m))
            for m in expected.markers
            if not any(d.custom == m.custom and d.action is MarkerAction.LEAVE for d in decisions)
        ] + [
            b
            for b in (self.previous.markers if self.previous else ())
            if any(d.custom == b.custom and d.action is MarkerAction.LEAVE for d in decisions)
        ]

    def _gaps(self, expected: Expected) -> None:
        """What scripting cannot do: reported with the alternative, never replaced."""
        for gap in expected.gaps:
            status = (
                OperationStatus.PARTIAL
                if gap.strategy is Strategy.MANUAL_MARKER
                else OperationStatus.UNSUPPORTED
            )
            self.ledger.op(
                gap.element_id,
                _area_of(gap),
                gap.operation,
                status,
                strategy=gap.strategy,
                verification=Verification.NOT_VERIFIABLE,
                detail=gap.detail,
            )
            self.ledger.alternatives.append(
                AlternativeRecord(
                    gap.element_id,
                    gap.operation,
                    gap.strategy,
                    gap.detail,
                    gap.alternatives,
                    gap.owner,
                )
            )

    def _subtitles(self, expected: Expected) -> None:
        subs = expected.subtitles
        if subs is None:
            return
        target = self.export_dir / "captions.srt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(subs.srt, encoding="utf-8")
        folder = (self.config.root_folder, "Captions")
        self.backend.ensure_folder(folder)
        got = self.backend.import_media([target], folder)
        found = any(norm(i.path) == norm(str(target)) for i in got)
        self.ledger.op(
            "captions",
            Area.CAPTION,
            "import the caption cues",
            OperationStatus.PARTIAL if found else OperationStatus.FAILED,
            strategy=Strategy.IMPORTED_FILE,
            verification=Verification.PARTIALLY_VERIFIED if found else Verification.FAILED,
            detail=(
                f"{len(subs.cues)} cues are in the media pool as a subtitle file; drag it onto a "
                "subtitle track to edit the words in Resolve"
                if found
                else "Resolve did not import the subtitle file"
            ),
        )
        if found:
            self.ledger.confirm("caption.subtitle_import")

    # --- phase: verification and the new binding ------------------------------------------------
    def verify(self, expected: Expected) -> TimelineSnapshot:
        self.ledger.enter(ExecutionState.VERIFYING_PROJECT)
        if self.config.save:
            self.backend.save_project()
            self.ledger.op(
                "project",
                Area.PROJECT,
                "save",
                OperationStatus.EXECUTED,
                detail="saved after the timeline was built",
            )
            self.ledger.confirm("project.save")
        snapshot = self.backend.read_timeline()
        self.snapshot = snapshot
        self.ledger.confirm("snapshot.read")
        results = verify_items(self.decisions, snapshot, self.element_item, self._asset_of_media())
        for element_id, (state, detail) in results.items():
            item = next(
                (
                    i
                    for i in (self.expected.items if self.expected else ())
                    if i.element_id == element_id
                ),
                None,
            )
            area = Area.CLIP if item is None or item.kind == "video" else Area.AUDIO
            self.ledger.verify(element_id, area, state, detail)
        checks = self.ledger.checks
        checks.append(summarize(Area.CLIP, "placed clips", results))
        checks.extend(verify_tracks(expected, snapshot))
        checks.append(verify_markers(expected.markers, snapshot))
        end = max((self._final_end(e) for e in self.decisions if e in self.element_item), default=0)
        checks.append(verify_duration(end, snapshot))
        checks.append(self._media_check())
        checks.extend(self._identity_checks())
        if any(c.state is Verification.VERIFIED for c in checks if c.area is Area.CLIP):
            self.ledger.confirm("clip.place")
        return snapshot

    def _final_end(self, element_id: str) -> int:
        d = self.decisions[element_id]
        if d.action is Action.LEAVE:
            live = self.snapshot.item(self.element_item[element_id]) if self.snapshot else None
            return live.end if live else 0
        final = dict(d.final)
        return int(final.get("start", 0)) + int(final.get("duration", 0))

    def _media_check(self) -> CheckRecord:
        offline = [
            r.name
            for r in self.media_records.values()
            if r.status in {"offline", "missing", "unavailable"}
        ]
        if offline:
            return CheckRecord(
                Area.MEDIA, "media online", Verification.FAILED, ", ".join(offline[:3])
            )
        return CheckRecord(
            Area.MEDIA,
            "media online",
            Verification.VERIFIED,
            f"{len(self.media_records)} files online",
        )

    def _identity_checks(self) -> list[CheckRecord]:
        identity = self.facts.identity
        missing = [f.family for f in identity.fonts if not f.available]
        found = [
            CheckRecord(
                Area.IDENTITY,
                "creator identity",
                Verification.VERIFIED,
                f"{identity.identity_id} v{identity.identity_version} · style "
                f"{identity.style_fingerprint[:12]} · {len(identity.presets)} design choices",
            )
        ]
        if missing:
            found.append(
                CheckRecord(
                    Area.IDENTITY,
                    "fonts",
                    Verification.FAILED,
                    "fonts the identity needs are missing: " + ", ".join(sorted(missing)),
                )
            )
            self.ledger.warn(
                "fonts missing for the creator identity: " + ", ".join(sorted(missing))
            )
        elif identity.fonts:
            found.append(
                CheckRecord(
                    Area.IDENTITY,
                    "fonts",
                    Verification.NOT_VERIFIABLE,
                    "the scripting API cannot list the fonts installed for Resolve",
                )
            )
        return found

    def new_state(self, execution_id: str, snapshot: TimelineSnapshot) -> SyncState:
        """What the next run compares against: the state written and read back, per element."""
        if self.expected is None or self.env is None:
            raise InvariantViolation("a state is written after the timeline was translated")
        old = {
            e.element_id: e
            for e in (self.previous.elements if self.previous and not self.fresh else ())
        }
        elements: list[ElementBinding] = list(old.values()) if not self.decisions else []
        for element_id, d in sorted(self.decisions.items()):
            item_id = self.element_item.get(element_id)
            if d.action is Action.DELETE or item_id is None:
                continue
            wanted = self.expected.item(element_id)
            if d.action in {Action.LEAVE}:
                if element_id in old:
                    elements.append(replace(old[element_id], item_id=item_id))
                continue
            live = snapshot.item(item_id)
            if live is None or wanted is None:
                continue
            elements.append(
                ElementBinding(element_id, wanted.kind, item_id, d.final, wanted.digest)
            )
        media = tuple(
            MediaBinding(
                asset_id=a,
                pool_item_id=r.pool_item_id,
                path=r.path,
                folder=r.folder,
                checksum=self._checksum(a),
                proxy_path=None,
            )
            for a, r in sorted(self.media_records.items())
            if r.pool_item_id
        )
        project = ProjectBinding(
            anchor_asset_id=self.facts.anchor_asset_id,
            timeline_id=self.facts.timeline_id,
            project_name=self.project_name,
            project_id=self.project_resolve_id or None,
            database=self.database or None,
            timeline_name=self.timeline_name,
            timeline_resolve_id=self.timeline_resolve_id or None,
        )
        observed = sorted(
            set(self.ledger.observed)
            | {"connection.external_scripting"}
            | set(
                self.previous.observed
                if self.previous and self.previous.environment_key == self.env.key
                else ()
            )
        )
        return SyncState(
            project=project,
            media=media,
            elements=tuple(elements),
            markers=tuple(self.marker_state)
            if self.markers_done
            else (self.previous.markers if self.previous and not self.fresh else ()),
            conflicts=tuple(c for c in self.conflicts if c.resolution is Resolution.OPEN),
            build_hash=self.facts.build_hash,
            build_version=self.facts.build_version,
            sequence=(self.previous.sequence if self.previous else 0) + self._next_sequence(),
            environment_key=self.env.key,
            observed=tuple(observed),
            last_execution_id=execution_id,
        )

    def _checksum(self, asset_id: str) -> str | None:
        media = next((m for m in self.facts.media if m.asset_id == asset_id), None)
        return media.checksum if media else None

    def _next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def checkpoint(self) -> None:
        """Keep what is true now, so an interruption from here on loses nothing that was done."""
        if self.on_checkpoint is None or self.expected is None or self.env is None:
            return
        self.on_checkpoint(self.new_state(self.execution_id, self.backend.read_timeline()))

    def sync_record(self) -> SyncRecord:
        modified = sorted(e for e, d in self.decisions.items() if d.changed_by_person)
        deleted = sorted(
            e
            for e, d in self.decisions.items()
            if d.action is Action.LEAVE and "deleted in Resolve" in d.note
        )
        status = "first_build" if self.previous is None else "in_sync"
        if modified or deleted or self.human_created:
            status = "changes_found"
        if any(c.resolution is Resolution.OPEN for c in self.conflicts):
            status = "conflicts_open"
        return SyncRecord(
            status=status,
            human_modified=tuple(modified),
            human_deleted=tuple(deleted),
            human_created=tuple(sorted(self.human_created)),
            conflicts=tuple(self.conflicts),
            detail="",
        )


# --- small helpers --------------------------------------------------------------------------------


def _same_setting(name: str, wanted: str, actual: str) -> bool:
    if name in {"timelineFrameRate", "timelinePlaybackFrameRate"}:
        try:
            return abs(float(wanted) - float(actual.split()[0])) < 0.001
        except (ValueError, IndexError):
            return False
    return wanted == actual


def _free_name(base: str, taken: Mapping[str, object]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base} ({n})" in taken:
        n += 1
    return f"{base} ({n})"


def _audio_layout(channels: str) -> str:
    text = channels.lower()
    return {"mono": "mono", "stereo": "stereo", "5.1": "5.1", "7.1": "7.1"}.get(text, "stereo")


def _area_of(gap: Gap) -> Area:
    if gap.operation.startswith("transition"):
        return Area.TRANSITION
    if gap.operation.startswith("effect") or gap.operation.startswith("clip."):
        return Area.EFFECT
    if gap.operation.startswith("audio"):
        return Area.AUDIO
    return Area.SETTING
