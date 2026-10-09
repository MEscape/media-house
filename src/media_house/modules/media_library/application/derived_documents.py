"""Find-or-store of the JSON documents that analysis modules keep as derived library assets.

Transcripts, measurements, signal series and result timelines all follow one pattern: look the
document up by its processing fingerprint, treat an unreadable one as absent (the caller
recomputes), and register a freshly computed one. This is the single place that knows how, built
only on the public ``MediaLibrary`` contract.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.media_library.application.dto import DerivedAssetResultDto, MediaAssetDto
from media_house.modules.media_library.domain.values import JsonValue
from media_house.shared.errors import (
    ConflictError,
    Err,
    MediaHouseError,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_DISPLAY_NAME_MAX = 255

type _LibraryError = ValidationError | NotFoundError | ConflictError


class DocumentLibrary(Protocol):
    """The part of ``MediaLibrary`` that document storage needs (``MediaLibrary`` satisfies it)."""

    def fingerprint(
        self,
        source_asset_id: str,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
    ) -> Result[str, _LibraryError]: ...

    def find_derived_asset(
        self, source_asset_id: str, processing_fingerprint: str
    ) -> MediaAssetDto | None: ...

    def local_path(self, asset_id: str) -> Result[Path, _LibraryError]: ...

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
    ) -> Result[DerivedAssetResultDto, _LibraryError]: ...

    def delete(
        self, asset_id: str, *, include_derived: bool = True
    ) -> Result[object, _LibraryError]: ...


@dataclass(frozen=True, slots=True)
class DocumentSpec:
    """What identifies and describes one derived document of a source asset."""

    operation: str
    version: int
    config: Mapping[str, JsonValue]


class DerivedDocuments:
    """Reads and writes derived JSON documents through a library (no storage of its own)."""

    def __init__(self, library: DocumentLibrary, paths: AppPaths) -> None:
        self._library = library
        self._paths = paths

    def fingerprint(self, source_id: str, spec: DocumentSpec) -> Result[str, _LibraryError]:
        return self._library.fingerprint(source_id, spec.operation, spec.config, spec.version)

    def load[T](
        self,
        source_id: str,
        fingerprint: str,
        parse: Callable[[str], T],
    ) -> tuple[T, MediaAssetDto] | None:
        """The stored document and its asset, or ``None`` if absent or unusable.

        ``parse`` rejects a document by raising ``ValueError`` or a ``MediaHouseError``. An unusable
        document (missing file, unreadable text, rejected by ``parse``) is removed:
        the library answers an identical registration with the existing asset, so a damaged
        document would otherwise be found and rejected on every request.
        """
        asset = self._library.find_derived_asset(source_id, fingerprint)
        if asset is None:
            return None
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            _log.warning("Stored document missing, recomputing", asset_id=asset.id)
            self._discard(asset)
            return None
        try:
            return parse(path.value.read_text(encoding="utf-8")), asset
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            _log.warning(
                "Stored document unusable, recomputing", asset_id=asset.id, reason=str(exc)
            )
            self._discard(asset)
            return None

    def store(
        self,
        source: MediaAssetDto,
        text: str,
        *,
        filename: str,
        spec: DocumentSpec,
        display_name: str,
        metadata: Mapping[str, JsonValue],
    ) -> Result[MediaAssetDto, _LibraryError]:
        with self._paths.temporary_directory(prefix=f"{spec.operation}-") as tmp:
            document = tmp / filename
            document.write_text(text, encoding="utf-8")
            registered = self._library.register_derived(
                source.id,
                document,
                operation=spec.operation,
                config=spec.config,
                version=spec.version,
                display_name=display_name[:_DISPLAY_NAME_MAX],
                metadata={"processing_type": spec.operation, "source_asset_id": source.id}
                | dict(metadata),
            )
        if isinstance(registered, Err):
            return registered
        return Ok(registered.value.asset)

    def _discard(self, asset: MediaAssetDto) -> None:
        removed = self._library.delete(asset.id, include_derived=False)
        if isinstance(removed, Err):
            _log.warning("Damaged document could not be removed", asset_id=asset.id)
