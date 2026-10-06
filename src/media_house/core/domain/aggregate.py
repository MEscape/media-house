"""Aggregate root base class."""

from media_house.shared.events import DomainEvent


class AggregateRoot:
    """Collects domain events raised by state changes.

    Use cases call :meth:`pull_events` after persisting the aggregate and publish
    the result. The aggregate never publishes anything itself and never touches
    a clock - timestamps are passed in.
    """

    def __init__(self) -> None:
        self._pending_events: list[DomainEvent] = []

    def _record(self, event: DomainEvent) -> None:
        self._pending_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        events, self._pending_events = self._pending_events, []
        return events
