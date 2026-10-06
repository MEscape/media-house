"""Use cases for previews: look one up cheaply, or generate it as a derived asset."""

from media_house.modules.media_library.application.commands import RegisterDerivedCommand
from media_house.modules.media_library.application.derived_assets import RegisterDerivedAsset
from media_house.modules.media_library.application.dto import MediaAssetDto
from media_house.modules.media_library.application.mapping import AssetDtoMapper
from media_house.modules.media_library.application.ports import MediaStorage, ThumbnailGenerator
from media_house.modules.media_library.domain.errors import MediaNotFound, UnsupportedMediaType
from media_house.modules.media_library.domain.media_asset import MediaAsset
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import (
    AssetRole,
    Derivation,
    JsonValue,
    MediaAssetId,
    MediaType,
)
from media_house.shared.errors import Err, NotFoundError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths

THUMBNAIL_OPERATION = "thumbnail"
#: Bump when the way thumbnails are rendered changes: old ones stop matching and are rebuilt.
THUMBNAIL_VERSION = 1
DEFAULT_THUMBNAIL_SIZE = 256
_MIN_SIZE, _MAX_SIZE = 16, 2048
_PREVIEWABLE = frozenset({MediaType.IMAGE, MediaType.VIDEO})


class Thumbnails:
    """Thumbnails are PREVIEW-role derived assets: stored, deduplicated and deleted like others.

    ``find`` is a cheap indexed lookup for the GUI. ``ensure`` renders a missing thumbnail;
    it is a blocking call meant to be run by the job system, never on the UI thread.
    """

    def __init__(
        self,
        repository: MediaAssetRepository,
        storage: MediaStorage,
        generator: ThumbnailGenerator,
        register: RegisterDerivedAsset,
        mapper: AssetDtoMapper,
        paths: AppPaths,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._generator = generator
        self._register = register
        self._mapper = mapper
        self._paths = paths

    def find(
        self,
        asset_id: str,
        max_size: int = DEFAULT_THUMBNAIL_SIZE,
    ) -> Result[MediaAssetDto | None, ValidationError | NotFoundError]:
        prepared = self._prepare(asset_id, max_size)
        if isinstance(prepared, Err):
            return prepared
        source, derivation = prepared.value
        existing = self._repository.find_derived(source.id, derivation.fingerprint)
        return Ok(self._mapper.one(existing) if existing else None)

    def ensure(
        self,
        asset_id: str,
        max_size: int = DEFAULT_THUMBNAIL_SIZE,
    ) -> Result[MediaAssetDto, ValidationError | NotFoundError]:
        prepared = self._prepare(asset_id, max_size)
        if isinstance(prepared, Err):
            return prepared
        source, derivation = prepared.value
        existing = self._repository.find_derived(source.id, derivation.fingerprint)
        if existing is not None:
            return Ok(self._mapper.one(existing))

        source_path = self._storage.get_path(source.file.storage_key)
        with self._paths.temporary_directory(prefix="thumb-") as tmp:
            destination = tmp / f"thumbnail{self._generator.extension}"
            self._generator.render(source_path, destination, max_size=max_size)
            registered = self._register.execute(
                RegisterDerivedCommand(
                    source_asset_id=source.id.value,
                    path=destination,
                    operation=THUMBNAIL_OPERATION,
                    config=dict(derivation.config),
                    version=THUMBNAIL_VERSION,
                    display_name=f"{source.display_name.value} (thumbnail)"[:255],
                    role=AssetRole.PREVIEW,
                ),
            )
        if isinstance(registered, Err):
            return registered
        return Ok(registered.value.asset)

    def _prepare(
        self,
        asset_id: str,
        max_size: int,
    ) -> Result[tuple[MediaAsset, Derivation], ValidationError | NotFoundError]:
        if not _MIN_SIZE <= max_size <= _MAX_SIZE:
            return Err(
                ValidationError(
                    f"Thumbnail size must be {_MIN_SIZE}-{_MAX_SIZE}",
                    field="max_size",
                ),
            )
        source = self._repository.get(MediaAssetId(asset_id)) if asset_id else None
        if source is None:
            return Err(MediaNotFound(asset_id))
        if source.file.media_type not in _PREVIEWABLE:
            return Err(
                UnsupportedMediaType(
                    f"No thumbnail support for {source.file.media_type.value}",
                    user_message="Previews are not available for this kind of media yet.",
                ),
            )
        config: dict[str, JsonValue] = {
            "max_size": max_size,
            "format": self._generator.extension.lstrip("."),
        }
        derivation = Derivation.create(
            source_asset_id=source.id,
            source_checksum=source.file.checksum,
            operation=THUMBNAIL_OPERATION,
            version=THUMBNAIL_VERSION,
            config=config,
        )
        return Ok((source, derivation))
