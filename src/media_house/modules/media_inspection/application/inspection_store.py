"""Finds and stores inspections as derived JSON documents of their source asset.

Media Inspection owns no storage: the Media Library keys the document by source content +
operation + version + canonical configuration, so an identical inspection is answered from the
library (also after a restart) and a different threshold, depth or tool version is a new one.
"""

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.errors import MediaInspectionError
from media_house.modules.media_inspection.domain.model import MediaInspection
from media_house.modules.media_inspection.domain.serialization import from_json, to_json
from media_house.modules.media_inspection.domain.values import (
    INSPECTION_OPERATION,
    INSPECTION_VERSION,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_FILENAME = "media_inspection.json"


class InspectionStore:
    """Read and write side of the cache; knows nothing about how inspecting works."""

    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._paths = paths

    def fingerprint(
        self, source_id: str, config: InspectionConfig, probe_identity: str
    ) -> Result[str, MediaError]:
        return self._library.fingerprint(
            source_id,
            INSPECTION_OPERATION,
            config.fingerprint_config(probe_identity),
            INSPECTION_VERSION,
        )

    def load(
        self, source_id: str, fingerprint: str
    ) -> tuple[MediaInspection, MediaAssetDto] | None:
        """The stored inspection and its asset, or ``None`` if absent or unusable."""
        asset = self._library.find_derived_asset(source_id, fingerprint)
        if asset is None:
            return None
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            _log.warning("Stored inspection missing, inspecting again", asset_id=asset.id)
            self._discard(asset)
            return None
        try:
            return from_json(path.value.read_text(encoding="utf-8")), asset
        except (OSError, UnicodeDecodeError, MediaInspectionError) as exc:
            _log.warning(
                "Stored inspection unusable, inspecting again", asset_id=asset.id, reason=str(exc)
            )
            self._discard(asset)
            return None

    def _discard(self, asset: MediaAssetDto) -> None:
        """Remove a cache entry that cannot be read, so a fresh one can take its fingerprint.

        The library answers an identical registration with the existing asset, so a damaged
        document would otherwise be found, rejected and recomputed on every request.
        """
        removed = self._library.delete(asset.id, include_derived=False)
        if isinstance(removed, Err):
            _log.warning("Damaged inspection could not be removed", asset_id=asset.id)

    def save(
        self,
        source: MediaAssetDto,
        inspection: MediaInspection,
        config: InspectionConfig,
        probe_identity: str,
    ) -> Result[MediaAssetDto, MediaError]:
        with self._paths.temporary_directory(prefix="inspection-") as tmp:
            document = tmp / _FILENAME
            document.write_text(to_json(inspection), encoding="utf-8")
            registered = self._library.register_derived(
                source.id,
                document,
                operation=INSPECTION_OPERATION,
                config=config.fingerprint_config(probe_identity),
                version=INSPECTION_VERSION,
                display_name=f"{source.display_name} - inspection"[:255],
                metadata={
                    "processing_type": INSPECTION_OPERATION,
                    "source_asset_id": source.id,
                    "verdict": inspection.status.verdict.value,
                    "usable": inspection.status.usable,
                    "concerns": [c.value for c in inspection.status.concerns],
                    "depth": inspection.depth.value,
                },
            )
        if isinstance(registered, Err):
            return registered
        return Ok(registered.value.asset)
