"""The MediaLibrary facade: a thin, flat API over the individual use cases."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from media_house.modules.media_library.application.commands import (
    ImportMediaCommand,
    RegisterDerivedCommand,
)
from media_house.modules.media_library.application.delete_media import DeleteMedia
from media_house.modules.media_library.application.derived_assets import RegisterDerivedAsset
from media_house.modules.media_library.application.dto import (
    DeleteSummaryDto,
    DerivedAssetResultDto,
    ImportResultDto,
    MediaAssetDto,
    MediaGroupDto,
    TagDto,
)
from media_house.modules.media_library.application.groups import ManageGroups
from media_house.modules.media_library.application.import_media import ImportMedia
from media_house.modules.media_library.application.manage_assets import ManageAsset
from media_house.modules.media_library.application.queries import MediaQueries
from media_house.modules.media_library.application.thumbnails import (
    DEFAULT_THUMBNAIL_SIZE,
    Thumbnails,
)
from media_house.modules.media_library.domain.query import MediaQuery, Page
from media_house.modules.media_library.domain.values import JsonValue, SourceType
from media_house.shared.errors import ConflictError, NotFoundError, Result, ValidationError

type _Error = ValidationError | NotFoundError | ConflictError


class MediaLibraryService:
    """Implements the ``MediaLibrary`` contract by delegating; contains no logic of its own."""

    def __init__(
        self,
        *,
        importer: ImportMedia,
        derived: RegisterDerivedAsset,
        queries: MediaQueries,
        assets: ManageAsset,
        groups: ManageGroups,
        deleter: DeleteMedia,
        thumbnails: Thumbnails,
    ) -> None:
        self._importer = importer
        self._derived = derived
        self._queries = queries
        self._assets = assets
        self._groups = groups
        self._deleter = deleter
        self._thumbnails = thumbnails

    # --- import
    def import_file(
        self,
        path: Path,
        *,
        display_name: str | None = None,
        group_ids: Sequence[str] = (),
        tags: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
        source_type: SourceType = SourceType.LOCAL_FILE,
        source_url: str | None = None,
        source_provider: str | None = None,
    ) -> Result[ImportResultDto, _Error]:
        return self._importer.execute(
            ImportMediaCommand(
                path=path,
                display_name=display_name,
                group_ids=group_ids,
                tags=tags,
                metadata=metadata or {},
                source_type=source_type,
                source_url=source_url,
                source_provider=source_provider,
            ),
        )

    # --- lookup
    def get(self, asset_id: str) -> Result[MediaAssetDto, _Error]:
        return self._queries.get(asset_id)

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]:
        return self._queries.get_many(asset_ids)

    def find_by_checksum(self, checksum: str) -> MediaAssetDto | None:
        return self._queries.find_by_checksum(checksum)

    def search(self, query: MediaQuery | None = None) -> Page[MediaAssetDto]:
        return self._queries.search(query or MediaQuery())

    def local_path(self, asset_id: str) -> Result[Path, _Error]:
        return self._queries.local_path(asset_id)

    # --- derived assets
    def fingerprint(
        self,
        source_asset_id: str,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
    ) -> Result[str, _Error]:
        return self._queries.fingerprint(source_asset_id, operation, config, version)

    def find_derived_asset(
        self,
        source_asset_id: str,
        processing_fingerprint: str,
    ) -> MediaAssetDto | None:
        return self._queries.find_derived_asset(source_asset_id, processing_fingerprint)

    def register_derived(
        self,
        source_asset_id: str,
        path: Path,
        *,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
        display_name: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> Result[DerivedAssetResultDto, _Error]:
        return self._derived.execute(
            RegisterDerivedCommand(
                source_asset_id=source_asset_id,
                path=path,
                operation=operation,
                config=config,
                version=version,
                display_name=display_name,
                metadata=metadata or {},
            ),
        )

    def list_derived(self, source_asset_id: str) -> list[MediaAssetDto]:
        return self._queries.list_derived(source_asset_id)

    # --- previews
    def thumbnail(
        self,
        asset_id: str,
        max_size: int = DEFAULT_THUMBNAIL_SIZE,
    ) -> Result[MediaAssetDto | None, _Error]:
        return self._thumbnails.find(asset_id, max_size)

    def ensure_thumbnail(
        self,
        asset_id: str,
        max_size: int = DEFAULT_THUMBNAIL_SIZE,
    ) -> Result[MediaAssetDto, _Error]:
        return self._thumbnails.ensure(asset_id, max_size)

    # --- library state
    def rename(self, asset_id: str, name: str) -> Result[MediaAssetDto, _Error]:
        return self._assets.rename(asset_id, name)

    def set_favorite(self, asset_id: str, favorite: bool) -> Result[MediaAssetDto, _Error]:
        return self._assets.set_favorite(asset_id, favorite)

    def archive(self, asset_id: str) -> Result[MediaAssetDto, _Error]:
        return self._assets.archive(asset_id)

    def restore(self, asset_id: str) -> Result[MediaAssetDto, _Error]:
        return self._assets.restore(asset_id)

    def update_metadata(
        self,
        asset_id: str,
        patch: Mapping[str, JsonValue],
    ) -> Result[MediaAssetDto, _Error]:
        return self._assets.update_metadata(asset_id, patch)

    def add_tags(self, asset_id: str, tags: Sequence[str]) -> Result[MediaAssetDto, _Error]:
        return self._assets.add_tags(asset_id, tags)

    def remove_tags(self, asset_id: str, tags: Sequence[str]) -> Result[MediaAssetDto, _Error]:
        return self._assets.remove_tags(asset_id, tags)

    def list_tags(self, *, prefix: str | None = None, limit: int = 100) -> list[TagDto]:
        return self._queries.list_tags(prefix=prefix, limit=limit)

    # --- groups
    def create_group(
        self,
        name: str,
        *,
        parent_id: str | None = None,
        description: str = "",
    ) -> Result[MediaGroupDto, _Error]:
        return self._groups.create(name, parent_id=parent_id, description=description)

    def rename_group(self, group_id: str, name: str) -> Result[MediaGroupDto, _Error]:
        return self._groups.rename(group_id, name)

    def delete_group(self, group_id: str) -> Result[None, _Error]:
        return self._groups.delete(group_id)

    def list_groups(self) -> list[MediaGroupDto]:
        return self._groups.list_groups()

    def add_to_group(self, asset_id: str, group_id: str) -> Result[bool, _Error]:
        return self._groups.add_asset(group_id, asset_id)

    def remove_from_group(self, asset_id: str, group_id: str) -> Result[bool, _Error]:
        return self._groups.remove_asset(group_id, asset_id)

    # --- deletion
    def delete(
        self, asset_id: str, *, include_derived: bool = True
    ) -> Result[DeleteSummaryDto, _Error]:
        return self._deleter.execute(asset_id, include_derived=include_derived)
