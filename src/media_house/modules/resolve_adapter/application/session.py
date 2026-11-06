"""Reach the project and timeline an earlier execution bound, without changing anything in them."""

from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.domain.binding import SyncState
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.errors import ProjectConflict
from media_house.modules.resolve_adapter.domain.live import TimelineSnapshot
from media_house.modules.resolve_adapter.domain.values import (
    Area,
    ExecutionState,
    OperationStatus,
    Verification,
)


def open_bound(
    backend: ResolveBackend, state: SyncState, config: ResolveAdapterConfig, ledger: Ledger
) -> TimelineSnapshot:
    """Make the bound project and timeline the open ones (switching only with permission, the
    project that was open is saved first) and read the timeline."""
    ledger.enter(ExecutionState.VERIFYING_PROJECT)
    wanted = state.project.project_name
    current = backend.current_project()
    if current is None or current.name != wanted:
        if wanted not in backend.list_projects():
            raise ProjectConflict(f"the bound project {wanted!r} no longer exists in Resolve")
        if current is not None and not config.allow_project_switch:
            raise ProjectConflict(
                f"{current.name!r} is open; the bound project is {wanted!r}. "
                "Open it in Resolve, or allow switching projects"
            )
        if current is not None:
            backend.save_project()
        backend.open_project(wanted)
    backend.open_timeline(state.project.timeline_name)
    snapshot = backend.read_timeline()
    ledger.op(
        "timeline",
        Area.SYNC,
        "read the timeline",
        OperationStatus.EXECUTED,
        verification=Verification.VERIFIED,
        detail=f"{len(snapshot.items)} clips read",
    )
    ledger.confirm("snapshot.read")
    return snapshot
