"""Look at the Resolve installation without changing anything: is it reachable, which version and
edition, which operations does it offer, which projects exist.

This is what a person (or the pipeline) asks first. A reachable process is not a connection: the
state is ``connected`` only when a real call came back.
"""

from dataclasses import dataclass

from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.domain.capabilities import CapabilityMatrix, detect
from media_house.modules.resolve_adapter.domain.environment import ConnectionStatus


@dataclass(frozen=True, slots=True)
class ResolveInspection:
    status: ConnectionStatus
    capabilities: CapabilityMatrix
    projects: tuple[str, ...]
    current_project: str | None


class InspectResolve:
    def __init__(self, backend: ResolveBackend) -> None:
        self._backend = backend

    def execute(self) -> ResolveInspection:
        status = self._backend.connect()
        if not status.connected:
            return ResolveInspection(status, detect(status.environment), (), None)
        with self._backend.exclusive():
            env = self._backend.probe()
            current = self._backend.current_project()
            projects = self._backend.list_projects()
        refreshed = ConnectionStatus(
            status.state, status.detail, status.installed, status.running, env
        )
        return ResolveInspection(
            refreshed,
            detect(env, frozenset({"connection.external_scripting"})),
            projects,
            current.name if current else None,
        )
