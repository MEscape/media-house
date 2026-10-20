"""Use cases: remove a proxy on purpose, and prune what nothing serves any more.

Deleting is the most dangerous thing this module does, so every deletion follows one rule: a
library asset is deleted only if this module's own records say it created it, and only after the
record is marked PRUNING in a transaction that also checks nothing serves it or is producing it.
A PRUNING artifact cannot be adopted or activated, so a request racing the delete either finishes
first (the claim then skips its slot) or fails cleanly and asks again.

Two-phase, so a crash between the phases is repaired by the next call:

    1. mark PRUNING (database)  2. delete the library asset  3. forget the record (database)

Never touched: originals, assets of other modules, assets this module cannot prove it created
(reported as orphans), and artifacts whose slot has an attempt in flight.
"""

from dataclasses import dataclass

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.ports import ProxyStore, Scratch
from media_house.modules.proxy_manager.domain.attempt import DEFAULT_LEASE_SECONDS
from media_house.modules.proxy_manager.domain.errors import AttemptInFlight
from media_house.modules.proxy_manager.domain.records import ArtifactRecord
from media_house.modules.proxy_manager.domain.values import PROXY_OPERATION
from media_house.shared.errors import Err, InfrastructureError, NotFoundError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_SCRATCH_PREFIX = "proxy-"
_HAS_DERIVATIVES = "other assets were derived from it"
_DELETE_FAILED = "the library could not delete it; the next prune retries"


@dataclass(frozen=True, slots=True)
class RemovalDto:
    source_asset_id: str
    profile: str
    #: Library ids of the proxy files that were deleted.
    removed_asset_ids: tuple[str, ...]
    #: ``(library id, reason)`` of proxy files that could not be deleted.
    kept: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PruneReportDto:
    dry_run: bool
    #: Proxy files deleted (in a dry run: that would be deleted).
    pruned: tuple[str, ...]
    #: ``(library id, reason)`` of proxy files that could not be deleted.
    kept: tuple[tuple[str, str], ...]
    #: Records forgotten because the source or the proxy file no longer exists in the library.
    purged_records: int
    #: Proxy files in the library that this module cannot prove it created. Never deleted.
    orphan_assets: tuple[str, ...]
    #: ``proxy-*`` directories no attempt names. Never deleted.
    orphan_scratch: tuple[str, ...]


def release_pruned(library: MediaLibrary, store: ProxyStore, record: ArtifactRecord) -> str:
    """Phase 2 and 3 of a removal for an artifact already marked PRUNING.

    ``""`` when the file is gone and the record forgotten, else why not. Safe to repeat and safe
    when another process is doing the same: deleting an asset that is already gone is success.
    """
    try:
        deleted = library.delete(record.asset_id, include_derived=False)
    except InfrastructureError:
        _log.warning("Proxy file not deleted", asset_id=record.asset_id)
        return _DELETE_FAILED  # stays PRUNING: nothing can adopt it, the next prune finishes
    if isinstance(deleted, Err) and not isinstance(deleted.error, NotFoundError):
        store.abandon_prune(record.asset_id)  # someone derived from it: leave it be
        return _HAS_DERIVATIVES
    store.finish_prune(record.asset_id)
    return ""


class MaintainProxies:
    """Remove a slot's proxies, or prune the artifacts nothing serves or keeps."""

    def __init__(
        self,
        library: MediaLibrary,
        store: ProxyStore,
        scratch: Scratch,
        clock: Clock,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._library = library
        self._store = store
        self._scratch = scratch
        self._clock = clock
        self._lease = lease_seconds

    # ----------------------------------------------------------------------------------------
    def remove(self, source_asset_id: str, profile: str) -> Result[RemovalDto, AttemptInFlight]:
        """Take the proxy of ``(source, profile)`` out of service and delete its files.

        Refused while a request for that slot is running. The original is untouched.
        """
        self._retire_dead_owners()
        try:
            claimed = self._store.claim_slot(source_asset_id, profile)
        except AttemptInFlight as busy:
            return Err(busy)
        removed: list[str] = []
        kept: list[tuple[str, str]] = []
        for record in claimed:
            reason = self._delete(record)
            (kept.append((record.asset_id, reason)) if reason else removed.append(record.asset_id))
        _log.info(
            "Proxy removed", source_asset_id=source_asset_id, profile=profile, files=len(removed)
        )
        return Ok(RemovalDto(source_asset_id, profile, tuple(removed), tuple(kept)))

    def prune(self, *, dry_run: bool = True) -> PruneReportDto:
        """Delete artifacts no slot serves or keeps. A dry run (the default) deletes nothing."""
        if dry_run:
            pending = [r.asset_id for r in self._store.pruning_artifacts()]
            would = [r.asset_id for r in self._store.unreferenced_artifacts()]
            return self._report(True, [*pending, *would], [], 0)
        self._retire_dead_owners()
        purged = self._reconcile()
        pruned: list[str] = []
        kept: list[tuple[str, str]] = []
        # an earlier prune that stopped half way is finished first, then new work is claimed
        for record in [*self._store.pruning_artifacts(), *self._store.claim_unreferenced()]:
            reason = self._delete(record)
            (kept.append((record.asset_id, reason)) if reason else pruned.append(record.asset_id))
        _log.info("Proxies pruned", pruned=len(pruned), kept=len(kept), purged=purged)
        return self._report(False, pruned, kept, purged)

    # ----------------------------------------------------------------------------------------
    def _retire_dead_owners(self) -> None:
        """An attempt of a process that stopped responding must not protect its slot forever."""
        self._store.interrupt_foreign_attempts(now=self._clock.now(), lease_seconds=self._lease)

    def _delete(self, record: ArtifactRecord) -> str:
        return release_pruned(self._library, self._store, record)

    def _reconcile(self) -> int:
        """Forget records of things the library no longer has (it cascades deletes silently)."""
        purged = 0
        for source in self._store.known_sources():
            if self._missing(source):
                purged += self._store.purge_source(source)
        for record in self._store.list_artifacts():
            if self._missing(record.asset_id) and self._store.drop_record(record.asset_id):
                purged += 1
        return purged

    def _missing(self, asset_id: str) -> bool:
        return isinstance(err := self._library.get(asset_id), Err) and isinstance(
            err.error, NotFoundError
        )

    def _report(
        self, dry_run: bool, pruned: list[str], kept: list[tuple[str, str]], purged: int
    ) -> PruneReportDto:
        return PruneReportDto(
            dry_run=dry_run,
            pruned=tuple(pruned),
            kept=tuple(kept),
            purged_records=purged,
            orphan_assets=self._orphan_assets(),
            orphan_scratch=self._orphan_scratch(),
        )

    def _orphan_assets(self) -> tuple[str, ...]:
        found: list[str] = []
        for source in self._store.known_sources():
            for asset in self._library.list_derived(source):
                is_proxy = asset.metadata.get("processing_type") == PROXY_OPERATION
                if is_proxy and self._store.get_artifact(asset.id) is None:
                    found.append(asset.id)
        return tuple(sorted(found))

    def _orphan_scratch(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self._scratch.names()
            if self._store.get_attempt(name.removeprefix(_SCRATCH_PREFIX)) is None
        )
