"""Use case: delete an asset (and, explicitly, everything derived from it)."""

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.dto import DeleteSummaryDto
from media_house.modules.media_library.application.ports import MediaStorage
from media_house.modules.media_library.domain.errors import MediaNotFound, MediaStorageError
from media_house.modules.media_library.domain.events import MediaAssetDeleted
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import MediaAssetId, StorageKey
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class DeleteMedia:
    """Permanently delete assets.

    Semantics (explicit on purpose):

    * Derivatives (processed variants, thumbnails) are deleted with their source by default;
      with ``include_derived=False`` the call is refused if any exist.
    * Tags and group memberships disappear with the asset (one transaction). Groups remain.
    * Metadata is removed first, files second. A blob is deleted only when no remaining
      asset references it (identical content is shared). If a file cannot be removed the
      records are already gone, so the key is reported in ``orphaned_storage_keys`` and
      logged instead of failing a delete that has succeeded.
    * To hide an asset without destroying it, use ``ManageAsset.archive``.
    """

    def __init__(
        self,
        repository: MediaAssetRepository,
        storage: MediaStorage,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._clock = clock
        self._events = events

    def execute(
        self,
        asset_id: str,
        *,
        include_derived: bool = True,
    ) -> Result[DeleteSummaryDto, NotFoundError | ConflictError]:
        root_id = MediaAssetId(asset_id) if asset_id else None
        if root_id is None or self._repository.get(root_id) is None:
            return Err(MediaNotFound(asset_id))
        descendants = list(self._repository.descendant_ids(root_id))
        if descendants and not include_derived:
            return Err(
                ConflictError(
                    f"Asset {asset_id} has {len(descendants)} derived assets",
                    user_message="This item has processed versions. Delete them together or archive it.",
                ),
            )
        doomed = self._repository.get_many([root_id, *descendants])
        keys = list(dict.fromkeys(a.file.storage_key for a in doomed))
        self._repository.delete([a.id for a in doomed])

        deleted_files = 0
        orphaned: list[str] = []
        for key in keys:
            if self._remove_blob_if_unreferenced(key):
                deleted_files += 1
            elif self._repository.count_storage_references(key) == 0:
                orphaned.append(key.value)
        now = self._clock.now()
        for asset in doomed:
            self._events.publish(
                MediaAssetDeleted(
                    occurred_at=now,
                    asset_id=asset.id.value,
                    media_type=asset.file.media_type.value,
                ),
            )
        _log.info(
            "Asset deleted",
            asset_id=asset_id,
            assets=len(doomed),
            files=deleted_files,
            orphaned=len(orphaned),
        )
        return Ok(
            DeleteSummaryDto(
                deleted_asset_ids=tuple(a.id.value for a in doomed),
                deleted_file_count=deleted_files,
                orphaned_storage_keys=tuple(orphaned),
            ),
        )

    def _remove_blob_if_unreferenced(self, key: StorageKey) -> bool:
        if self._repository.count_storage_references(key) > 0:
            return False
        try:
            self._storage.delete(key)
        except MediaStorageError:
            _log.warning("Could not delete a blob", storage_key=key.value)
            return False
        return True
