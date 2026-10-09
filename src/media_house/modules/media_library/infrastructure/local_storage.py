"""Local-filesystem implementation of :class:`MediaStorage`.

Blobs are written atomically (temp file in the destination directory, ``replace``) and hashed
while being copied, so a source that changes after inspection is rejected rather than stored
under the wrong identity.

A ``StorageKey`` is a validated value object (relative, ``/``-separated, no dot segments), so a
key is mapped to a path LEXICALLY under the root, which is resolved once at construction. The
file system is never asked to resolve a path per call: ``Path.resolve`` on a file that another
writer is replacing at that moment can return a transient, wrong path (seen on Windows), which
made concurrent stores of identical content fail spuriously.
"""

import hashlib
import os
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

from media_house.modules.media_library.domain.errors import MediaStorageError
from media_house.modules.media_library.domain.values import Checksum, StorageKey
from media_house.shared.types import new_id

_CHUNK = 1024 * 1024


class LocalMediaStorage:
    """Structurally implements ``MediaStorage`` on a local (or mounted) directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def store(self, source: Path, key: StorageKey, *, expected_checksum: Checksum) -> None:
        destination = self._resolve(key)
        temp = destination.with_name(f".{destination.name}.{new_id()}.part")
        try:
            if destination.is_file() and destination.stat().st_size == source.stat().st_size:
                return  # content-addressed: same key + same size == already stored
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with source.open("rb") as reader, temp.open("wb") as writer:
                while chunk := reader.read(_CHUNK):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if digest.hexdigest() != expected_checksum.value:
                raise MediaStorageError(
                    "Source file changed while it was being imported",
                    user_message="The file changed during import. Please try again.",
                )
            temp.replace(destination)
        except OSError as exc:
            # Another process may have stored identical content a moment ago (Windows refuses
            # to replace a file that is open elsewhere): that is success, not failure.
            if self._is_stored(destination, source):
                return
            raise MediaStorageError(
                f"Failed to store media blob: {exc}",
                details={"storage_key": key.value},
            ) from exc
        finally:
            with suppress(OSError):
                temp.unlink(missing_ok=True)

    def exists(self, key: StorageKey) -> bool:
        return self._resolve(key).is_file()

    def open(self, key: StorageKey) -> BinaryIO:
        path = self._resolve(key)
        try:
            return path.open("rb")
        except OSError as exc:
            raise MediaStorageError(
                f"Cannot open media blob: {exc}",
                details={"storage_key": key.value},
            ) from exc

    def get_path(self, key: StorageKey) -> Path:
        path = self._resolve(key)
        if not path.is_file():
            raise MediaStorageError(
                "Media blob is missing from storage",
                details={"storage_key": key.value},
            )
        return path

    def delete(self, key: StorageKey) -> None:
        path = self._resolve(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise MediaStorageError(
                f"Failed to delete media blob: {exc}",
                details={"storage_key": key.value},
            ) from exc
        root = self._root
        for parent in path.parents:  # prune empty shard directories, never the root
            if parent == root or not parent.is_relative_to(root):
                break
            try:
                parent.rmdir()
            except OSError:
                break

    # ------------------------------------------------------------------ internals
    def _resolve(self, key: StorageKey) -> Path:
        """The path of ``key`` under the root. ``StorageKey`` already forbids escaping it."""
        return self._root.joinpath(*key.value.split("/"))

    @staticmethod
    def _is_stored(destination: Path, source: Path) -> bool:
        try:
            return destination.is_file() and destination.stat().st_size == source.stat().st_size
        except OSError:
            return False
