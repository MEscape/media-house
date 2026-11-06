"""The Resolve Adapter PUBLIC API for other modules (import this file only).

``resolve_adapter`` is the only module that talks to DaVinci Resolve. It takes the built
timeline of ``timeline_builder`` and carries it out in a real Resolve project, reads it back to
verify it, reads a person's changes back into Media House, and renders and checks the result.
Nothing outside this module sees a Resolve object or its API.

    result = executor.execute(ExecuteTimelineCommand(timeline=built), ctx)   # send + verify
    report = result.value.report   # outcome: completed / with warnings / partial / failed
    synchronizer.execute(SyncCommand(anchor_asset_id=...), ctx)   # a person's fixes -> Media House
    renderer.execute(RenderCommand(anchor_asset_id=...), ctx)     # render + verify the file

Blocking calls: run them as jobs. A failure after Resolve was reached is a report with the state
it ended in, not an exception.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.resolve_adapter.application.audio_layout import (
    AudioClipRef,
    AudioLayout,
    AudioTrackRef,
    ResolveAudioLayouts,
)
from media_house.modules.resolve_adapter.application.execute_timeline import (
    ExecuteError,
    ExecuteTimelineCommand,
    ExecutionResult,
)
from media_house.modules.resolve_adapter.application.inspect_resolve import ResolveInspection
from media_house.modules.resolve_adapter.application.render_timeline import (
    RenderCommand,
    RenderResult,
)
from media_house.modules.resolve_adapter.application.sync_from_resolve import (
    SyncCommand,
    SyncResult,
)
from media_house.modules.resolve_adapter.domain.binding import (
    ConflictRecord,
    ElementBinding,
    FieldConflict,
    MediaBinding,
    ProjectBinding,
    SyncState,
)
from media_house.modules.resolve_adapter.domain.capabilities import (
    REGISTRY,
    CapabilityMatrix,
    DetectedCapability,
)
from media_house.modules.resolve_adapter.domain.config import (
    RENDER_PROFILES,
    RenderConfig,
    RenderProfile,
    ResolveAdapterConfig,
)
from media_house.modules.resolve_adapter.domain.environment import (
    ConnectionStatus,
    ResolveEnvironment,
    ResolveVersion,
)
from media_house.modules.resolve_adapter.domain.errors import (
    ConfigurationNotApplied,
    InvalidAdapterConfig,
    InvalidBindingDocument,
    NothingBound,
    ProjectConflict,
    RenderBlocked,
    ResolveAdapterError,
    ResolveCallFailed,
    ResolveConnectionLost,
    ResolveUnavailable,
    TimelineNotExecutable,
)
from media_house.modules.resolve_adapter.domain.report import (
    AlternativeRecord,
    CheckRecord,
    ConnectionRecord,
    ExecutionReport,
    MediaRecord,
    OperationRecord,
    RenderRecord,
    SettingRecord,
    SyncRecord,
)
from media_house.modules.resolve_adapter.domain.values import (
    ADAPTER_VERSION,
    REPORT_OPERATION,
    SCHEMA_VERSION,
    Area,
    CapabilityState,
    CaptionStrategy,
    ChangeOrigin,
    ConnectionState,
    ExecutionPolicy,
    ExecutionState,
    OperationStatus,
    Outcome,
    Resolution,
    RetimePlacement,
    Strategy,
    Verification,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ResolveExecutor(Protocol):
    def execute(
        self, command: ExecuteTimelineCommand, ctx: JobContext
    ) -> Result[ExecutionResult, ExecuteError]:
        """Carry out a built timeline in Resolve and verify it (idempotent, incremental)."""
        ...


class ResolveSynchronizer(Protocol):
    def execute(self, command: SyncCommand, ctx: JobContext) -> Result[SyncResult, ExecuteError]:
        """Read a person's changes in Resolve into Media House (the builder records them)."""
        ...


class ResolveRenderer(Protocol):
    def execute(
        self, command: RenderCommand, ctx: JobContext
    ) -> Result[RenderResult, ExecuteError]:
        """Render the bound timeline and verify the file."""
        ...


class ResolveInspector(Protocol):
    def execute(self) -> ResolveInspection:
        """Connection state, version, edition, capabilities, projects. Changes nothing."""
        ...


class ResolveCatalog(Protocol):
    """Stored links and reports; Resolve is not contacted."""

    def latest_binding(
        self, anchor_asset_id: str, timeline_id: str
    ) -> Result[tuple[SyncState, MediaAssetDto] | None, MediaError]: ...

    def reports(self, anchor_asset_id: str) -> list[MediaAssetDto]: ...


class AudioLayouts(Protocol):
    """Where each sound is in Resolve: the read side of the contract with ``audio_mix_engine``."""

    def layout(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> Result[AudioLayout, MediaError | NothingBound]: ...


__all__ = [
    "ADAPTER_VERSION",
    "REGISTRY",
    "RENDER_PROFILES",
    "REPORT_OPERATION",
    "SCHEMA_VERSION",
    "AlternativeRecord",
    "Area",
    "AudioClipRef",
    "AudioLayout",
    "AudioLayouts",
    "AudioTrackRef",
    "CapabilityMatrix",
    "CapabilityState",
    "CaptionStrategy",
    "ChangeOrigin",
    "CheckRecord",
    "ConfigurationNotApplied",
    "ConflictRecord",
    "ConnectionRecord",
    "ConnectionState",
    "ConnectionStatus",
    "DetectedCapability",
    "ElementBinding",
    "ExecuteError",
    "ExecuteTimelineCommand",
    "ExecutionPolicy",
    "ExecutionReport",
    "ExecutionResult",
    "ExecutionState",
    "FieldConflict",
    "InvalidAdapterConfig",
    "InvalidBindingDocument",
    "MediaBinding",
    "MediaRecord",
    "NothingBound",
    "OperationRecord",
    "OperationStatus",
    "Outcome",
    "ProjectBinding",
    "ProjectConflict",
    "RenderBlocked",
    "RenderCommand",
    "RenderConfig",
    "RenderProfile",
    "RenderRecord",
    "RenderResult",
    "Resolution",
    "ResolveAdapterConfig",
    "ResolveAdapterError",
    "ResolveAudioLayouts",
    "ResolveCallFailed",
    "ResolveCatalog",
    "ResolveConnectionLost",
    "ResolveEnvironment",
    "ResolveExecutor",
    "ResolveInspection",
    "ResolveInspector",
    "ResolveRenderer",
    "ResolveSynchronizer",
    "ResolveUnavailable",
    "ResolveVersion",
    "RetimePlacement",
    "SettingRecord",
    "Strategy",
    "SyncCommand",
    "SyncRecord",
    "SyncResult",
    "SyncState",
    "TimelineNotExecutable",
    "Verification",
]
