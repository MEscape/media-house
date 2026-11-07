"""What the render manager needs from the outside, and nothing more.

``RenderBackend`` is the one port to whatever renders a timeline (Resolve today, through
``resolve_adapter``). It says what it CAN do (``BackendCapabilities``) so the manager never asks for
a range, a pause or a setting the backend would silently ignore. ``SectionCutter`` cuts a stretch
of a finished render; ``DiskProbe`` reads free space. Both are infrastructure.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.concurrency import JobContext


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    #: Can render a stretch of the timeline (else a section is cut out of a full render).
    range_render: bool
    #: Can pause and resume a running render.
    pause_resume: bool
    #: The backend's own profile keys it can realise (a render profile names one).
    profiles: frozenset[str]
    #: Profile settings it applies itself: ``container``, ``codec``, ``audio_codec``, ``size``...
    applies: frozenset[str]


@dataclass(frozen=True, slots=True)
class BackendIdentity:
    name: str
    #: The backend's version, or what is known when it cannot be reached.
    version: str
    capabilities: BackendCapabilities


@dataclass(frozen=True, slots=True)
class BackendRequest:
    anchor_asset_id: str
    timeline_id: str
    #: The backend's own profile key (from the render profile's ``resolve_profile``).
    native_profile: str
    #: The output name (no extension): unique, versioned.
    file_name: str
    #: Block on a proxy or an open conflict (always true for a final render).
    require_final_quality: bool
    #: Serve a stored identical render instead of rendering (never for ``overwrite``).
    reuse: bool
    start_frame: int | None = None
    end_frame: int | None = None


@dataclass(frozen=True, slots=True)
class BackendResult:
    #: ``completed`` | ``failed`` | ``cancelled``
    state: str
    output: MediaAssetDto | None
    reused: bool = False
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    #: What the backend did about each setting it was asked for (``applied`` per setting name).
    detail: str = ""
    extra: tuple[tuple[str, str], ...] = field(default_factory=tuple)


class RenderBackend(Protocol):
    def identity(self) -> BackendIdentity: ...

    def render(self, request: BackendRequest, ctx: JobContext) -> BackendResult:
        """Render and register the file. A failure is a result (state ``failed``)."""
        ...

    def pause(self) -> bool:
        """Pause the running render; ``False`` when it cannot (see ``capabilities``)."""
        ...

    def resume(self) -> bool: ...


class SectionCutter(Protocol):
    def cut(
        self,
        source: Path,
        *,
        start_seconds: float,
        end_seconds: float,
        destination: Path,
        ctx: JobContext,
    ) -> None:
        """Write the stretch ``[start, end)`` of ``source`` as a small review file."""
        ...


class DiskProbe(Protocol):
    def free_bytes(self, path: Path) -> int | None:
        """Free space where ``path`` lives, or ``None`` if it cannot be read."""
        ...
