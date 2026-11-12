"""The port to a running DaVinci Resolve. Owned by the application (it needs the capability);
implemented by the infrastructure adapter that drives Resolve's scripting API.

Everything crossing this port is a plain record from ``domain/live.py`` or
``domain/environment.py``: Resolve's own objects never leave the adapter. Methods raise
``ResolveCallFailed`` when Resolve answers a call with a failure, and ``ResolveConnectionLost``
when Resolve went away; a failed call is never taken to mean "nothing happened" (the caller
reads the state again).
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from media_house.modules.resolve_adapter.domain.environment import (
    ConnectionStatus,
    ResolveEnvironment,
)
from media_house.modules.resolve_adapter.domain.facts import BlurKey, ZoomKey
from media_house.modules.resolve_adapter.domain.grade import Cdl
from media_house.modules.resolve_adapter.domain.live import (
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


class ResolveBackend(Protocol):
    # --- connection -------------------------------------------------------------------------
    def exclusive(self) -> AbstractContextManager[None]:
        """One caller at a time: two executions never drive the same Resolve together."""
        ...

    def connect(self) -> ConnectionStatus:
        """Install check, process check, then a real call. ``CONNECTED`` only if it answered."""
        ...

    def disconnect(self) -> None: ...

    def alive(self) -> bool:
        """A cheap real call: does Resolve still answer?"""
        ...

    def probe(self) -> ResolveEnvironment:
        """The environment again, with the methods of every object reachable now."""
        ...

    # --- projects ---------------------------------------------------------------------------
    def current_project(self) -> ProjectInfo | None: ...

    def list_projects(self) -> tuple[str, ...]: ...

    def create_project(self, name: str) -> ProjectInfo: ...

    def open_project(self, name: str) -> ProjectInfo: ...

    def save_project(self) -> None: ...

    def export_project(self, name: str, path: Path) -> None: ...

    def get_settings(self, names: Sequence[str]) -> dict[str, str]: ...

    def set_settings(self, values: Mapping[str, str]) -> dict[str, bool]: ...

    # --- media pool -------------------------------------------------------------------------
    def pool_items(self) -> tuple[PoolItem, ...]: ...

    def ensure_folder(self, path: tuple[str, ...]) -> None: ...

    def import_media(
        self, paths: Sequence[Path], folder: tuple[str, ...]
    ) -> tuple[PoolItem, ...]: ...

    def link_proxy(self, item_id: str, path: Path) -> bool: ...

    def relink(self, item_ids: Sequence[str], folder: Path) -> bool: ...

    # --- timelines --------------------------------------------------------------------------
    def list_timelines(self) -> tuple[TimelineInfo, ...]: ...

    def create_timeline(self, name: str, start_timecode: str) -> TimelineInfo: ...

    def open_timeline(self, name: str) -> TimelineInfo: ...

    def duplicate_timeline(self, name: str, new_name: str) -> TimelineInfo: ...

    def rename_timeline(self, name: str, new_name: str) -> None: ...

    def delete_timeline(self, name: str) -> bool: ...

    def ensure_tracks(self, video: int, audio: int, audio_layout: str) -> None: ...

    def name_track(self, kind: str, index: int, name: str) -> bool: ...

    def place(self, requests: Sequence[PlaceRequest]) -> tuple[Placed, ...]: ...

    def set_item(
        self, item_id: str, properties: Mapping[str, float], enabled: bool | None
    ) -> dict[str, bool]: ...

    def delete_items(self, item_ids: Sequence[str]) -> None: ...

    def link_items(self, item_ids: Sequence[str]) -> bool: ...

    def grade_item(self, item_id: str, grade: Cdl) -> bool:
        """Set ``grade`` as the CDL of the clip's first colour node."""
        ...

    def animate_item(
        self,
        item_id: str,
        keys: Sequence[ZoomKey],
        blur: Sequence[BlurKey] = (),
        card: tuple[float, float, float, float] | None = None,
    ) -> bool:
        """Build the clip's Fusion composition: the framing (zoom ``keys``, or the page ``card``
        floated over a dark ground and scaled by ``keys``), then the ``blur``; ``True`` when
        every keyframed input reads back."""
        ...

    def add_marker(
        self,
        scope: str,
        frame: int,
        color: str,
        name: str,
        note: str,
        duration: int,
        custom: str,
    ) -> bool: ...

    def remove_marker(self, scope: str, custom: str) -> bool: ...

    def read_timeline(self) -> TimelineSnapshot: ...

    # --- render -----------------------------------------------------------------------------
    def render_options(self, format_id: str | None) -> RenderOptions: ...

    def configure_render(
        self, format_id: str, codec_id: str, settings: Mapping[str, SettingValue]
    ) -> dict[str, bool]: ...

    def submit_render(self) -> str: ...

    def render_status(self, job_id: str) -> RenderStatus: ...

    def stop_render(self) -> None: ...


class PictureMeter(Protocol):
    """The average colour of a recording's picture (normalised RGB), ``None`` when unknown."""

    def mean_rgb(self, path: Path) -> tuple[float, float, float] | None: ...
