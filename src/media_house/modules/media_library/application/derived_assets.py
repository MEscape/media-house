"""Use case: register the output of a processing step as a derived asset."""

from media_house.modules.media_library.application.commands import RegisterDerivedCommand
from media_house.modules.media_library.application.dto import DerivedAssetResultDto
from media_house.modules.media_library.application.ingest import AssetIngestor
from media_house.modules.media_library.application.mapping import AssetDtoMapper
from media_house.modules.media_library.domain.errors import (
    InvalidMedia,
    MediaNotFound,
    UnsupportedMediaType,
)
from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import (
    AssetRole,
    Derivation,
    MediaAssetId,
    MediaName,
    validate_json_object,
)
from media_house.shared.errors import DomainError, Err, NotFoundError, Ok, Result, ValidationError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class RegisterDerivedAsset:
    """Store a processing result, or reuse the identical one that already exists.

    The source is never modified. The fingerprint (source content + operation + version +
    canonical config) is unique in the database, so concurrent registrations converge on one
    asset and ``created`` tells the caller whether its file was actually used.
    """

    def __init__(
        self,
        ingestor: AssetIngestor,
        repository: MediaAssetRepository,
        mapper: AssetDtoMapper,
    ) -> None:
        self._ingestor = ingestor
        self._repository = repository
        self._mapper = mapper

    def execute(
        self,
        command: RegisterDerivedCommand,
    ) -> Result[DerivedAssetResultDto, ValidationError | NotFoundError]:
        if command.role is AssetRole.ORIGINAL:
            return Err(ValidationError("Derived assets cannot be ORIGINAL", field="role"))
        if not command.source_asset_id:
            return Err(MediaNotFound(command.source_asset_id))
        source_id = MediaAssetId(command.source_asset_id)
        source = self._repository.get(source_id)
        if source is None:
            return Err(MediaNotFound(source_id.value))
        try:
            derivation = Derivation.create(
                source_asset_id=source_id,
                source_checksum=source.file.checksum,
                operation=command.operation,
                version=command.version,
                config=command.config,
            )
            metadata = validate_json_object(command.metadata, what="metadata")
            name = (
                MediaName.of(command.display_name)
                if command.display_name is not None
                else MediaName.of(f"{source.display_name.value} - {command.operation}"[:255])
            )
        except DomainError as exc:
            return Err(ValidationError(str(exc), user_message=exc.user_message))

        def lookup() -> MediaAsset | None:
            return self._repository.find_derived(source_id, derivation.fingerprint)

        existing = lookup()
        if existing is not None:
            _log.info("Derived asset reused", asset_id=existing.id.value)
            return Ok(DerivedAssetResultDto(self._mapper.one(existing), created=False))

        try:
            inspected = self._ingestor.inspect(command.path)
        except (InvalidMedia, UnsupportedMediaType) as exc:
            _log.warning("Derived asset rejected", reason=exc.code, operation=command.operation)
            return Err(exc)
        info = self._ingestor.file_info(inspected)
        self._ingestor.place(command.path, info)
        asset = MediaAsset.create_derived(
            file=info,
            original_filename=inspected.original_filename,
            display_name=name,
            derivation=derivation,
            role=command.role,
            metadata=metadata,
            now=self._ingestor.clock.now(),
        )
        stored, created = self._ingestor.commit(asset, command.path, lookup)
        if created:
            _log.info(
                "Derived asset created",
                asset_id=stored.id.value,
                source_asset_id=source_id.value,
                operation=command.operation,
            )
        return Ok(DerivedAssetResultDto(self._mapper.one(stored), created=created))
