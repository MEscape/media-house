"""What Resolve reports about a project, its media pool and a timeline.

These are the adapter's own plain records of Resolve's live state. Resolve's objects never leave
the infrastructure adapter; everything above it reads and writes these.

Timeline frames in a ``TimelineSnapshot`` are RELATIVE to the timeline start (frame 0 is the first
frame), whatever start timecode the project uses.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    name: str
    unique_id: str | None = None
    database: str | None = None


@dataclass(frozen=True, slots=True)
class PoolItem:
    item_id: str
    name: str
    path: str
    folder: tuple[str, ...]
    media_type: str  # video | audio | image | other
    fps: str = ""
    frames: int | None = None
    width: int | None = None
    height: int | None = None
    offline: bool = False
    #: ``none`` | ``linked`` | ``offline`` | ``unknown`` (the API may not say).
    proxy: str = "unknown"
    proxy_path: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineInfo:
    name: str
    unique_id: str | None
    start_frame: int
    end_frame: int
    video_tracks: int
    audio_tracks: int
    subtitle_tracks: int = 0


@dataclass(frozen=True, slots=True)
class LiveTrack:
    kind: str  # video | audio | subtitle
    index: int
    name: str
    enabled: bool = True
    locked: bool = False


@dataclass(frozen=True, slots=True)
class LiveItem:
    item_id: str
    name: str
    #: The element id from the adapter's tag on the item ("" for a person's own item).
    tag: str
    track_kind: str
    track_index: int
    start: int
    duration: int
    media_item_id: str | None
    #: Frames trimmed from the head of the media (the in point, as a frame of the file).
    left_offset: int
    enabled: bool
    #: Resolve property name -> value, only the ones Resolve answered.
    properties: tuple[tuple[str, float], ...] = ()

    def prop(self, name: str) -> float | None:
        return next((v for k, v in self.properties if k == name), None)

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass(frozen=True, slots=True)
class LiveMarker:
    #: ``timeline``, or the id of the item the marker sits on.
    scope: str
    frame: int
    color: str
    name: str
    note: str
    duration: int
    custom_data: str


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    info: TimelineInfo
    tracks: tuple[LiveTrack, ...]
    items: tuple[LiveItem, ...]
    markers: tuple[LiveMarker, ...]
    settings: tuple[tuple[str, str], ...] = ()

    def item_for(self, tag: str) -> LiveItem | None:
        return next((i for i in self.items if i.tag == tag), None)

    def item(self, item_id: str) -> LiveItem | None:
        return next((i for i in self.items if i.item_id == item_id), None)

    def track(self, kind: str, index: int) -> LiveTrack | None:
        return next((t for t in self.tracks if t.kind == kind and t.index == index), None)

    @property
    def duration(self) -> int:
        return max((i.end for i in self.items), default=0)


@dataclass(frozen=True, slots=True)
class PlaceRequest:
    """One clip to put on the timeline. Frames of the media are those of its own file."""

    element_id: str
    pool_item_id: str
    media_type: int  # 1 picture only, 2 sound only (Resolve's own code)
    track_index: int
    record_frame: int  # relative to the timeline start
    start_frame: int
    end_frame: int  # as Resolve wants it (see ``Quirks.end_frame_inclusive``)


@dataclass(frozen=True, slots=True)
class Placed:
    element_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """What this Resolve can render with: formats, and the codecs of a format."""

    formats: tuple[tuple[str, str], ...]  # (label, id)
    codecs: tuple[tuple[str, str], ...]  # (label, id) of the current format
    current_format: str = ""
    current_codec: str = ""


@dataclass(frozen=True, slots=True)
class RenderStatus:
    job_id: str
    #: Resolve's own word: Ready, Rendering, Complete, Failed, Cancelled.
    status: str
    percent: float | None = None
    error: str = ""
    seconds: float | None = None

    @property
    def finished(self) -> bool:
        return self.status.lower() in {"complete", "failed", "cancelled"}
