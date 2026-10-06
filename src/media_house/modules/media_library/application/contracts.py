"""The Media Library's PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``media_library``.
Keep it small and stable: it is a promise.

Conventions
-----------
* Anything that can fail for an *expected* reason returns ``Result`` (``Ok`` / ``Err``).
  Technical failures (disk, database, missing tools) are raised as typed errors.
* Assets are referenced by their stable ``id`` (a string). Never store file paths.
* Blocking calls (import, register_derived, ensure_thumbnail, delete) do file I/O: call them
  from a job, not from the UI thread.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from media_house.modules.media_library.application.dto import (
    DeleteSummaryDto,
    DerivationDto,
    DerivedAssetResultDto,
    ImportResultDto,
    MediaAssetDto,
    MediaGroupDto,
    TagDto,
)
from media_house.modules.media_library.domain.errors import (
    GroupNotFound,
    InvalidMedia,
    MediaNotFound,
    UnsupportedMediaType,
)
from media_house.modules.media_library.domain.events import (
    DerivedAssetRegistered,
    MediaAssetDeleted,
    MediaAssetImported,
    MediaGroupCreated,
)
from media_house.modules.media_library.domain.query import (
    MediaQuery,
    Page,
    SortField,
    SortOrder,
)
from media_house.modules.media_library.domain.values import (
    AssetRole,
    AssetStatus,
    JsonValue,
    MediaType,
    SourceType,
)
from media_house.shared.errors import ConflictError, NotFoundError, Result, ValidationError

type MediaError = ValidationError | NotFoundError | ConflictError


class MediaLibrary(Protocol):
    """Import, find, organise and derive media assets."""

    # --- import ---------------------------------------------------------------------------
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
    ) -> Result[ImportResultDto, MediaError]:
        """Validate and import ``path``. Identical content is stored once (``created=False``)."""
        ...

    # --- lookup ---------------------------------------------------------------------------
    def get(self, asset_id: str) -> Result[MediaAssetDto, MediaError]: ...

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]: ...

    def find_by_checksum(self, checksum: str) -> MediaAssetDto | None: ...

    def search(self, query: MediaQuery | None = None) -> Page[MediaAssetDto]:
        """Filtered, sorted, paginated listing (default: first 50 active assets, newest first)."""
        ...

    def local_path(self, asset_id: str) -> Result[Path, MediaError]:
        """A readable local path for tools that need a file. Resolve it, use it, do not store it."""
        ...

    # --- derived assets -------------------------------------------------------------------
    def fingerprint(
        self,
        source_asset_id: str,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
    ) -> Result[str, MediaError]: ...

    def find_derived_asset(
        self,
        source_asset_id: str,
        processing_fingerprint: str,
    ) -> MediaAssetDto | None: ...

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
    ) -> Result[DerivedAssetResultDto, MediaError]: ...

    def list_derived(self, source_asset_id: str) -> list[MediaAssetDto]: ...

    # --- previews -------------------------------------------------------------------------
    def thumbnail(
        self, asset_id: str, max_size: int = 256
    ) -> Result[MediaAssetDto | None, MediaError]:
        """The existing thumbnail asset (resolve its file with ``local_path``) or ``None``."""
        ...

    def ensure_thumbnail(
        self, asset_id: str, max_size: int = 256
    ) -> Result[MediaAssetDto, MediaError]:
        """Generate the thumbnail if missing. Blocking: run it as a job."""
        ...

    # --- library state --------------------------------------------------------------------
    def rename(self, asset_id: str, name: str) -> Result[MediaAssetDto, MediaError]: ...

    def set_favorite(self, asset_id: str, favorite: bool) -> Result[MediaAssetDto, MediaError]: ...

    def archive(self, asset_id: str) -> Result[MediaAssetDto, MediaError]: ...

    def restore(self, asset_id: str) -> Result[MediaAssetDto, MediaError]: ...

    def update_metadata(
        self,
        asset_id: str,
        patch: Mapping[str, JsonValue],
    ) -> Result[MediaAssetDto, MediaError]: ...

    def add_tags(self, asset_id: str, tags: Sequence[str]) -> Result[MediaAssetDto, MediaError]: ...

    def remove_tags(
        self, asset_id: str, tags: Sequence[str]
    ) -> Result[MediaAssetDto, MediaError]: ...

    def list_tags(self, *, prefix: str | None = None, limit: int = 100) -> list[TagDto]: ...

    # --- groups ---------------------------------------------------------------------------
    def create_group(
        self,
        name: str,
        *,
        parent_id: str | None = None,
        description: str = "",
    ) -> Result[MediaGroupDto, MediaError]: ...

    def rename_group(self, group_id: str, name: str) -> Result[MediaGroupDto, MediaError]: ...

    def delete_group(self, group_id: str) -> Result[None, MediaError]: ...

    def list_groups(self) -> list[MediaGroupDto]: ...

    def add_to_group(self, asset_id: str, group_id: str) -> Result[bool, MediaError]: ...

    def remove_from_group(self, asset_id: str, group_id: str) -> Result[bool, MediaError]: ...

    # --- deletion -------------------------------------------------------------------------
    def delete(
        self, asset_id: str, *, include_derived: bool = True
    ) -> Result[DeleteSummaryDto, MediaError]:
        """Permanently delete an asset, its derivatives (by default) and unreferenced files."""
        ...


__all__ = [
    "AssetRole",
    "AssetStatus",
    "DeleteSummaryDto",
    "DerivationDto",
    "DerivedAssetRegistered",
    "DerivedAssetResultDto",
    "GroupNotFound",
    "ImportResultDto",
    "InvalidMedia",
    "JsonValue",
    "MediaAssetDeleted",
    "MediaAssetDto",
    "MediaAssetImported",
    "MediaError",
    "MediaGroupCreated",
    "MediaGroupDto",
    "MediaLibrary",
    "MediaNotFound",
    "MediaQuery",
    "MediaType",
    "Page",
    "SortField",
    "SortOrder",
    "SourceType",
    "TagDto",
    "UnsupportedMediaType",
]
