"""A stand-in for DaVinci Resolve's scripting object tree, for tests.

It follows the DOCUMENTED API (Resolve 18/19): ``Resolve`` -> ``ProjectManager`` -> ``Project`` ->
``MediaPool`` / ``Timeline`` -> ``TimelineItem``, with the behaviours the adapter depends on
(``AppendToTimeline`` places by ``recordFrame`` and an inclusive ``endFrame``, an occupied slot is
refused, a project's frame rate is fixed once clips exist, markers carry custom data, tracks
count from 1). It is NOT Resolve: it proves the adapter does what the documentation says, not what
a real Resolve does. The switches (``alive``, ``end_inclusive``, ``studio``) let tests break it on
purpose; the ``human_*`` methods do what a person does in Resolve.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from media_house.core.application.ports import ProcessResult, ProcessSpec

FPS_TEXT = {24.0: "24", 25.0: "25", 30.0: "30", 29.97: "29.97", 23.976: "23.976"}


class Dead(RuntimeError):
    """What a vendor proxy raises once Resolve is gone."""


class FakeClip:
    def __init__(self, uid: str, path: Path, *, offline: bool = False) -> None:
        self.uid = uid
        self.path = path
        self.offline = offline
        self.proxy = ""
        self.fps = "25"
        self.frames = 100000
        self.kind = "Video"
        self.resolution = "1920x1080"

    def GetUniqueId(self) -> str:  # noqa: N802
        return self.uid

    def GetName(self) -> str:  # noqa: N802
        return self.path.name

    def GetClipProperty(self, name: str) -> str:  # noqa: N802
        return {
            "File Path": str(self.path),
            "Type": self.kind,
            "FPS": self.fps,
            "Frames": str(self.frames),
            "Resolution": self.resolution,
            "Offline": "True" if self.offline else "False",
            "Proxy Media Path": self.proxy,
        }.get(name, "")

    def LinkProxyMedia(self, path: str) -> bool:  # noqa: N802
        self.proxy = path
        return True


class FakeFolder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.clips: list[FakeClip] = []
        self.folders: list[FakeFolder] = []

    def GetName(self) -> str:  # noqa: N802
        return self.name

    def GetClipList(self) -> list[FakeClip]:  # noqa: N802
        return list(self.clips)

    def GetSubFolderList(self) -> list["FakeFolder"]:  # noqa: N802
        return list(self.folders)


class FakeItem:
    PROPS = {
        "Pan", "Tilt", "ZoomX", "ZoomY", "RotationAngle", "Opacity",
        "CropLeft", "CropRight", "CropTop", "CropBottom",
    }  # fmt: skip

    def __init__(
        self,
        uid: str,
        clip: FakeClip,
        start: int,
        duration: int,
        left: int,
        kind: str,
        track: int,
    ) -> None:
        self.uid, self.clip, self.start, self.duration, self.left = uid, clip, start, duration, left
        self.kind, self.track = kind, track
        self.enabled = True
        self.props: dict[str, float] = {
            "Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0,
            "RotationAngle": 0.0, "Opacity": 100.0,
        }  # fmt: skip
        self.markers: dict[int, dict[str, Any]] = {}
        self.linked: set[str] = set()
        self.timeline: FakeTimeline | None = None

    def GetUniqueId(self) -> str:  # noqa: N802
        return self.uid

    def GetName(self) -> str:  # noqa: N802
        return self.clip.GetName()

    def GetStart(self) -> int:  # noqa: N802
        assert self.timeline is not None
        return self.timeline.start_frame + self.start

    def GetDuration(self) -> int:  # noqa: N802
        return self.duration

    def GetLeftOffset(self) -> int:  # noqa: N802
        return self.left

    def GetClipEnabled(self) -> bool:  # noqa: N802
        return self.enabled

    def SetClipEnabled(self, value: bool) -> bool:  # noqa: N802
        self.enabled = value
        return True

    def GetMediaPoolItem(self) -> FakeClip:  # noqa: N802
        return self.clip

    def SetProperty(self, name: str, value: float) -> bool:  # noqa: N802
        if name not in self.PROPS:
            return False
        self.props[name] = float(value)
        return True

    def GetProperty(self, name: str) -> float | None:  # noqa: N802
        return self.props.get(name)

    def AddMarker(  # noqa: N802
        self, frame: int, color: str, name: str, note: str, duration: int, custom: str = ""
    ) -> bool:
        if frame in self.markers:
            return False
        self.markers[frame] = {
            "color": color, "name": name, "note": note, "duration": duration, "customData": custom,
        }  # fmt: skip
        return True

    def GetMarkers(self) -> dict[int, dict[str, Any]]:  # noqa: N802
        return dict(self.markers)

    def DeleteMarkerByCustomData(self, custom: str) -> bool:  # noqa: N802
        gone = [f for f, m in self.markers.items() if m["customData"] == custom]
        for f in gone:
            del self.markers[f]
        return bool(gone)


class FakeTimeline:
    def __init__(self, project: "FakeProject", name: str, uid: str) -> None:
        self.project, self.name, self.uid = project, name, uid
        self.start_frame = 86400  # 01:00:00:00 at 24: Resolve's default start
        self.tracks: dict[str, list[list[FakeItem]]] = {
            "video": [[]],
            "audio": [[]],
            "subtitle": [],
        }
        self.track_names: dict[tuple[str, int], str] = {}
        self.markers: dict[int, dict[str, Any]] = {}

    def GetName(self) -> str:  # noqa: N802
        return self.name

    def SetName(self, name: str) -> bool:  # noqa: N802
        self.name = name
        return True

    def GetUniqueId(self) -> str:  # noqa: N802
        return self.uid

    def GetStartFrame(self) -> int:  # noqa: N802
        return self.start_frame

    def GetEndFrame(self) -> int:  # noqa: N802
        return self.start_frame + max(
            (i.start + i.duration for t in self.tracks.values() for lane in t for i in lane),
            default=0,
        )

    def SetStartTimecode(self, tc: str) -> bool:  # noqa: N802
        self.start_frame = 0 if tc == "00:00:00:00" else 86400
        return True

    def GetTrackCount(self, kind: str) -> int:  # noqa: N802
        return len(self.tracks[kind])

    def AddTrack(self, kind: str, layout: str = "") -> bool:  # noqa: N802
        self.tracks[kind].append([])
        return True

    def SetTrackName(self, kind: str, index: int, name: str) -> bool:  # noqa: N802
        self.track_names[(kind, index)] = name
        return True

    def GetTrackName(self, kind: str, index: int) -> str:  # noqa: N802
        return self.track_names.get((kind, index), f"{kind.title()} {index}")

    def GetIsTrackEnabled(self, kind: str, index: int) -> bool:  # noqa: N802
        return True

    def GetIsTrackLocked(self, kind: str, index: int) -> bool:  # noqa: N802
        return False

    def GetItemListInTrack(self, kind: str, index: int) -> list[FakeItem]:  # noqa: N802
        lanes = self.tracks[kind]
        return sorted(lanes[index - 1], key=lambda i: i.start) if 0 < index <= len(lanes) else []

    def DeleteClips(self, items: list[FakeItem], ripple: bool = False) -> bool:  # noqa: N802
        for lanes in self.tracks.values():
            for lane in lanes:
                lane[:] = [i for i in lane if i not in items]
        return True

    def SetClipsLinked(self, items: list[FakeItem], linked: bool) -> bool:  # noqa: N802
        for a in items:
            a.linked |= {b.uid for b in items if b is not a}
        return True

    def AddMarker(  # noqa: N802
        self, frame: int, color: str, name: str, note: str, duration: int, custom: str = ""
    ) -> bool:
        if frame in self.markers:
            return False
        self.markers[frame] = {
            "color": color, "name": name, "note": note, "duration": duration, "customData": custom,
        }  # fmt: skip
        return True

    def GetMarkers(self) -> dict[int, dict[str, Any]]:  # noqa: N802
        return dict(self.markers)

    def DeleteMarkerByCustomData(self, custom: str) -> bool:  # noqa: N802
        gone = [f for f, m in self.markers.items() if m["customData"] == custom]
        for f in gone:
            del self.markers[f]
        return bool(gone)

    def DuplicateTimeline(self, name: str) -> "FakeTimeline | None":  # noqa: N802
        return self.project.copy_timeline(self, name)

    def GetSetting(self, name: str) -> str:  # noqa: N802
        return ""


class FakePool:
    def __init__(self, project: "FakeProject") -> None:
        self.project = project
        self.root = FakeFolder("Master")
        self.current = self.root

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        return self.root

    def SetCurrentFolder(self, folder: FakeFolder) -> bool:  # noqa: N802
        self.current = folder
        return True

    def AddSubFolder(self, parent: FakeFolder, name: str) -> FakeFolder | None:  # noqa: N802
        if any(f.name == name for f in parent.folders):
            return None
        folder = FakeFolder(name)
        parent.folders.append(folder)
        return folder

    def ImportMedia(self, paths: list[str]) -> list[FakeClip]:  # noqa: N802
        out: list[FakeClip] = []
        for text in paths:
            path = Path(text)
            if not path.is_file():
                continue
            clip = FakeClip(self.project.next_id("clip"), path)
            if path.suffix == ".srt":
                clip.kind = "Subtitle"
            self.current.clips.append(clip)
            out.append(clip)
        return out

    def RelinkClips(self, clips: list[FakeClip], folder: str) -> bool:  # noqa: N802
        for c in clips:
            c.offline = not c.path.is_file()
        return True

    def CreateEmptyTimeline(self, name: str) -> FakeTimeline | None:  # noqa: N802
        if any(t.name == name for t in self.project.timelines):
            return None
        return self.project.new_timeline(name)

    def AppendToTimeline(self, infos: list[dict[str, Any]]) -> list[FakeItem]:  # noqa: N802
        timeline = self.project.current_timeline
        if timeline is None:
            return []
        out: list[FakeItem] = []
        for info in infos:
            limit = self.project.resolve.die_after
            if limit is not None and self.project.resolve.placed >= limit:
                self.project.resolve.alive = False
                raise Dead("Resolve crashed")
            clip: FakeClip = info["mediaPoolItem"]
            kind = "video" if info.get("mediaType", 1) == 1 else "audio"
            lanes = timeline.tracks[kind]
            index = int(info.get("trackIndex", 1))
            if not 0 < index <= len(lanes):
                continue
            start = int(info["recordFrame"]) - timeline.start_frame
            last = int(info["endFrame"]) + (1 if self.project.resolve.end_inclusive else 0)
            duration = last - int(info["startFrame"])
            if any(
                i.start < start + duration and start < i.start + i.duration
                for i in lanes[index - 1]
            ):
                continue  # the slot is taken: nothing is placed
            item = FakeItem(
                self.project.next_id("item"),
                clip,
                start,
                duration,
                int(info["startFrame"]),
                kind,
                index,
            )
            item.timeline = timeline
            lanes[index - 1].append(item)
            out.append(item)
        self.project.resolve.placed += len(out)
        return out


class FakeProject:
    def __init__(self, resolve: "FakeResolve", name: str, uid: str) -> None:
        self.resolve = resolve
        self.name = name
        self.uid = uid
        self.settings: dict[str, str] = {
            "timelineFrameRate": "24",
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
            "timelinePlaybackFrameRate": "24",
        }
        self.pool = FakePool(self)
        self.timelines: list[FakeTimeline] = []
        self.current_timeline: FakeTimeline | None = None
        self.counter = 0
        self.render_format = ("mp4", "H264")
        self.render_settings: dict[str, Any] = {}
        self.jobs: dict[str, list[str]] = {}
        self.saved = 0

    @property
    def timeline(self) -> "FakeTimeline":
        assert self.current_timeline is not None
        return self.current_timeline

    def next_id(self, kind: str) -> str:
        self.counter += 1
        return f"{kind}-{self.uid}-{self.counter}"

    def new_timeline(self, name: str) -> FakeTimeline:
        timeline = FakeTimeline(self, name, self.next_id("tl"))
        self.timelines.append(timeline)
        self.current_timeline = timeline
        return timeline

    def copy_timeline(self, source: FakeTimeline, name: str) -> FakeTimeline:
        copy = FakeTimeline(self, name, self.next_id("tl"))
        copy.start_frame = source.start_frame
        for kind, lanes in source.tracks.items():
            copy.tracks[kind] = [[] for _ in lanes]
            for index, lane in enumerate(lanes):
                for item in lane:
                    twin = FakeItem(
                        self.next_id("item"), item.clip, item.start, item.duration, item.left,
                        item.kind, item.track,
                    )  # fmt: skip
                    twin.timeline, twin.props, twin.markers = (
                        copy,
                        dict(item.props),
                        dict(item.markers),
                    )
                    copy.tracks[kind][index].append(twin)
        self.timelines.append(copy)
        return copy

    def GetName(self) -> str:  # noqa: N802
        return self.name

    def GetUniqueId(self) -> str:  # noqa: N802
        return self.uid

    def GetMediaPool(self) -> FakePool:  # noqa: N802
        return self.pool

    def GetTimelineCount(self) -> int:  # noqa: N802
        return len(self.timelines)

    def GetTimelineByIndex(self, index: int) -> FakeTimeline:  # noqa: N802
        return self.timelines[index - 1]

    def GetCurrentTimeline(self) -> FakeTimeline | None:  # noqa: N802
        return self.current_timeline

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:  # noqa: N802
        self.current_timeline = timeline
        return True

    def GetSetting(self, name: str) -> str:  # noqa: N802
        return self.settings.get(name, "")

    def SetSetting(self, name: str, value: str) -> bool:  # noqa: N802
        locked = {"timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight"}
        if name in locked and any(
            lane for t in self.timelines for lanes in t.tracks.values() for lane in lanes if lane
        ):
            return False
        self.settings[name] = value
        return True

    # --- render ---
    def GetRenderFormats(self) -> dict[str, str]:  # noqa: N802
        return {"MP4": "mp4", "QuickTime": "mov"}

    def GetRenderCodecs(self, fmt: str) -> dict[str, str]:  # noqa: N802
        return {
            "mp4": {"H.264": "H264", "H.265": "H265"},
            "mov": {"ProRes 422 HQ": "ProRes422HQ"},
        }.get(fmt, {})

    def GetCurrentRenderFormatAndCodec(self) -> dict[str, str]:  # noqa: N802
        return {"format": self.render_format[0], "codec": self.render_format[1]}

    def SetCurrentRenderFormatAndCodec(self, fmt: str, codec: str) -> bool:  # noqa: N802
        self.render_format = (fmt, codec)
        return True

    def SetRenderSettings(self, settings: dict[str, Any]) -> bool:  # noqa: N802
        self.render_settings = dict(settings)
        return True

    def AddRenderJob(self) -> str:  # noqa: N802
        job = f"job-{len(self.jobs) + 1}"
        self.jobs[job] = list(self.resolve.render_script)
        return job

    def StartRendering(self, job: str) -> bool:  # noqa: N802
        return job in self.jobs

    def GetRenderJobStatus(self, job: str) -> dict[str, Any]:  # noqa: N802
        script = self.jobs[job]
        word = script.pop(0) if len(script) > 1 else script[0]
        if word == "Complete":
            self.resolve.write_render(self)
            return {
                "JobStatus": "Complete",
                "CompletionPercentage": 100,
                "TimeTakenToRenderInMs": 1500,
            }
        if word == "Failed":
            return {"JobStatus": "Failed", "CompletionPercentage": 10, "Error": "disk full"}
        return {"JobStatus": "Rendering", "CompletionPercentage": 40}

    def StopRendering(self) -> None:  # noqa: N802
        self.resolve.stopped += 1


class FakeManager:
    def __init__(self, resolve: "FakeResolve") -> None:
        self.resolve = resolve

    def GetProjectListInCurrentFolder(self) -> list[str]:  # noqa: N802
        return sorted(self.resolve.projects)

    def GetCurrentProject(self) -> FakeProject | None:  # noqa: N802
        return self.resolve.current

    def CreateProject(self, name: str) -> FakeProject | None:  # noqa: N802
        if name in self.resolve.projects:
            return None
        project = FakeProject(self.resolve, name, f"p{len(self.resolve.projects) + 1}")
        self.resolve.projects[name] = project
        self.resolve.current = project
        return project

    def LoadProject(self, name: str) -> FakeProject | None:  # noqa: N802
        project = self.resolve.projects.get(name)
        if project is not None:
            self.resolve.current = project
        return project

    def SaveProject(self) -> bool:  # noqa: N802
        if self.resolve.current is not None:
            self.resolve.current.saved += 1
        return True

    def ExportProject(self, name: str, path: str, stills: bool = True) -> bool:  # noqa: N802
        Path(path).write_text(name, encoding="utf-8")
        return True

    def GetCurrentDatabase(self) -> dict[str, str]:  # noqa: N802
        return {"DbType": "Disk", "DbName": "Local Database"}

    def GetDatabaseList(self) -> list[dict[str, str]]:  # noqa: N802
        return [{"DbType": "Disk", "DbName": "Local Database"}]


class FakeResolve:
    """The ``resolve`` object ``scriptapp("Resolve")`` returns."""

    def __init__(
        self,
        *,
        studio: bool = True,
        version: tuple[int, int, int, int, str] = (19, 0, 3, 2, ""),
        end_inclusive: bool = True,
        render_script: tuple[str, ...] = ("Rendering", "Complete"),
        render_writer: Callable[[Path, str], None] | None = None,
    ) -> None:
        self.studio = studio
        self.version = version
        self.end_inclusive = end_inclusive
        self.render_script = list(render_script)
        self.render_writer = render_writer
        self.projects: dict[str, FakeProject] = {}
        self.current: FakeProject | None = None
        self.alive = True
        self.placed = 0
        self.stopped = 0
        #: Resolve goes away once this many clips were placed (a crash in the middle of a run).
        self.die_after: int | None = None
        self.manager = FakeManager(self)

    # --- the API ---
    def _live(self) -> None:
        if not self.alive:
            raise Dead("Resolve is not running")

    def GetVersion(self) -> list[Any]:  # noqa: N802
        self._live()
        return list(self.version)

    def GetVersionString(self) -> str:  # noqa: N802
        self._live()
        return ".".join(str(n) for n in self.version[:3])

    def GetProductName(self) -> str:  # noqa: N802
        self._live()
        return "DaVinci Resolve Studio" if self.studio else "DaVinci Resolve"

    def GetProjectManager(self) -> FakeManager:  # noqa: N802
        self._live()
        return self.manager

    # --- what a person does in Resolve ---
    def human_item(self, element_tag: str, project: FakeProject) -> FakeItem:
        """The placed item whose tag marker names ``element_tag``."""
        timeline = project.current_timeline
        assert timeline is not None
        for lanes in timeline.tracks.values():
            for lane in lanes:
                for item in lane:
                    for m in item.markers.values():
                        if m["customData"].endswith(f"clip:{element_tag}"):
                            return item
        raise KeyError(element_tag)

    def write_render(self, project: FakeProject) -> None:
        target = Path(project.render_settings.get("TargetDir", "."))
        name = project.render_settings.get("CustomName", "render")
        ext = {"mp4": "mp4", "mov": "mov"}[project.render_format[0]]
        if self.render_writer is not None:
            self.render_writer(target / f"{name}.{ext}", ext)
        else:
            (target / f"{name}.{ext}").write_bytes(b"\x00" * 4096)


class FakeRunner:
    """A ``ProcessRunner`` that answers the process question."""

    def __init__(self, *, running: bool | None = True, process: str = "Resolve.exe") -> None:
        self.running = running
        self.process = process
        self.calls: list[ProcessSpec] = []

    def run(
        self, spec: ProcessSpec, *, cancellation: object = None, on_output: object = None
    ) -> ProcessResult:
        self.calls.append(spec)
        if self.running is None:
            from media_house.shared.errors import ToolNotFoundError

            raise ToolNotFoundError(spec.executable)
        if spec.executable == "tasklist":
            out = f"{self.process}   1234 Console" if self.running else "INFO: No tasks are running"
            return ProcessResult(0, out, "", 0.0)
        return ProcessResult(0 if self.running else 1, "", "", 0.0)
