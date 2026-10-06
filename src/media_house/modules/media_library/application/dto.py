"""Data transfer objects: what callers see. Domain objects never leave the module."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from media_house.modules.media_library.domain.values import (
    AssetRole,
    AssetStatus,
    JsonValue,
    MediaType,
    SourceType,
)


@dataclass(frozen=True, slots=True)
class DerivationDto:
    source_asset_id: str
    operation: str
    version: int
    config: Mapping[str, JsonValue]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class MediaAssetDto:
    """Everything a GUI or pipeline needs to know about an asset, except where it is stored."""

    id: str
    media_type: MediaType
    mime_type: str
    extension: str
    original_filename: str
    display_name: str
    file_size: int
    checksum: str
    role: AssetRole
    status: AssetStatus
    is_favorite: bool
    source_type: SourceType
    created_at: datetime
    updated_at: datetime
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    technical: Mapping[str, JsonValue] = field(default_factory=dict)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    source_url: str | None = None
    source_provider: str | None = None
    derivation: DerivationDto | None = None

    @property
    def is_derived(self) -> bool:
        return self.derivation is not None


@dataclass(frozen=True, slots=True)
class MediaGroupDto:
    id: str
    name: str
    parent_id: str | None
    description: str
    asset_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TagDto:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class ImportResultDto:
    """``created`` is ``False`` when identical content was already in the library."""

    asset: MediaAssetDto
    created: bool


@dataclass(frozen=True, slots=True)
class DerivedAssetResultDto:
    """``created`` is ``False`` when the exact derivative already existed (it was reused)."""

    asset: MediaAssetDto
    created: bool


@dataclass(frozen=True, slots=True)
class DeleteSummaryDto:
    """What a delete did. ``orphaned_storage_keys`` lists blobs whose removal failed."""

    deleted_asset_ids: tuple[str, ...]
    deleted_file_count: int
    orphaned_storage_keys: tuple[str, ...] = ()
