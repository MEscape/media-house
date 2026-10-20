"""Local-filesystem adapters: free disk space and the module's own scratch directories."""

import re
import shutil
from pathlib import Path

from media_house.shared.errors import InfrastructureError
from media_house.shared.filesystem import AppPaths, resolve_within

_SCRATCH_NAME = re.compile(r"proxy-[A-Za-z0-9_-]{1,64}")


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


class StatDiskSpace:
    """Structurally implements ``application.ports.DiskSpace``."""

    def free_bytes(self, path: Path) -> int:
        try:
            return shutil.disk_usage(_nearest_existing(path)).free
        except OSError as exc:
            raise InfrastructureError(
                f"Cannot read free disk space: {exc}", details={"path": path.name}
            ) from exc

    def volume_of(self, path: Path) -> str:
        try:
            return str(_nearest_existing(path).stat().st_dev)
        except OSError as exc:
            raise InfrastructureError(
                f"Cannot identify the volume: {exc}", details={"path": path.name}
            ) from exc

    def size_bytes(self, path: Path) -> int | None:
        try:
            return path.stat().st_size if path.is_file() else None
        except OSError:
            return None


class LocalScratch:
    """Structurally implements ``application.ports.Scratch`` under the application temp dir.

    Only names of the form ``proxy-<id>`` are ever created or removed, so cleanup can never
    reach a directory this module did not name.
    """

    def __init__(self, paths: AppPaths) -> None:
        self._root = paths.temp_dir

    @property
    def root(self) -> Path:
        return self._root

    def create(self, name: str) -> Path:
        path = self._path(name)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InfrastructureError(
                f"Cannot create a scratch directory: {exc}", details={"name": name}
            ) from exc
        return path

    def remove(self, name: str) -> None:
        path = self._path(name)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise InfrastructureError(
                f"Cannot remove a scratch directory: {exc}", details={"name": name}
            ) from exc

    def names(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            p.name for p in self._root.iterdir() if p.is_dir() and _SCRATCH_NAME.fullmatch(p.name)
        )

    def _path(self, name: str) -> Path:
        if not _SCRATCH_NAME.fullmatch(name):
            raise InfrastructureError("Not a proxy scratch directory name", details={"name": name})
        return resolve_within(self._root, name)
