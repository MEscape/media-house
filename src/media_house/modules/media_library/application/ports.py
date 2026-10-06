"""Ports owned by the media library's application layer.

* :class:`MediaInspector` - validates an untrusted file and extracts facts (adapter reads bytes).
* :class:`MediaStorage` - where the bytes live; swap the adapter to move to NAS / S3.
* :class:`ThumbnailGenerator` - renders a small preview (image, video frame, ...).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

from media_house.modules.media_library.domain.values import (
    Checksum,
    JsonObject,
    MediaType,
    StorageKey,
)


@dataclass(frozen=True, slots=True)
class InspectedMedia:
    """Verified facts about a candidate file, derived from its *content*, not its name."""

    checksum: Checksum
    file_size: int
    media_type: MediaType
    mime_type: str
    extension: str
    original_filename: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    technical: JsonObject = field(default_factory=dict)


class MediaInspector(Protocol):
    def inspect(self, path: Path) -> InspectedMedia:
        """Validate and describe ``path``.

        Raises ``InvalidMedia`` (unreadable, corrupt, too large, symlink, ...) or
        ``UnsupportedMediaType`` (not a format we ingest).
        """
        ...


class MediaStorage(Protocol):
    """Blob storage addressed by :class:`StorageKey`. All methods raise ``MediaStorageError``."""

    def store(self, source: Path, key: StorageKey, *, expected_checksum: Checksum) -> None:
        """Atomically place ``source`` at ``key``.

        Idempotent for identical content. Fails if the bytes read do not hash to
        ``expected_checksum`` (the file changed after it was inspected).
        """
        ...

    def exists(self, key: StorageKey) -> bool: ...

    def open(self, key: StorageKey) -> BinaryIO: ...

    def get_path(self, key: StorageKey) -> Path:
        """A readable local path (backends without one materialise a cached copy)."""
        ...

    def delete(self, key: StorageKey) -> None:
        """Remove the blob; missing blobs are not an error."""
        ...


class ThumbnailGenerator(Protocol):
    #: File extension (with dot) of the previews this generator produces.
    extension: str

    def render(self, source: Path, destination: Path, *, max_size: int) -> None:
        """Write a preview of ``source`` to ``destination`` fitting a ``max_size`` box."""
        ...
