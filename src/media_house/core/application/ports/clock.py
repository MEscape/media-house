"""Time as a dependency, so use cases are deterministic under test."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware (UTC)."""
        ...
