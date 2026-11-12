"""The project folder on disk: scan without moving, hash by streaming, place as link or copy.

Originals are never moved or changed. A large file is never read into memory: it is hashed in
chunks and placed as a hard link when the volume allows it (a copy otherwise). A picture that
must change format (``.svg.webp`` to PNG, with its transparency) is converted into the project
folder; the original stays where it is.
"""

import hashlib
import os
import shutil
from pathlib import Path

from media_house.modules.production_project.application.ports import FolderScan, ScannedFile
from media_house.shared.errors import (
    Err,
    ExternalSystemError,
    Ok,
    PersistenceError,
    Result,
    ValidationError,
)
from media_house.shared.filesystem import resolve_within

_CHUNK = 8 * 1024 * 1024
#: Folders that are looked at and left alone: thumbnails are out of scope for this product.
_IGNORED_FOLDERS = ("thumb",)
_JUNK = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_NUMBERED = ("00", "01", "02", "03", "04", "05", "06", "07", "08", "09")


class LocalProjectFolder:
    """Structurally implements ``ProjectFolder``."""

    def scan(self, root: Path) -> Result[FolderScan, ValidationError]:
        if not root.is_dir():
            return Err(
                ValidationError(
                    f"{root} is not a directory",
                    field="root",
                    user_message="Dieser Ordner existiert nicht.",
                )
            )
        files: list[ScannedFile] = []
        ignored: list[str] = []
        try:
            for entry in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
                if entry.is_dir():
                    self._folder(entry, files, ignored)
                elif self._keep(entry):
                    files.append(ScannedFile(entry, entry.name, "", entry.stat().st_size))
        except OSError as exc:
            raise PersistenceError(f"Could not scan {root.name}: {exc}") from exc
        return Ok(FolderScan(tuple(files), tuple(ignored)))

    def _folder(self, folder: Path, files: list[ScannedFile], ignored: list[str]) -> None:
        name = folder.name
        if any(marker in name.casefold() for marker in _IGNORED_FOLDERS):
            ignored.append(f"{name} (Thumbnails sind nicht Teil des Projekts)")
            return
        children = [c for c in folder.iterdir() if c.is_file() and self._keep(c)]
        if not children:
            ignored.append(f"{name} (leer)")
            return
        numbered = name[:2] in _NUMBERED and name[2:3] == "_"
        for child in sorted(children, key=lambda p: p.name.casefold()):
            files.append(
                ScannedFile(child, child.name, name if numbered else "", child.stat().st_size)
            )

    @staticmethod
    def _keep(path: Path) -> bool:
        return (
            path.is_file() and path.name.casefold() not in _JUNK and not path.name.startswith(".")
        )

    def sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
        except OSError as exc:
            raise PersistenceError(f"Could not read {path.name}: {exc}") from exc
        return digest.hexdigest()

    def place(self, source: Path, root: Path, relative: str, convert_to: str) -> Path:
        target = resolve_within(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if convert_to and source.suffix.lstrip(".").lower() != convert_to.lower():
                _convert(source, target, convert_to)
            elif target.exists():
                return target
            else:
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
        except OSError as exc:
            raise PersistenceError(f"Could not place {target.name}: {exc}") from exc
        return target

    def write_text(self, root: Path, relative: str, text: str) -> Path:
        target = resolve_within(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise PersistenceError(f"Could not write {target.name}: {exc}") from exc
        return target


def _convert(source: Path, target: Path, fmt: str) -> None:
    """Re-encode a picture, keeping transparency for PNG. The source is only read."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow is a dependency
        raise ExternalSystemError("Pillow is not installed") from exc
    try:
        with Image.open(source) as opened:
            opened.load()
            jpeg = fmt.lower() in {"jpg", "jpeg"}
            image = opened.convert("RGB") if jpeg and opened.mode in {"RGBA", "LA", "P"} else opened
            image.save(target, format="JPEG" if jpeg else fmt.upper())
    except (OSError, ValueError) as exc:
        raise ExternalSystemError(
            f"Could not convert {source.name}: {exc}",
            user_message=f"Die Datei {source.name} konnte nicht umgewandelt werden.",
        ) from exc
