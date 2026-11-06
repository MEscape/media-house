"""The adapter that drives DaVinci Resolve through its scripting API.

This is the only file that knows Resolve's objects (``Resolve``, ``ProjectManager``, ``Project``,
``MediaPool``, ``Timeline``, ``TimelineItem``). Everything it returns is a plain record from
``domain/live.py``. A call that answers ``False`` / ``None`` is raised as ``ResolveCallFailed``; a
call that fails while Resolve no longer answers is ``ResolveConnectionLost``. It never assumes a
failed call changed nothing: the caller reads the state again.

Written from the published scripting documentation (Resolve 18/19). It was NOT run against an
installed Resolve when it was written; the unit tests drive it with a faithful stand-in of the
documented object tree (``tests/support/resolve_fakes.py``). Every operation it offers is
therefore ``NOT_VERIFIED`` in the capability matrix until a live run reads it back.
"""

import functools
import inspect
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Concatenate, Protocol

from media_house.modules.resolve_adapter.domain.environment import (
    ConnectionStatus,
    ResolveEnvironment,
    ResolveVersion,
)
from media_house.modules.resolve_adapter.domain.errors import (
    ResolveAdapterError,
    ResolveCallFailed,
    ResolveConnectionLost,
)
from media_house.modules.resolve_adapter.domain.live import (
    LiveItem,
    LiveMarker,
    LiveTrack,
    Placed,
    PlaceRequest,
    PoolItem,
    ProjectInfo,
    RenderOptions,
    RenderStatus,
    TimelineInfo,
    TimelineSnapshot,
)
from media_house.modules.resolve_adapter.domain.render import SettingValue
from media_house.modules.resolve_adapter.domain.values import TAG_PREFIX, ConnectionState
from media_house.modules.resolve_adapter.infrastructure.install import (
    ProcessProbe,
    ResolveInstall,
    ResolveLoader,
)

type Raw = Any

#: The classes whose methods are listed for the capability matrix.
_PROBED = (
    "Resolve",
    "ProjectManager",
    "Project",
    "MediaPool",
    "MediaPoolItem",
    "Timeline",
    "TimelineItem",
)

#: The item properties that are read back (everything else stays unknown).
_PROPERTIES = (
    "Pan",
    "Tilt",
    "ZoomX",
    "ZoomY",
    "RotationAngle",
    "Opacity",
    "CropLeft",
    "CropRight",
    "CropTop",
    "CropBottom",
)

_TAG = f"{TAG_PREFIX}clip:"


class _Alive(Protocol):
    def alive(self) -> bool: ...


def _guarded[S: _Alive, **P, R](
    fn: Callable[Concatenate[S, P], R],
) -> Callable[Concatenate[S, P], R]:
    """Whatever a vendor call raises becomes a typed error: Resolve gone is a lost connection
    (what it had done is unknown), anything else is a failed call."""

    @functools.wraps(fn)
    def inner(owner: S, /, *args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(owner, *args, **kwargs)
        except ResolveAdapterError:
            raise
        except Exception as exc:
            operation = fn.__name__.replace("_", " ")
            if not owner.alive():
                raise ResolveConnectionLost(operation) from exc
            raise ResolveCallFailed(operation, str(exc)) from exc

    return inner


class ScriptingResolveBackend:
    def __init__(
        self,
        install: ResolveInstall,
        probe: ProcessProbe,
        loader: ResolveLoader,
        *,
        minimum_version: tuple[int, ...] = (18, 0),
    ) -> None:
        self._install = install
        self._probe = probe
        self._loader = loader
        self._minimum = minimum_version
        self._lock = threading.RLock()
        self._resolve: Raw = None
        self._pool_cache: dict[str, Raw] = {}

    # --- connection -------------------------------------------------------------------------
    def exclusive(self) -> AbstractContextManager[None]:
        return self._exclusive()

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._lock:
            yield

    def connect(self) -> ConnectionStatus:
        installed = self._install.installed
        running = self._probe.running()
        if not installed and not running:
            return ConnectionStatus(
                ConnectionState.NOT_INSTALLED,
                f"DaVinci Resolve's scripting library was not found at {self._install.script_lib}",
                installed=False,
                running=False,
            )
        if running is False:
            return ConnectionStatus(
                ConnectionState.NOT_RUNNING,
                "DaVinci Resolve is installed but not running. Start it, then retry",
                installed=installed,
                running=False,
            )
        try:
            resolve = self._loader(self._install)
        except (ImportError, OSError) as exc:
            return ConnectionStatus(
                ConnectionState.API_UNAVAILABLE,
                f"Resolve's scripting library could not be loaded: {exc}. A Python version it "
                "does not support is a common cause",
                installed=installed,
                running=bool(running),
            )
        except Exception as exc:  # noqa: BLE001 - a vendor library: anything it raises is a failed connection
            return ConnectionStatus(
                ConnectionState.CONNECTION_FAILED,
                f"Resolve's scripting library failed: {exc}",
                installed=installed,
                running=bool(running),
            )
        if resolve is None:
            return ConnectionStatus(
                ConnectionState.API_UNAVAILABLE,
                "Resolve did not answer. Enable Preferences > General > External scripting using: "
                "Local (the free edition may only run scripts from inside Resolve)",
                installed=installed,
                running=bool(running),
            )
        self._resolve = resolve
        try:
            version = ResolveVersion.parse(self._resolve.GetVersion())
            manager = self._resolve.GetProjectManager()
            if version is None or manager is None:
                raise ResolveCallFailed("read the version", "no answer from Resolve")
            if not version.at_least(self._minimum):
                return ConnectionStatus(
                    ConnectionState.UNSUPPORTED_VERSION,
                    f"Resolve {version.text} is older than the {self._minimum} this adapter drives",
                    installed=installed,
                    running=True,
                    environment=self.probe(),
                )
            return ConnectionStatus(
                ConnectionState.CONNECTED,
                "",
                installed=installed,
                running=True,
                environment=self.probe(),
            )
        except ResolveAdapterError as exc:
            return ConnectionStatus(
                ConnectionState.CONNECTION_FAILED, str(exc), installed=installed, running=True
            )
        except Exception as exc:  # noqa: BLE001 - see above
            return ConnectionStatus(
                ConnectionState.CONNECTION_FAILED,
                f"Resolve failed to answer: {exc}",
                installed=installed,
                running=True,
            )

    def disconnect(self) -> None:
        self._resolve = None
        self._pool_cache.clear()

    def alive(self) -> bool:
        if self._resolve is None:
            return False
        try:
            return bool(self._resolve.GetVersionString())
        except Exception:  # noqa: BLE001 - any failure of a dead proxy means "not alive"
            return False

    @_guarded
    def probe(self) -> ResolveEnvironment:
        resolve = self._need()
        product = str(self._call("read the product", resolve.GetProductName) or "")
        version = ResolveVersion.parse(self._call("read the version", resolve.GetVersion) or [])
        edition = "studio" if "studio" in product.lower() else ("free" if product else "unknown")
        manager = resolve.GetProjectManager()
        project = manager.GetCurrentProject() if manager else None
        objects: dict[str, Raw] = {
            "Resolve": resolve,
            "ProjectManager": manager,
            "Project": project,
        }
        if project is not None:
            pool = project.GetMediaPool()
            objects["MediaPool"] = pool
            timeline = project.GetCurrentTimeline()
            objects["Timeline"] = timeline
            if timeline is not None:
                for kind in ("video", "audio"):
                    for track in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                        items = timeline.GetItemListInTrack(kind, track) or []
                        if items:
                            objects["TimelineItem"] = items[0]
                            break
                    if "TimelineItem" in objects:
                        break
            if pool is not None:
                root = pool.GetRootFolder()
                clips = root.GetClipList() if root is not None else []
                if clips:
                    objects["MediaPoolItem"] = clips[0]
        probed = tuple(name for name in _PROBED if objects.get(name) is not None)
        methods = tuple(
            sorted(
                f"{name}.{attr}"
                for name in probed
                for attr in dir(objects[name])
                if not attr.startswith("_")
                and callable(inspect.getattr_static(objects[name], attr, None))
            )
        )
        databases: tuple[str, ...] = ()
        database: str | None = None
        if manager is not None:
            current = (
                manager.GetCurrentDatabase() if hasattr(manager, "GetCurrentDatabase") else None
            )
            if isinstance(current, dict):
                database = str(current.get("DbName") or current.get("DbType") or "") or None
            listed = manager.GetDatabaseList() if hasattr(manager, "GetDatabaseList") else None
            if isinstance(listed, list):
                databases = tuple(str(d.get("DbName", "")) for d in listed if isinstance(d, dict))
        names = (
            tuple(str(n) for n in (manager.GetProjectListInCurrentFolder() or []))
            if manager
            else ()
        )
        return ResolveEnvironment(
            product=product,
            version=version,
            edition=edition,
            operating_system=self._install.platform,
            scripting_module=str(self._install.script_lib or ""),
            database=database,
            databases=databases,
            current_project=str(project.GetName()) if project is not None else None,
            projects=names,
            probed=probed,
            methods=methods,
            plugins=None,
        )

    # --- projects ---------------------------------------------------------------------------
    @_guarded
    def current_project(self) -> ProjectInfo | None:
        project = self._manager().GetCurrentProject()
        return self._info(project) if project is not None else None

    @_guarded
    def list_projects(self) -> tuple[str, ...]:
        return tuple(str(n) for n in (self._manager().GetProjectListInCurrentFolder() or []))

    @_guarded
    def create_project(self, name: str) -> ProjectInfo:
        project = self._call("create the project", self._manager().CreateProject, name)
        if not project:
            raise ResolveCallFailed("create the project", name)
        return self._info(project)

    @_guarded
    def open_project(self, name: str) -> ProjectInfo:
        project = self._call("open the project", self._manager().LoadProject, name)
        if not project:
            raise ResolveCallFailed("open the project", name)
        return self._info(project)

    @_guarded
    def save_project(self) -> None:
        if not self._call("save the project", self._manager().SaveProject):
            raise ResolveCallFailed("save the project")

    @_guarded
    def export_project(self, name: str, path: Path) -> None:
        if not self._call(
            "export the project", self._manager().ExportProject, name, str(path), True
        ):
            raise ResolveCallFailed("export the project", name)

    @_guarded
    def get_settings(self, names: Sequence[str]) -> dict[str, str]:
        project = self._project()
        return {n: str(self._call(f"read {n}", project.GetSetting, n) or "") for n in names}

    @_guarded
    def set_settings(self, values: Mapping[str, str]) -> dict[str, bool]:
        project = self._project()
        return {
            n: bool(self._call(f"set {n}", project.SetSetting, n, v)) for n, v in values.items()
        }

    # --- media pool -------------------------------------------------------------------------
    @_guarded
    def pool_items(self) -> tuple[PoolItem, ...]:
        pool = self._pool()
        found: list[PoolItem] = []
        self._pool_cache.clear()

        def walk(folder: Raw, path: tuple[str, ...]) -> None:
            for clip in folder.GetClipList() or []:
                item = self._pool_item(clip, path)
                self._pool_cache[item.item_id] = clip
                found.append(item)
            for sub in folder.GetSubFolderList() or []:
                walk(sub, (*path, str(sub.GetName())))

        root = pool.GetRootFolder()
        walk(root, ())
        return tuple(found)

    @_guarded
    def ensure_folder(self, path: tuple[str, ...]) -> None:
        self._folder(path, create=True)

    @_guarded
    def import_media(self, paths: Sequence[Path], folder: tuple[str, ...]) -> tuple[PoolItem, ...]:
        pool = self._pool()
        target = self._folder(folder, create=True)
        self._call("open the pool folder", pool.SetCurrentFolder, target)
        imported = self._call("import media", pool.ImportMedia, [str(p) for p in paths]) or []
        items: list[PoolItem] = []
        for clip in imported:
            item = self._pool_item(clip, folder)
            self._pool_cache[item.item_id] = clip
            items.append(item)
        return tuple(items)

    @_guarded
    def link_proxy(self, item_id: str, path: Path) -> bool:
        clip = self._clip(item_id)
        return bool(self._call("link the proxy", clip.LinkProxyMedia, str(path)))

    @_guarded
    def relink(self, item_ids: Sequence[str], folder: Path) -> bool:
        clips = [self._clip(i) for i in item_ids]
        return bool(self._call("relink the media", self._pool().RelinkClips, clips, str(folder)))

    # --- timelines --------------------------------------------------------------------------
    @_guarded
    def list_timelines(self) -> tuple[TimelineInfo, ...]:
        project = self._project()
        count = int(project.GetTimelineCount() or 0)
        return tuple(
            self._timeline_info(project.GetTimelineByIndex(i)) for i in range(1, count + 1)
        )

    @_guarded
    def create_timeline(self, name: str, start_timecode: str) -> TimelineInfo:
        project = self._project()
        timeline = self._call("create the timeline", self._pool().CreateEmptyTimeline, name)
        if not timeline:
            raise ResolveCallFailed("create the timeline", name)
        self._call("open the timeline", project.SetCurrentTimeline, timeline)
        self._call("set the start timecode", timeline.SetStartTimecode, start_timecode)
        return self._timeline_info(timeline)

    @_guarded
    def open_timeline(self, name: str) -> TimelineInfo:
        timeline = self._find_timeline(name)
        if not self._call("open the timeline", self._project().SetCurrentTimeline, timeline):
            raise ResolveCallFailed("open the timeline", name)
        return self._timeline_info(timeline)

    @_guarded
    def duplicate_timeline(self, name: str, new_name: str) -> TimelineInfo:
        timeline = self._find_timeline(name)
        copy = self._call("duplicate the timeline", timeline.DuplicateTimeline, new_name)
        if not copy:
            raise ResolveCallFailed("duplicate the timeline", name)
        return self._timeline_info(copy)

    @_guarded
    def rename_timeline(self, name: str, new_name: str) -> None:
        timeline = self._find_timeline(name)
        if not self._call("rename the timeline", timeline.SetName, new_name):
            raise ResolveCallFailed("rename the timeline", name)

    @_guarded
    def ensure_tracks(self, video: int, audio: int, audio_layout: str) -> None:
        timeline = self._timeline()
        for kind, wanted in (("video", video), ("audio", audio)):
            while int(timeline.GetTrackCount(kind) or 0) < wanted:
                args = (kind, audio_layout) if kind == "audio" else (kind,)
                if not self._call(f"add a {kind} track", timeline.AddTrack, *args):
                    raise ResolveCallFailed(f"add a {kind} track")

    @_guarded
    def name_track(self, kind: str, index: int, name: str) -> bool:
        return bool(self._call("name the track", self._timeline().SetTrackName, kind, index, name))

    @_guarded
    def place(self, requests: Sequence[PlaceRequest]) -> tuple[Placed, ...]:
        pool, timeline = self._pool(), self._timeline()
        offset = int(timeline.GetStartFrame() or 0)
        placed: list[Placed] = []
        for r in requests:
            info = {
                "mediaPoolItem": self._clip(r.pool_item_id),
                "startFrame": r.start_frame,
                "endFrame": r.end_frame,
                "mediaType": r.media_type,
                "trackIndex": r.track_index,
                "recordFrame": offset + r.record_frame,
            }
            items = self._call("place a clip", pool.AppendToTimeline, [info]) or []
            if items:
                kind = "video" if r.media_type == 1 else "audio"
                placed.append(
                    Placed(
                        r.element_id, self._item_id(items[0], kind, r.track_index, r.record_frame)
                    )
                )
        return tuple(placed)

    @_guarded
    def set_item(
        self, item_id: str, properties: Mapping[str, float], enabled: bool | None
    ) -> dict[str, bool]:
        item = self._find_item(item_id)
        done = {
            name: bool(self._call(f"set {name}", item.SetProperty, name, value))
            for name, value in properties.items()
        }
        if enabled is not None:
            done["enabled"] = bool(self._call("enable the clip", item.SetClipEnabled, enabled))
        return done

    @_guarded
    def delete_items(self, item_ids: Sequence[str]) -> None:
        items = [self._find_item(i) for i in item_ids]
        if items and not self._call("delete clips", self._timeline().DeleteClips, items, False):
            raise ResolveCallFailed("delete clips")

    @_guarded
    def link_items(self, item_ids: Sequence[str]) -> bool:
        items = [self._find_item(i) for i in item_ids]
        return bool(self._call("link clips", self._timeline().SetClipsLinked, items, True))

    @_guarded
    def add_marker(
        self, scope: str, frame: int, color: str, name: str, note: str, duration: int, custom: str
    ) -> bool:
        timeline = self._timeline()
        if scope == "timeline":
            at = frame + int(timeline.GetStartFrame() or 0)
            return bool(
                self._call(
                    "add a marker", timeline.AddMarker, at, color, name, note, duration, custom
                )
            )
        item = self._find_item(scope)
        return bool(
            self._call("tag the clip", item.AddMarker, frame, color, name, note, duration, custom)
        )

    @_guarded
    def remove_marker(self, scope: str, custom: str) -> bool:
        target = self._timeline() if scope == "timeline" else self._find_item(scope)
        return bool(self._call("remove a marker", target.DeleteMarkerByCustomData, custom))

    @_guarded
    def read_timeline(self) -> TimelineSnapshot:
        timeline = self._timeline()
        offset = int(timeline.GetStartFrame() or 0)
        tracks: list[LiveTrack] = []
        items: list[LiveItem] = []
        for kind in ("video", "audio", "subtitle"):
            for index in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                tracks.append(
                    LiveTrack(
                        kind=kind,
                        index=index,
                        name=str(timeline.GetTrackName(kind, index) or ""),
                        enabled=bool(timeline.GetIsTrackEnabled(kind, index)),
                        locked=bool(timeline.GetIsTrackLocked(kind, index)),
                    )
                )
                if kind == "subtitle":
                    continue
                for raw in timeline.GetItemListInTrack(kind, index) or []:
                    items.append(self._live_item(raw, kind, index, offset))
        markers = tuple(
            LiveMarker(
                scope="timeline",
                frame=int(frame) - offset,
                color=str(data.get("color", "")),
                name=str(data.get("name", "")),
                note=str(data.get("note", "")),
                duration=int(data.get("duration", 1)),
                custom_data=str(data.get("customData", "")),
            )
            for frame, data in sorted((timeline.GetMarkers() or {}).items())
        )
        return TimelineSnapshot(
            info=self._timeline_info(timeline),
            tracks=tuple(tracks),
            items=tuple(sorted(items, key=lambda i: (i.track_kind, i.track_index, i.start))),
            markers=markers,
        )

    # --- render -----------------------------------------------------------------------------
    @_guarded
    def render_options(self, format_id: str | None) -> RenderOptions:
        project = self._project()
        formats = project.GetRenderFormats() or {}
        current = project.GetCurrentRenderFormatAndCodec() or {}
        chosen = format_id or str(current.get("format", ""))
        codecs = (project.GetRenderCodecs(chosen) or {}) if chosen else {}
        return RenderOptions(
            formats=tuple((str(k), str(v)) for k, v in formats.items()),
            codecs=tuple((str(k), str(v)) for k, v in codecs.items()),
            current_format=str(current.get("format", "")),
            current_codec=str(current.get("codec", "")),
        )

    @_guarded
    def configure_render(
        self, format_id: str, codec_id: str, settings: Mapping[str, SettingValue]
    ) -> dict[str, bool]:
        project = self._project()
        done = {
            "format_and_codec": bool(
                self._call(
                    "set the render format",
                    project.SetCurrentRenderFormatAndCodec,
                    format_id,
                    codec_id,
                )
            )
        }
        ok = bool(self._call("set the render settings", project.SetRenderSettings, dict(settings)))
        done.update(dict.fromkeys(settings, ok))
        return done

    @_guarded
    def submit_render(self) -> str:
        project = self._project()
        job = self._call("queue the render", project.AddRenderJob)
        if not job:
            raise ResolveCallFailed("queue the render")
        if not self._call("start the render", project.StartRendering, job):
            raise ResolveCallFailed("start the render", str(job))
        return str(job)

    @_guarded
    def render_status(self, job_id: str) -> RenderStatus:
        data = (
            self._call("read the render status", self._project().GetRenderJobStatus, job_id) or {}
        )
        percent = data.get("CompletionPercentage")
        taken = data.get("TimeTakenToRenderInMs")
        return RenderStatus(
            job_id=job_id,
            status=str(data.get("JobStatus", "Unknown")),
            percent=float(percent) if isinstance(percent, int | float) else None,
            error=str(data.get("Error", "")),
            seconds=float(taken) / 1000.0 if isinstance(taken, int | float) else None,
        )

    @_guarded
    def stop_render(self) -> None:
        self._call("stop the render", self._project().StopRendering)

    # --- plumbing ---------------------------------------------------------------------------
    def _need(self) -> Raw:
        if self._resolve is None:
            raise ResolveConnectionLost("use Resolve before connecting")
        return self._resolve

    def _call(self, operation: str, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except ResolveAdapterError:
            raise
        except Exception as exc:
            if not self.alive():
                raise ResolveConnectionLost(operation) from exc
            raise ResolveCallFailed(operation, str(exc)) from exc

    def _manager(self) -> Raw:
        manager = self._call("reach the project manager", self._need().GetProjectManager)
        if manager is None:
            raise ResolveConnectionLost("reach the project manager")
        return manager

    def _project(self) -> Raw:
        project = self._call("reach the open project", self._manager().GetCurrentProject)
        if project is None:
            raise ResolveCallFailed("reach the open project", "no project is open")
        return project

    def _pool(self) -> Raw:
        pool = self._call("reach the media pool", self._project().GetMediaPool)
        if pool is None:
            raise ResolveCallFailed("reach the media pool")
        return pool

    def _timeline(self) -> Raw:
        timeline = self._call("reach the timeline", self._project().GetCurrentTimeline)
        if timeline is None:
            raise ResolveCallFailed("reach the timeline", "no timeline is open")
        return timeline

    def _find_timeline(self, name: str) -> Raw:
        project = self._project()
        for i in range(1, int(project.GetTimelineCount() or 0) + 1):
            timeline = project.GetTimelineByIndex(i)
            if timeline is not None and str(timeline.GetName()) == name:
                return timeline
        raise ResolveCallFailed("find the timeline", name)

    def _folder(self, path: tuple[str, ...], *, create: bool) -> Raw:
        pool = self._pool()
        folder = pool.GetRootFolder()
        for name in path:
            nxt = next(
                (s for s in folder.GetSubFolderList() or [] if str(s.GetName()) == name), None
            )
            if nxt is None:
                if not create:
                    raise ResolveCallFailed("find the pool folder", "/".join(path))
                nxt = self._call("create the pool folder", pool.AddSubFolder, folder, name)
                if not nxt:
                    raise ResolveCallFailed("create the pool folder", "/".join(path))
            folder = nxt
        return folder

    def _clip(self, item_id: str) -> Raw:
        if item_id not in self._pool_cache:
            self.pool_items()
        clip = self._pool_cache.get(item_id)
        if clip is None:
            raise ResolveCallFailed("find the pool item", item_id)
        return clip

    def _info(self, project: Raw) -> ProjectInfo:
        unique = project.GetUniqueId() if hasattr(project, "GetUniqueId") else None
        manager = self._manager()
        current = manager.GetCurrentDatabase() if hasattr(manager, "GetCurrentDatabase") else None
        database = str(current.get("DbName", "")) if isinstance(current, dict) else None
        return ProjectInfo(
            str(project.GetName()), str(unique) if unique else None, database or None
        )

    def _timeline_info(self, timeline: Raw) -> TimelineInfo:
        unique = timeline.GetUniqueId() if hasattr(timeline, "GetUniqueId") else None
        return TimelineInfo(
            name=str(timeline.GetName()),
            unique_id=str(unique) if unique else None,
            start_frame=int(timeline.GetStartFrame() or 0),
            end_frame=int(timeline.GetEndFrame() or 0),
            video_tracks=int(timeline.GetTrackCount("video") or 0),
            audio_tracks=int(timeline.GetTrackCount("audio") or 0),
            subtitle_tracks=int(timeline.GetTrackCount("subtitle") or 0),
        )

    def _pool_item(self, clip: Raw, folder: tuple[str, ...]) -> PoolItem:
        def prop(name: str) -> str:
            value = clip.GetClipProperty(name)
            return "" if value is None else str(value)

        unique = clip.GetUniqueId() if hasattr(clip, "GetUniqueId") else clip.GetMediaId()
        kind = prop("Type").lower()
        media = (
            "audio"
            if kind == "audio"
            else "image"
            if "still" in kind
            else "video"
            if "video" in kind
            else "other"
        )
        width, height = _resolution(prop("Resolution"))
        proxy_path = prop("Proxy Media Path")
        return PoolItem(
            item_id=str(unique),
            name=str(clip.GetName()),
            path=prop("File Path"),
            folder=folder,
            media_type=media,
            fps=prop("FPS"),
            frames=_int(prop("Frames")),
            width=width,
            height=height,
            offline=prop("Offline").lower() in {"true", "1", "yes"},
            proxy="linked" if proxy_path else "none",
            proxy_path=proxy_path or None,
        )

    def _item_id(self, item: Raw, kind: str, track: int, frame: int) -> str:
        if hasattr(item, "GetUniqueId"):
            return str(item.GetUniqueId())
        return f"pos:{kind}:{track}:{frame}"

    def _find_item(self, item_id: str) -> Raw:
        timeline = self._timeline()
        offset = int(timeline.GetStartFrame() or 0)
        for kind in ("video", "audio"):
            for index in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                for raw in timeline.GetItemListInTrack(kind, index) or []:
                    if self._item_id(raw, kind, index, int(raw.GetStart()) - offset) == item_id:
                        return raw
        raise ResolveCallFailed("find the timeline item", item_id)

    def _live_item(self, raw: Raw, kind: str, index: int, offset: int) -> LiveItem:
        start = int(raw.GetStart()) - offset
        media = raw.GetMediaPoolItem() if hasattr(raw, "GetMediaPoolItem") else None
        media_id = None
        if media is not None:
            media_id = str(
                media.GetUniqueId() if hasattr(media, "GetUniqueId") else media.GetMediaId()
            )
        props = tuple(
            (name, float(value))
            for name in _PROPERTIES
            if isinstance(value := raw.GetProperty(name), int | float)
            and not isinstance(value, bool)
        )
        tag = ""
        for data in (raw.GetMarkers() or {}).values() if hasattr(raw, "GetMarkers") else ():
            custom = str(data.get("customData", ""))
            if custom.startswith(_TAG):
                tag = custom[len(_TAG) :]
        return LiveItem(
            item_id=self._item_id(raw, kind, index, start),
            name=str(raw.GetName()),
            tag=tag,
            track_kind=kind,
            track_index=index,
            start=start,
            duration=int(raw.GetDuration()),
            media_item_id=media_id,
            left_offset=int(raw.GetLeftOffset() or 0),
            enabled=bool(raw.GetClipEnabled()) if hasattr(raw, "GetClipEnabled") else True,
            properties=props,
        )


def _int(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None


def _resolution(text: str) -> tuple[int | None, int | None]:
    parts = text.lower().split("x")
    if len(parts) != 2:
        return None, None
    return _int(parts[0]), _int(parts[1])
