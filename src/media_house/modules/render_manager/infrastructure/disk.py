"""Free disk space where renders are written."""

import shutil
from pathlib import Path


class LocalDiskProbe:
    """Structurally implements ``DiskProbe``."""

    def free_bytes(self, path: Path) -> int | None:
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None
