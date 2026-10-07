"""What media inspection needs from the outside world."""

from pathlib import Path
from typing import Protocol

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import ObservedMedia
from media_house.shared.concurrency import CancellationToken


class MediaProber(Protocol):
    """Reads a media file and reports what it IS: no interpretation, no repair."""

    @property
    def identity(self) -> str:
        """Name and versions of the tools, as part of the cache fingerprint."""
        ...

    def probe(
        self,
        path: Path,
        config: InspectionConfig,
        cancellation: CancellationToken,
    ) -> ObservedMedia:
        """The facts of ``path``. An unopenable file is reported as unreadable, not raised.

        Technical failures (a missing tool, a timeout, cancellation) raise typed errors.
        """
        ...
