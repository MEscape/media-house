"""Read-only questions about what a slot serves. Never generates, never writes.

The condition of a slot is computed against the live library on every call: a stored flag could
claim a proxy is ready after its file was deleted.
"""

from media_house.modules.media_library.application.contracts import (
    AssetStatus,
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.proxy_manager.application.dto import ProxyDto
from media_house.modules.proxy_manager.application.ports import DiskSpace, ProxyStore
from media_house.modules.proxy_manager.application.provenance import ProxyProvenance
from media_house.modules.proxy_manager.domain.records import ArtifactRecord, ArtifactState, assess
from media_house.modules.proxy_manager.domain.values import VALIDATOR_VERSION, Condition, Evidence
from media_house.shared.errors import Err, InfrastructureError


class ProxyLookup:
    """Implements ``ProxyCatalog``; also the reuse check ``EnsureProxy`` starts with."""

    def __init__(self, library: MediaLibrary, store: ProxyStore, disk: DiskSpace) -> None:
        self._library = library
        self._store = store
        self._disk = disk

    def file_ok(self, asset: MediaAssetDto) -> bool:
        """The asset is usable: not archived, and the library still has its file at the size it
        recorded."""
        if asset.status is AssetStatus.ARCHIVED:
            return False
        try:
            path = self._library.local_path(asset.id)
        except InfrastructureError:  # the storage backend reports a missing blob by raising
            return False
        if isinstance(path, Err):
            return False
        return self._disk.size_bytes(path.value) == asset.file_size

    def find(self, source_asset_id: str, profile: str) -> ProxyDto | None:
        """The proxy the slot serves, whatever its condition, or ``None`` if nothing is served."""
        slot = self._store.get_slot(source_asset_id, profile)
        record = self._store.get_artifact(slot.active_asset_id) if slot else None
        if record is None:
            return None
        asset = self._library.get(record.asset_id)
        if isinstance(asset, Err):
            return None
        return self.dto(asset.value, record, self._condition(asset.value, record), reused=True)

    def condition(self, source_asset_id: str, profile: str) -> Condition:
        slot = self._store.get_slot(source_asset_id, profile)
        if slot is None:
            return Condition.NONE
        record = self._store.get_artifact(slot.active_asset_id)
        asset = self._library.get(slot.active_asset_id)
        if record is None or isinstance(asset, Err):
            return Condition.MISSING
        return self._condition(asset.value, record)

    def served(
        self, source_asset_id: str, profile: str, fingerprint: str, *, decoded: bool = False
    ) -> ProxyDto | None:
        """The active proxy iff it is exactly what a request with ``fingerprint`` would produce,
        is READY now and rests on the evidence asked for. A pure read: this is the path that
        reuses without any write."""
        slot = self._store.get_slot(source_asset_id, profile)
        record = self._store.get_artifact(slot.active_asset_id) if slot else None
        if record is None or record.spec_fingerprint != fingerprint:
            return None
        if decoded and record.evidence is not Evidence.DECODED:
            return None
        asset = self._library.get(record.asset_id)
        if isinstance(asset, Err):
            return None
        condition = self._condition(asset.value, record, wanted=fingerprint)
        if condition is not Condition.READY:
            return None
        return self.dto(asset.value, record, condition, reused=True)

    def _condition(
        self, asset: MediaAssetDto, record: ArtifactRecord, wanted: str | None = None
    ) -> Condition:
        if record.state is ArtifactState.PRUNING:
            return Condition.MISSING
        return assess(
            has_slot=True,
            asset_present=True,
            file_ok=self.file_ok(asset),
            verdict=record.verdict,
            validator_version=record.validator_version,
            artifact_fingerprint=record.spec_fingerprint,
            wanted_fingerprint=wanted or record.spec_fingerprint,
            validator_current=VALIDATOR_VERSION,
        )

    @staticmethod
    def dto(
        asset: MediaAssetDto, record: ArtifactRecord, condition: Condition, *, reused: bool
    ) -> ProxyDto:
        provenance = ProxyProvenance.from_metadata(asset.metadata)
        spec = provenance.spec if provenance else {}
        return ProxyDto(
            source_asset_id=record.source_asset_id,
            proxy_asset_id=record.asset_id,
            profile=record.profile,
            profile_version=provenance.profile_version if provenance else 0,
            spec_fingerprint=record.spec_fingerprint,
            condition=condition,
            verdict=record.verdict,
            evidence=record.evidence,
            codec=str(spec.get("codec", "unknown")),
            width=asset.width or 0,
            height=asset.height or 0,
            size_bytes=asset.file_size,
            transformations=provenance.transformations if provenance else (),
            warnings=provenance.warnings if provenance else (),
            encoder=provenance.encoder if provenance else None,
            toolchain=provenance.toolchain if provenance else None,
            reused=reused,
        )
