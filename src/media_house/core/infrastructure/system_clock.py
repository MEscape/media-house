"""Real-time :class:`~media_house.core.application.ports.clock.Clock`."""

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
