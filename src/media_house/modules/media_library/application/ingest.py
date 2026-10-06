"""Shared ingest pipeline: inspect -> store blob -> persist metadata, with safe cleanup.

Used by every use case that brings new bytes into the library (import, derived assets,
thumbnails) so the consistency rules live in exactly one place.

Consistency model
-----------------
1. The blob is written first, atomically and content-addressed (idempotent).
2. The metadata row is inserted in one transaction; the database enforces uniqueness.
3. If step 2 fails, the blob is removed *only if no asset references it* (blobs are shared
   by identical content), so a failed or lost-race import never leaves an orphan file and
   never removes a file another asset needs.
4. After a successful insert the blob's presence is re-checked and healed, which closes the
   narrow window where a concurrent failed import of identical content cleaned it up.
"""

from collections.abc import Callable
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.ports import (
    InspectedMedia,
    MediaInspector,
    MediaStorage,
)
from media_house.modules.media_library.domain.errors import (
    DuplicateDerivative,
    DuplicateMedia,
    MediaStorageError,
)
from media_house.modules.media_library.domain.media_asset import MediaAsset, MediaFileInfo
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import StorageKey
from media_house.shared.errors import PersistenceError
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class AssetIngestor:
    """Coordinates inspector, storage and repository for new assets."""

    def __init__(
        self,
        repository: MediaAssetRepository,
        storage: MediaStorage,
        inspector: MediaInspector,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._inspector = inspector
        self._events = events
        self.clock = clock

    def inspect(self, path: Path) -> InspectedMedia:
        return self._inspector.inspect(path)

    @staticmethod
    def file_info(inspected: InspectedMedia) -> MediaFileInfo:
        """Describe the file as it will be stored (content-addressed key)."""
        return MediaFileInfo(
            media_type=inspected.media_type,
            mime_type=inspected.mime_type,
            extension=inspected.extension,
            file_size=inspected.file_size,
            checksum=inspected.checksum,
            storage_key=StorageKey.for_content(
                inspected.media_type,
                inspected.checksum,
                inspected.extension,
            ),
            width=inspected.width,
            height=inspected.height,
            duration_seconds=inspected.duration_seconds,
            technical=inspected.technical,
        )

    def place(self, path: Path, info: MediaFileInfo) -> None:
        """Write the blob. Raises ``MediaStorageError`` (also when the file changed)."""
        self._storage.store(path, info.storage_key, expected_checksum=info.checksum)

    def commit(
        self,
        asset: MediaAsset,
        source_path: Path,
        lookup_existing: Callable[[], MediaAsset | None],
    ) -> tuple[MediaAsset, bool]:
        """Persist ``asset``. Returns ``(asset, True)`` or ``(winner, False)`` on a lost race."""
        key = asset.file.storage_key
        try:
            self._repository.add(asset)
        except (DuplicateMedia, DuplicateDerivative):
            self._discard_if_unreferenced(key)
            existing = lookup_existing()
            if existing is None:
                raise
            _log.info("Duplicate detected", asset_id=existing.id.value)
            return existing, False
        except Exception:
            self._discard_if_unreferenced(key)
            raise
        if not self._storage.exists(key):  # heal: see module docstring, point 4
            self._storage.store(source_path, key, expected_checksum=asset.file.checksum)
        for event in asset.pull_events():
            self._events.publish(event)
        return asset, True

    def _discard_if_unreferenced(self, key: StorageKey) -> None:
        try:
            if self._repository.count_storage_references(key) == 0:
                self._storage.delete(key)
        except (MediaStorageError, PersistenceError):
            _log.warning("Could not clean up an unreferenced blob", storage_key=key.value)
