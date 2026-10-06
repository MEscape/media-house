"""The MediaAsset aggregate."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import AggregateRoot
from media_house.modules.media_library.domain.events import (
    DerivedAssetRegistered,
    MediaAssetImported,
)
from media_house.modules.media_library.domain.values import (
    AssetRole,
    AssetStatus,
    Checksum,
    Derivation,
    JsonObject,
    JsonValue,
    MediaAssetId,
    MediaName,
    MediaSource,
    MediaType,
    StorageKey,
    Tag,
    validate_json_object,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class MediaFileInfo:
    """Facts about the stored file. Fields that do not apply to a media type stay ``None``.

    ``technical`` holds extractor output that varies by type (image: ``format``,
    ``color_mode``, ``has_alpha``; audio: ``sample_rate``, ``channels``, ``codec``; video:
    ``fps``, ``codec``, ``audio_tracks``). Frequently queried values (size, dimensions,
    duration) are first-class fields so they can be indexed and sorted.
    """

    media_type: MediaType
    mime_type: str
    extension: str
    file_size: int
    checksum: Checksum
    storage_key: StorageKey
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    technical: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.file_size < 0:
            raise InvariantViolation("File size must not be negative")
        if (self.width is not None and self.width <= 0) or (
            self.height is not None and self.height <= 0
        ):
            raise InvariantViolation("Dimensions must be positive")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise InvariantViolation("Duration must not be negative")
        object.__setattr__(
            self,
            "technical",
            validate_json_object(self.technical, what="technical information"),
        )


class MediaAsset(AggregateRoot):
    """A logical media item: stable id + metadata. The bytes live in storage.

    Originals are immutable: only library state (name, favourite, status, tags, metadata)
    changes. Processing produces *derived* assets that point back via :class:`Derivation`.
    """

    def __init__(
        self,
        *,
        id: MediaAssetId,  # noqa: A002
        file: MediaFileInfo,
        original_filename: str,
        display_name: MediaName,
        source: MediaSource,
        created_at: datetime,
        updated_at: datetime,
        role: AssetRole = AssetRole.ORIGINAL,
        status: AssetStatus = AssetStatus.ACTIVE,
        is_favorite: bool = False,
        tags: Iterable[Tag] = (),
        metadata: JsonObject | None = None,
        derivation: Derivation | None = None,
    ) -> None:
        super().__init__()
        if (role is AssetRole.ORIGINAL) != (derivation is None):
            raise InvariantViolation("Only derived assets have a derivation, and all of them do")
        self.id = id
        self.file = file
        self.original_filename = original_filename
        self.display_name = display_name
        self.source = source
        self.created_at = created_at
        self.updated_at = updated_at
        self.role = role
        self.status = status
        self.is_favorite = is_favorite
        self.tags: frozenset[Tag] = frozenset(tags)
        self.metadata: dict[str, JsonValue] = validate_json_object(metadata, what="metadata")
        self.derivation = derivation

    # ------------------------------------------------------------------ creation
    @classmethod
    def import_original(
        cls,
        *,
        file: MediaFileInfo,
        original_filename: str,
        display_name: MediaName,
        source: MediaSource,
        tags: Iterable[Tag] = (),
        metadata: JsonObject | None = None,
        now: datetime,
    ) -> "MediaAsset":
        asset = cls(
            id=MediaAssetId.new(),
            file=file,
            original_filename=original_filename,
            display_name=display_name,
            source=source,
            created_at=now,
            updated_at=now,
            tags=tags,
            metadata=metadata,
        )
        asset._record(
            MediaAssetImported(
                occurred_at=now,
                asset_id=asset.id.value,
                media_type=file.media_type.value,
                checksum=file.checksum.value,
            ),
        )
        return asset

    @classmethod
    def create_derived(
        cls,
        *,
        file: MediaFileInfo,
        original_filename: str,
        display_name: MediaName,
        derivation: Derivation,
        role: AssetRole = AssetRole.DERIVED,
        metadata: JsonObject | None = None,
        now: datetime,
    ) -> "MediaAsset":
        if role is AssetRole.ORIGINAL:
            raise InvariantViolation("A derived asset cannot have the ORIGINAL role")
        asset = cls(
            id=MediaAssetId.new(),
            file=file,
            original_filename=original_filename,
            display_name=display_name,
            source=MediaSource(),
            created_at=now,
            updated_at=now,
            role=role,
            metadata=metadata,
            derivation=derivation,
        )
        asset._record(
            DerivedAssetRegistered(
                occurred_at=now,
                asset_id=asset.id.value,
                source_asset_id=derivation.source_asset_id.value,
                operation=derivation.operation,
                fingerprint=derivation.fingerprint.value,
            ),
        )
        return asset

    # ------------------------------------------------------------------ library state
    @property
    def is_derived(self) -> bool:
        return self.derivation is not None

    def rename(self, name: MediaName, *, now: datetime) -> None:
        if name != self.display_name:
            self.display_name = name
            self.updated_at = now

    def set_favorite(self, favorite: bool, *, now: datetime) -> None:
        if favorite != self.is_favorite:
            self.is_favorite = favorite
            self.updated_at = now

    def archive(self, *, now: datetime) -> None:
        self._set_status(AssetStatus.ARCHIVED, now)

    def restore(self, *, now: datetime) -> None:
        self._set_status(AssetStatus.ACTIVE, now)

    def add_tags(self, tags: Iterable[Tag], *, now: datetime) -> None:
        merged = self.tags | frozenset(tags)
        if merged != self.tags:
            self.tags = merged
            self.updated_at = now

    def remove_tags(self, tags: Iterable[Tag], *, now: datetime) -> None:
        remaining = self.tags - frozenset(tags)
        if remaining != self.tags:
            self.tags = remaining
            self.updated_at = now

    def update_metadata(self, patch: JsonObject, *, now: datetime) -> None:
        """Merge ``patch`` into the metadata. A ``None`` value removes that key."""
        merged = dict(self.metadata)
        for key, value in validate_json_object(patch, what="metadata").items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        merged = validate_json_object(merged, what="metadata")
        if merged != self.metadata:
            self.metadata = merged
            self.updated_at = now

    def _set_status(self, status: AssetStatus, now: datetime) -> None:
        if status is not self.status:
            self.status = status
            self.updated_at = now
