"""DaVinci Resolve status, read through ``resolve_adapter``'s ``ResolveInspector``.

The inspection changes nothing in Resolve."""

from dataclasses import dataclass

from media_house.modules.resolve_adapter.application.contracts import (
    CapabilityState,
    ConnectionState,
    ResolveInspector,
)

NEVER_VERIFIED_NOTE = (
    "resolve_adapter has never been run against a real DaVinci Resolve. Its behaviour is assumed "
    "from Resolve's scripting documentation; the first run on a real installation is the real test."
)

#: Capability states that mean the operation may be attempted (``CapabilityMatrix.usable``).
USABLE_STATES = frozenset(
    {
        CapabilityState.SUPPORTED,
        CapabilityState.PARTIALLY_SUPPORTED,
        CapabilityState.NOT_VERIFIED,
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityRow:
    operation: str
    state: str
    usable: bool
    summary: str
    reason: str


@dataclass(frozen=True, slots=True)
class ResolveStatus:
    connected: bool
    state: str
    detail: str
    installed: bool
    running: bool
    product: str
    version: str
    edition: str
    current_project: str
    projects: tuple[str, ...]
    capabilities: tuple[CapabilityRow, ...]


class CheckResolveStatus:
    """One look at the Resolve installation. Blocking (loads Resolve's scripting module): run it
    as a job."""

    def __init__(self, inspector: ResolveInspector) -> None:
        self._inspector = inspector

    def execute(self) -> ResolveStatus:
        inspection = self._inspector.execute()
        status = inspection.status
        env = status.environment
        return ResolveStatus(
            connected=status.state is ConnectionState.CONNECTED,
            state=status.state.value,
            detail=status.detail,
            installed=status.installed,
            running=status.running,
            product=env.product if env else "",
            version=env.version.text if env and env.version else "",
            edition=env.edition if env else "",
            current_project=inspection.current_project or "",
            projects=inspection.projects,
            capabilities=tuple(
                CapabilityRow(
                    c.operation,
                    c.state.value,
                    c.state in USABLE_STATES,
                    c.summary,
                    c.reason,
                )
                for c in inspection.capabilities.items
            ),
        )
