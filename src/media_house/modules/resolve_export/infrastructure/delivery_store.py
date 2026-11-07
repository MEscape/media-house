"""Delivery folder on the local disk: the final masters, organised, never overwritten."""

import json
import shutil
from collections.abc import Mapping
from pathlib import Path

from media_house.modules.resolve_export.domain.errors import DeliveryRefused
from media_house.shared.errors import PersistenceError
from media_house.shared.filesystem import resolve_within


class FilesystemDeliveryStore:
    """Structurally implements ``DeliveryStore``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def exists(self, name: str) -> bool:
        return resolve_within(self._root, name).exists()

    def place(self, source: Path, name: str, details: Mapping[str, str]) -> str:
        target = resolve_within(self._root, name)
        sidecar = target.with_suffix(target.suffix + ".json")
        if target.exists() or sidecar.exists():
            raise DeliveryRefused(
                f"A delivered file named '{name}' already exists; "
                "a delivered master is never replaced."
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            sidecar.write_text(
                json.dumps(dict(details), indent=1, sort_keys=True), encoding="utf-8"
            )
        except OSError as exc:
            raise PersistenceError(
                f"The file could not be placed in the delivery folder: {exc}",
                details={"name": name},
            ) from exc
        return name
