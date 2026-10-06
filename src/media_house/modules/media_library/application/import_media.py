"""Use case: import a file into the library (with content-based deduplication)."""

from media_house.modules.media_library.application.commands import ImportMediaCommand
from media_house.modules.media_library.application.dto import ImportResultDto
from media_house.modules.media_library.application.ingest import AssetIngestor
from media_house.modules.media_library.application.mapping import AssetDtoMapper
from media_house.modules.media_library.domain.errors import (
    GroupNotFound,
    InvalidMedia,
    UnsupportedMediaType,
)
from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.repository import (
    MediaAssetRepository,
    MediaGroupRepository,
)
from media_house.modules.media_library.domain.values import (
    MediaGroupId,
    MediaName,
    MediaSource,
    Tag,
    validate_json_object,
)
from media_house.shared.errors import DomainError, Err, NotFoundError, Ok, Result, ValidationError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ImportMedia:
    """Expected failures (bad input, unsupported/corrupt file, unknown group) are ``Err``.

    Importing identical content twice is *not* an error: the existing asset is returned with
    ``created=False`` and the requested tags/groups are merged into it, so the call is
    idempotent and safe to retry after a partial failure.
    """

    def __init__(
        self,
        ingestor: AssetIngestor,
        repository: MediaAssetRepository,
        groups: MediaGroupRepository,
        mapper: AssetDtoMapper,
    ) -> None:
        self._ingestor = ingestor
        self._repository = repository
        self._groups = groups
        self._mapper = mapper

    def execute(
        self,
        command: ImportMediaCommand,
    ) -> Result[ImportResultDto, ValidationError | NotFoundError]:
        try:
            name = MediaName.of(command.display_name) if command.display_name is not None else None
            tags = [Tag.of(raw) for raw in command.tags]
            source = MediaSource.of(
                command.source_type,
                url=command.source_url,
                provider=command.source_provider,
            )
            metadata = validate_json_object(command.metadata, what="metadata")
            group_ids = [MediaGroupId(raw) for raw in dict.fromkeys(command.group_ids)]
        except DomainError as exc:
            return Err(ValidationError(str(exc), user_message=exc.user_message))
        for group_id in group_ids:
            if self._groups.get(group_id) is None:
                return Err(GroupNotFound(group_id.value))

        try:
            inspected = self._ingestor.inspect(command.path)
        except (InvalidMedia, UnsupportedMediaType) as exc:
            _log.warning("Import rejected", reason=exc.code, filename=command.path.name)
            return Err(exc)

        existing = self._repository.find_original_by_checksum(inspected.checksum)
        if existing is not None:
            asset, created = existing, False
            _log.info("Asset already exists", asset_id=asset.id.value)
        else:
            info = self._ingestor.file_info(inspected)
            self._ingestor.place(command.path, info)
            new_asset = MediaAsset.import_original(
                file=info,
                original_filename=inspected.original_filename,
                display_name=name or MediaName.from_filename(inspected.original_filename),
                source=source,
                tags=tags,
                metadata=metadata,
                now=self._ingestor.clock.now(),
            )
            asset, created = self._ingestor.commit(
                new_asset,
                command.path,
                lambda: self._repository.find_original_by_checksum(info.checksum),
            )
            if created:
                _log.info(
                    "Asset imported",
                    asset_id=asset.id.value,
                    media_type=info.media_type.value,
                    size=info.file_size,
                )

        if not created and not frozenset(tags) <= asset.tags:
            asset.add_tags(tags, now=self._ingestor.clock.now())
            self._repository.save(asset)
        now = self._ingestor.clock.now()
        for group_id in group_ids:
            if self._groups.add_member(group_id, asset.id, now=now):
                _log.info("Asset linked to group", asset_id=asset.id.value, group_id=group_id.value)
        return Ok(ImportResultDto(self._mapper.one(asset), created))
