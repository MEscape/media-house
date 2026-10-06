"""Read-side use cases: lookup, search, derivative discovery, file resolution, tags."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from media_house.modules.media_library.application.dto import MediaAssetDto, TagDto
from media_house.modules.media_library.application.mapping import AssetDtoMapper
from media_house.modules.media_library.application.ports import MediaStorage
from media_house.modules.media_library.domain.errors import MediaNotFound
from media_house.modules.media_library.domain.query import MediaQuery, Page, SortOrder
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import (
    Checksum,
    Derivation,
    JsonValue,
    MediaAssetId,
    ProcessingFingerprint,
    Tag,
)
from media_house.shared.errors import DomainError, Err, NotFoundError, Ok, Result, ValidationError


class MediaQueries:
    """Everything that reads. Reads are not logged (they are frequent and uninteresting)."""

    def __init__(
        self,
        repository: MediaAssetRepository,
        mapper: AssetDtoMapper,
        storage: MediaStorage,
    ) -> None:
        self._repository = repository
        self._mapper = mapper
        self._storage = storage

    def get(self, asset_id: str) -> Result[MediaAssetDto, NotFoundError]:
        asset = self._repository.get(MediaAssetId(asset_id)) if asset_id else None
        if asset is None:
            return Err(MediaNotFound(asset_id))
        return Ok(self._mapper.one(asset))

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]:
        """Existing assets in the requested order (unknown ids are skipped). One round trip."""
        ids = [MediaAssetId(raw) for raw in dict.fromkeys(asset_ids) if raw]
        return self._mapper.many(self._repository.get_many(ids))

    def find_by_checksum(self, checksum: str) -> MediaAssetDto | None:
        try:
            parsed = Checksum(checksum.lower())
        except DomainError:
            return None
        asset = self._repository.find_original_by_checksum(parsed)
        return self._mapper.one(asset) if asset else None

    def search(self, query: MediaQuery) -> Page[MediaAssetDto]:
        page = self._repository.search(query)
        return Page(self._mapper.many(list(page.items)), page.total, page.page, page.page_size)

    def list_derived(self, source_asset_id: str) -> list[MediaAssetDto]:
        """Direct derivatives (including previews and archived ones), oldest first, max 200."""
        query = MediaQuery(
            source_asset_id=source_asset_id,
            statuses=frozenset(),
            roles=frozenset(),
            sort_order=SortOrder.ASC,
            page_size=MediaQuery.MAX_PAGE_SIZE,
        )
        return list(self.search(query).items)

    def fingerprint(
        self,
        source_asset_id: str,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
    ) -> Result[str, ValidationError | NotFoundError]:
        """The processing fingerprint a derivative of this source and config would have."""
        source = self._repository.get(MediaAssetId(source_asset_id)) if source_asset_id else None
        if source is None:
            return Err(MediaNotFound(source_asset_id))
        try:
            derivation = Derivation.create(
                source_asset_id=source.id,
                source_checksum=source.file.checksum,
                operation=operation,
                version=version,
                config=config,
            )
        except DomainError as exc:
            return Err(ValidationError(str(exc), user_message=exc.user_message))
        return Ok(derivation.fingerprint.value)

    def find_derived_asset(
        self,
        source_asset_id: str,
        processing_fingerprint: str,
    ) -> MediaAssetDto | None:
        """Has this exact derivative been produced before? Returns it or ``None``."""
        if not source_asset_id:
            return None
        try:
            fingerprint = ProcessingFingerprint(processing_fingerprint)
        except DomainError:
            return None
        asset = self._repository.find_derived(MediaAssetId(source_asset_id), fingerprint)
        return self._mapper.one(asset) if asset else None

    def local_path(self, asset_id: str) -> Result[Path, NotFoundError]:
        """A readable local path for tools that need a file (e.g. FFmpeg). Do not store it."""
        asset = self._repository.get(MediaAssetId(asset_id)) if asset_id else None
        if asset is None:
            return Err(MediaNotFound(asset_id))
        return Ok(self._storage.get_path(asset.file.storage_key))

    def list_tags(self, *, prefix: str | None = None, limit: int = 100) -> list[TagDto]:
        normalised = Tag.of(prefix).value if prefix and prefix.strip() else None
        usages = self._repository.list_tags(prefix=normalised, limit=max(1, min(limit, 1000)))
        return [TagDto(u.name, u.count) for u in usages]
