"""Use cases that change library state of an existing asset (never its bytes)."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.dto import MediaAssetDto
from media_house.modules.media_library.application.mapping import AssetDtoMapper
from media_house.modules.media_library.domain.errors import MediaNotFound
from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import (
    JsonValue,
    MediaAssetId,
    MediaName,
    Tag,
)
from media_house.shared.errors import DomainError, Err, NotFoundError, Ok, Result, ValidationError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type _Change = Callable[[MediaAsset, datetime], None]


class ManageAsset:
    """Rename, favourite, archive/restore, tag and annotate assets.

    Archiving hides an asset from default searches without deleting anything. Originals'
    files are never touched by any of these operations.
    """

    def __init__(
        self,
        repository: MediaAssetRepository,
        mapper: AssetDtoMapper,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._mapper = mapper
        self._clock = clock

    def rename(
        self, asset_id: str, name: str
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        try:
            parsed = MediaName.of(name)
        except DomainError as exc:
            return Err(ValidationError(str(exc), field="name", user_message=exc.user_message))
        return self._apply(asset_id, lambda a, now: a.rename(parsed, now=now))

    def set_favorite(
        self,
        asset_id: str,
        favorite: bool,
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        return self._apply(asset_id, lambda a, now: a.set_favorite(favorite, now=now))

    def archive(self, asset_id: str) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        return self._apply(asset_id, lambda a, now: a.archive(now=now))

    def restore(self, asset_id: str) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        return self._apply(asset_id, lambda a, now: a.restore(now=now))

    def update_metadata(
        self,
        asset_id: str,
        patch: Mapping[str, JsonValue],
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        """Merge ``patch`` into the asset's metadata; a ``None`` value removes the key."""
        return self._apply(asset_id, lambda a, now: a.update_metadata(patch, now=now))

    def add_tags(
        self,
        asset_id: str,
        tags: Sequence[str],
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        try:
            parsed = [Tag.of(t) for t in tags]
        except DomainError as exc:
            return Err(ValidationError(str(exc), field="tags", user_message=exc.user_message))
        return self._apply(asset_id, lambda a, now: a.add_tags(parsed, now=now))

    def remove_tags(
        self,
        asset_id: str,
        tags: Sequence[str],
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        try:
            parsed = [Tag.of(t) for t in tags]
        except DomainError as exc:
            return Err(ValidationError(str(exc), field="tags", user_message=exc.user_message))
        return self._apply(asset_id, lambda a, now: a.remove_tags(parsed, now=now))

    def _apply(
        self,
        asset_id: str,
        change: _Change,
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        asset = self._repository.get(MediaAssetId(asset_id)) if asset_id else None
        if asset is None:
            return Err(MediaNotFound(asset_id))
        before = asset.updated_at
        try:
            change(asset, self._clock.now())
        except DomainError as exc:
            return Err(ValidationError(str(exc), user_message=exc.user_message))
        if asset.updated_at != before:
            self._repository.save(asset)
            _log.info("Asset updated", asset_id=asset.id.value)
        return Ok(self._mapper.one(asset))
