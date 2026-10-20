"""The Proxy Manager PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``proxy_manager``.

A proxy is a replaceable DERIVATIVE of an original asset. The original is never modified and its
id never changes; ``ProxyDto.proxy_asset_id`` may change when a proxy is replaced, so store the
source id and ask the catalog, never a proxy id.

* ``ProxyManager.execute`` is blocking (FFmpeg, file I/O): run it as a job.
* ``ProxyCatalog`` only looks up; it never generates and never writes.

Reading a ``ProxyDto``: ``verdict`` says whether the proxy keeps its profile's promises and
``evidence`` says what that rests on. ``valid`` with evidence ``packets`` means the file is
complete and consistent with its spec; it does NOT prove every frame decodes (``decoded`` does).
``transformations`` lists deliberate departures from the source timeline.

Typical use::

    result = manager.execute(EnsureProxyCommand(clip_id, profile="analysis"), ctx)
    if isinstance(result, Ok):
        path = library.local_path(result.value.proxy_asset_id)   # use it, do not store it
"""

from collections.abc import Mapping
from typing import Protocol

from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.dto import ProxyDto
from media_house.modules.proxy_manager.application.ensure_proxy import ProxyManagerError
from media_house.modules.proxy_manager.application.maintain_proxies import (
    PruneReportDto,
    RemovalDto,
)
from media_house.modules.proxy_manager.application.provenance import ProxyProvenance
from media_house.modules.proxy_manager.domain.errors import (
    ArtifactBeingRemoved,
    ArtifactUnrepairable,
    AttemptInFlight,
    InsufficientSpace,
    InvalidProfile,
    OutputExceedsReservation,
    ProxyError,
    SharedAttemptFailed,
    Superseded,
    UnsupportedSource,
    Unverifiable,
    ValidationFailed,
    WaitTimedOut,
)
from media_house.modules.proxy_manager.domain.profile import DEFAULT_PROFILE, PROFILES
from media_house.modules.proxy_manager.domain.values import (
    METADATA_KEY,
    Condition,
    Evidence,
    JsonValue,
    Verdict,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ProxyManager(Protocol):
    """Make sure a valid proxy exists for a source and is the one served."""

    def execute(
        self, command: EnsureProxyCommand, ctx: JobContext
    ) -> Result[ProxyDto, ProxyManagerError]: ...


class ProxyMaintenance(Protocol):
    """Take proxies out of service, and delete what nothing serves any more."""

    def remove(self, source_asset_id: str, profile: str) -> Result[RemovalDto, AttemptInFlight]:
        """Remove the proxy of one slot and delete its files; refused while a request runs."""
        ...

    def prune(self, *, dry_run: bool = True) -> PruneReportDto:
        """Delete artifacts no slot serves or keeps. Blocking; a dry run deletes nothing."""
        ...


class ProxyCatalog(Protocol):
    """Read-only questions about the proxy a slot (source + profile) serves."""

    def find(self, source_asset_id: str, profile: str) -> ProxyDto | None:
        """The served proxy whatever its condition, or ``None`` if none is served or it is gone."""
        ...

    def condition(self, source_asset_id: str, profile: str) -> Condition: ...


def read_proxy_provenance(metadata: Mapping[str, JsonValue]) -> ProxyProvenance | None:
    """How a proxy asset was made, from its library metadata; ``None`` means "unknown"."""
    return ProxyProvenance.from_metadata(metadata)


def profile_names() -> tuple[str, ...]:
    return tuple(sorted(PROFILES))


__all__ = [
    "DEFAULT_PROFILE",
    "METADATA_KEY",
    "ArtifactBeingRemoved",
    "ArtifactUnrepairable",
    "AttemptInFlight",
    "Condition",
    "EnsureProxyCommand",
    "Evidence",
    "InsufficientSpace",
    "InvalidProfile",
    "OutputExceedsReservation",
    "ProxyCatalog",
    "ProxyDto",
    "ProxyError",
    "ProxyMaintenance",
    "ProxyManager",
    "ProxyManagerError",
    "ProxyProvenance",
    "PruneReportDto",
    "RemovalDto",
    "SharedAttemptFailed",
    "Superseded",
    "UnsupportedSource",
    "Unverifiable",
    "ValidationFailed",
    "Verdict",
    "WaitTimedOut",
    "profile_names",
    "read_proxy_provenance",
]
