"""Domain -> DTO mapping (batched, so listing N assets costs O(1) group queries)."""

from collections.abc import Sequence

from media_house.modules.media_library.application.dto import (
    DerivationDto,
    MediaAssetDto,
    MediaGroupDto,
)
from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.media_group import MediaGroup
from media_house.modules.media_library.domain.repository import MediaGroupRepository


class AssetDtoMapper:
    """Maps assets to DTOs, attaching group membership with a single batched lookup."""

    def __init__(self, groups: MediaGroupRepository) -> None:
        self._groups = groups

    def one(self, asset: MediaAsset) -> MediaAssetDto:
        return self.many([asset])[0]

    def many(self, assets: Sequence[MediaAsset]) -> list[MediaAssetDto]:
        if not assets:
            return []
        memberships = self._groups.group_ids_for_assets([a.id.value for a in assets])
        return [_to_dto(a, memberships.get(a.id.value, ())) for a in assets]


def _to_dto(asset: MediaAsset, group_ids: tuple[str, ...]) -> MediaAssetDto:
    derivation = asset.derivation
    return MediaAssetDto(
        id=asset.id.value,
        media_type=asset.file.media_type,
        mime_type=asset.file.mime_type,
        extension=asset.file.extension,
        original_filename=asset.original_filename,
        display_name=asset.display_name.value,
        file_size=asset.file.file_size,
        checksum=asset.file.checksum.value,
        role=asset.role,
        status=asset.status,
        is_favorite=asset.is_favorite,
        source_type=asset.source.source_type,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        width=asset.file.width,
        height=asset.file.height,
        duration_seconds=asset.file.duration_seconds,
        technical=dict(asset.file.technical),
        metadata=dict(asset.metadata),
        tags=tuple(sorted(t.value for t in asset.tags)),
        group_ids=tuple(group_ids),
        source_url=asset.source.url,
        source_provider=asset.source.provider,
        derivation=(
            DerivationDto(
                source_asset_id=derivation.source_asset_id.value,
                operation=derivation.operation,
                version=derivation.version,
                config=dict(derivation.config),
                fingerprint=derivation.fingerprint.value,
            )
            if derivation
            else None
        ),
    )


def to_group_dto(group: MediaGroup, asset_count: int) -> MediaGroupDto:
    return MediaGroupDto(
        id=group.id.value,
        name=group.name.value,
        parent_id=group.parent_id.value if group.parent_id else None,
        description=group.description,
        asset_count=asset_count,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )
