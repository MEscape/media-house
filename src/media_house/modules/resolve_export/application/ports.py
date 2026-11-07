"""The one thing the export asks of the outside world that no other module offers: a place on disk
for the delivered master."""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class DeliveryStore(Protocol):
    def place(self, source: Path, name: str, details: Mapping[str, str]) -> str:
        """Copy the validated file into the delivery folder as ``name`` and write its details next
        to it. A file of that name that already exists is never replaced: this raises
        ``DeliveryRefused``. Returns the name relative to the delivery root."""
        ...

    def exists(self, name: str) -> bool: ...
