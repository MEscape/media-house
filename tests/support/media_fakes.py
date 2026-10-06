"""In-memory test doubles for the media library repositories.

Like a real adapter, they hand out re-hydrated copies (no live aggregates, no pending events)
and enforce the same invariants, so the repository contract tests run against both.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from media_house.modules.media_library.domain.errors import (
    DuplicateDerivative,
    DuplicateMedia,
    GroupNameTaken,
    GroupNotFound,
    MediaNotFound,
)
from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.media_group import MediaGroup
from media_house.modules.media_library.domain.query import (
    MediaQuery,
    Page,
    SortField,
    SortOrder,
    TagUsage,
)
from media_house.modules.media_library.domain.values import (
    Checksum,
    MediaAssetId,
    MediaGroupId,
    ProcessingFingerprint,
    StorageKey,
)


def _copy_asset(a: MediaAsset) -> MediaAsset:
    return MediaAsset(
        id=a.id,
        file=a.file,
        original_filename=a.original_filename,
        display_name=a.display_name,
        source=a.source,
        created_at=a.created_at,
        updated_at=a.updated_at,
        role=a.role,
        status=a.status,
        is_favorite=a.is_favorite,
        tags=a.tags,
        metadata=a.metadata,
        derivation=a.derivation,
    )


def _copy_group(g: MediaGroup) -> MediaGroup:
    return MediaGroup(
        id=g.id,
        name=g.name,
        parent_id=g.parent_id,
        description=g.description,
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


class InMemoryMediaStore:
    """Implements both ``MediaAssetRepository`` and ``MediaGroupRepository``."""

    def __init__(self) -> None:
        self._assets: dict[str, MediaAsset] = {}
        self._groups: dict[str, MediaGroup] = {}
        self._members: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------ assets
    def add(self, asset: MediaAsset | MediaGroup) -> None:  # type: ignore[override]
        if isinstance(asset, MediaGroup):
            self._add_group(asset)
            return
        if asset.derivation is None:
            if any(
                a.derivation is None and a.file.checksum == asset.file.checksum
                for a in self._assets.values()
            ):
                raise DuplicateMedia(asset.file.checksum.value)
        elif any(
            a.derivation and a.derivation.fingerprint == asset.derivation.fingerprint
            for a in self._assets.values()
        ):
            raise DuplicateDerivative(asset.derivation.fingerprint.value)
        self._assets[asset.id.value] = _copy_asset(asset)

    def save(self, asset: MediaAsset | MediaGroup) -> None:  # type: ignore[override]
        if isinstance(asset, MediaGroup):
            self._save_group(asset)
            return
        if asset.id.value not in self._assets:
            raise MediaNotFound(asset.id.value)
        self._assets[asset.id.value] = _copy_asset(asset)

    def get(self, item_id: MediaAssetId | MediaGroupId) -> MediaAsset | MediaGroup | None:  # type: ignore[override]
        if isinstance(item_id, MediaGroupId):
            group = self._groups.get(item_id.value)
            return _copy_group(group) if group else None
        stored = self._assets.get(item_id.value)
        return _copy_asset(stored) if stored else None

    def get_many(self, asset_ids: Sequence[MediaAssetId]) -> Sequence[MediaAsset]:
        seen = dict.fromkeys(a.value for a in asset_ids)
        return [_copy_asset(self._assets[i]) for i in seen if i in self._assets]

    def find_original_by_checksum(self, checksum: Checksum) -> MediaAsset | None:
        for a in self._assets.values():
            if a.derivation is None and a.file.checksum == checksum:
                return _copy_asset(a)
        return None

    def find_derived(
        self,
        source_asset_id: MediaAssetId,
        fingerprint: ProcessingFingerprint,
    ) -> MediaAsset | None:
        for a in self._assets.values():
            d = a.derivation
            if d and d.source_asset_id == source_asset_id and d.fingerprint == fingerprint:
                return _copy_asset(a)
        return None

    def search(self, query: MediaQuery) -> Page[MediaAsset]:
        matches = [a for a in self._assets.values() if self._matches(a, query)]
        matches.sort(key=lambda a: a.id.value)  # stable tie-break, as in SQL
        matches.sort(key=lambda a: _sort_key(a, query.sort_by), reverse=query.sort_order is SortOrder.DESC)
        window = matches[query.offset : query.offset + query.page_size]
        return Page([_copy_asset(a) for a in window], len(matches), query.page, query.page_size)

    def descendant_ids(self, asset_id: MediaAssetId) -> Sequence[MediaAssetId]:
        found: list[MediaAssetId] = []
        frontier = {asset_id.value}
        while frontier:
            children = [
                a.id
                for a in self._assets.values()
                if a.derivation and a.derivation.source_asset_id.value in frontier
            ]
            found += children
            frontier = {c.value for c in children}
        return found

    def delete(self, asset_ids: Sequence[MediaAssetId]) -> None:
        for asset_id in asset_ids:
            self._assets.pop(asset_id.value, None)
            self._members = {m for m in self._members if m[1] != asset_id.value}

    def count_storage_references(self, key: StorageKey) -> int:
        return sum(1 for a in self._assets.values() if a.file.storage_key == key)

    def list_tags(self, *, prefix: str | None, limit: int) -> Sequence[TagUsage]:
        counts: dict[str, int] = {}
        for a in self._assets.values():
            for tag in a.tags:
                if prefix is None or tag.value.startswith(prefix):
                    counts[tag.value] = counts.get(tag.value, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [TagUsage(name, n) for name, n in ordered[:limit]]

    # ------------------------------------------------------------------ groups
    def _add_group(self, group: MediaGroup) -> None:
        self._check_group_name(group)
        self._groups[group.id.value] = _copy_group(group)

    def _save_group(self, group: MediaGroup) -> None:
        if group.id.value not in self._groups:
            raise GroupNotFound(group.id.value)
        self._check_group_name(group)
        self._groups[group.id.value] = _copy_group(group)

    def _check_group_name(self, group: MediaGroup) -> None:
        for other in self._groups.values():
            if (
                other.id != group.id
                and other.parent_id == group.parent_id
                and other.name.key == group.name.key
            ):
                raise GroupNameTaken(group.name.value)

    def list_all(self) -> Sequence[MediaGroup]:
        ordered = sorted(self._groups.values(), key=lambda g: (g.name.key, g.id.value))
        return [_copy_group(g) for g in ordered]

    def has_children(self, group_id: MediaGroupId) -> bool:
        return any(g.parent_id == group_id for g in self._groups.values())

    def delete_group(self, group_id: MediaGroupId) -> None:
        self._groups.pop(group_id.value, None)
        self._members = {m for m in self._members if m[0] != group_id.value}

    def add_member(self, group_id: MediaGroupId, asset_id: MediaAssetId, *, now: datetime) -> bool:
        pair = (group_id.value, asset_id.value)
        if pair in self._members:
            return False
        self._members.add(pair)
        return True

    def remove_member(self, group_id: MediaGroupId, asset_id: MediaAssetId) -> bool:
        pair = (group_id.value, asset_id.value)
        if pair not in self._members:
            return False
        self._members.discard(pair)
        return True

    def group_ids_for_assets(self, asset_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        wanted = set(asset_ids)
        found: dict[str, list[str]] = {}
        for group_id, asset_id in sorted(self._members):
            if asset_id in wanted:
                found.setdefault(asset_id, []).append(group_id)
        return {k: tuple(v) for k, v in found.items()}

    def member_counts(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for group_id, _asset in self._members:
            counts[group_id] = counts.get(group_id, 0) + 1
        return counts

    # ------------------------------------------------------------------ search
    def _group_closure(self, roots: frozenset[str]) -> set[str]:
        closure = set(roots)
        changed = True
        while changed:
            changed = False
            for g in self._groups.values():
                if g.parent_id and g.parent_id.value in closure and g.id.value not in closure:
                    closure.add(g.id.value)
                    changed = True
        return closure

    def _matches(self, a: MediaAsset, q: MediaQuery) -> bool:  # noqa: PLR0911, C901
        if q.text and q.text not in a.display_name.key and q.text not in a.original_filename.casefold():
            return False
        if q.media_types and a.file.media_type not in q.media_types:
            return False
        if q.group_ids:
            groups = q.group_ids if not q.include_subgroups else self._group_closure(q.group_ids)
            if not any((g, a.id.value) in self._members for g in groups):
                return False
        if q.tags and not q.tags <= {t.value for t in a.tags}:
            return False
        if q.created_from and a.created_at < q.created_from:
            return False
        if q.created_to and a.created_at >= q.created_to:
            return False
        if q.favorite is not None and a.is_favorite != q.favorite:
            return False
        if q.statuses and a.status not in q.statuses:
            return False
        if q.roles and a.role not in q.roles:
            return False
        if q.source_types and a.source.source_type not in q.source_types:
            return False
        return not (
            q.source_asset_id is not None
            and (a.derivation is None or a.derivation.source_asset_id.value != q.source_asset_id)
        )


def _sort_key(a: MediaAsset, field: SortField) -> tuple[bool, object]:
    match field:
        case SortField.CREATED_AT:
            return (True, a.created_at)
        case SortField.UPDATED_AT:
            return (True, a.updated_at)
        case SortField.NAME:
            return (True, a.display_name.key)
        case SortField.FILE_SIZE:
            return (True, a.file.file_size)
        case SortField.DURATION:
            # SQLite: NULL is the smallest value.
            return (a.file.duration_seconds is not None, a.file.duration_seconds or 0.0)
