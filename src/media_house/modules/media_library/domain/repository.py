"""Repository ports. Implemented in ``infrastructure``; consumed by use cases."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.media_group import MediaGroup
from media_house.modules.media_library.domain.query import MediaQuery, Page, TagUsage
from media_house.modules.media_library.domain.values import (
    Checksum,
    MediaAssetId,
    MediaGroupId,
    ProcessingFingerprint,
    StorageKey,
)


class MediaAssetRepository(Protocol):
    """Persistence of assets and their tags.

    The database enforces the invariants: one *original* per checksum and one derivative per
    processing fingerprint. Writers that lose a race get the matching ``Duplicate*`` error.
    """

    def add(self, asset: MediaAsset) -> None:
        """Insert a new asset with its tags atomically.

        Raises :class:`DuplicateMedia` / :class:`DuplicateDerivative`.
        """
        ...

    def save(self, asset: MediaAsset) -> None:
        """Persist the mutable library state (name, favourite, status, tags, metadata).

        Raises :class:`MediaNotFound`.
        """
        ...

    def get(self, asset_id: MediaAssetId) -> MediaAsset | None: ...

    def get_many(self, asset_ids: Sequence[MediaAssetId]) -> Sequence[MediaAsset]:
        """Existing assets in the requested order; unknown ids are skipped. One round trip."""
        ...

    def find_original_by_checksum(self, checksum: Checksum) -> MediaAsset | None: ...

    def find_derived(
        self,
        source_asset_id: MediaAssetId,
        fingerprint: ProcessingFingerprint,
    ) -> MediaAsset | None: ...

    def search(self, query: MediaQuery) -> Page[MediaAsset]: ...

    def descendant_ids(self, asset_id: MediaAssetId) -> Sequence[MediaAssetId]:
        """Ids of every asset derived (transitively) from ``asset_id``, excluding itself."""
        ...

    def delete(self, asset_ids: Sequence[MediaAssetId]) -> None:
        """Delete assets with their tags and group memberships in one transaction."""
        ...

    def count_storage_references(self, key: StorageKey) -> int:
        """How many assets point at ``key`` (blobs are shared by identical content)."""
        ...

    def list_tags(self, *, prefix: str | None, limit: int) -> Sequence[TagUsage]: ...


class MediaGroupRepository(Protocol):
    """Persistence of groups and of the asset <-> group many-to-many relation."""

    def add(self, group: MediaGroup) -> None:
        """Raises :class:`GroupNameTaken` when a sibling already uses the name."""
        ...

    def save(self, group: MediaGroup) -> None:
        """Persist a rename. Raises :class:`GroupNameTaken` / :class:`GroupNotFound`."""
        ...

    def get(self, group_id: MediaGroupId) -> MediaGroup | None: ...

    def list_all(self) -> Sequence[MediaGroup]:
        """Every group, ordered by name. Group counts are small by nature."""
        ...

    def has_children(self, group_id: MediaGroupId) -> bool: ...

    def delete(self, group_id: MediaGroupId) -> None:
        """Delete the group and its memberships. Assets are untouched."""
        ...

    def add_member(self, group_id: MediaGroupId, asset_id: MediaAssetId, *, now: datetime) -> bool:
        """Idempotent. Returns ``True`` when the membership is new."""
        ...

    def remove_member(self, group_id: MediaGroupId, asset_id: MediaAssetId) -> bool:
        """Returns ``True`` when a membership was removed. Never touches the asset."""
        ...

    def group_ids_for_assets(self, asset_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        """Batch lookup (one query) so listing N assets does not cost N queries."""
        ...

    def member_counts(self) -> Mapping[str, int]:
        """Direct member count per group id (groups without members may be absent)."""
        ...
