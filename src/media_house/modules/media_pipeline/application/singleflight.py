"""Identical work requested twice at the same time is done once.

Two pipeline runs in one process (two windows, two jobs) can ask for the same task on the same
asset. The owners dedupe what they STORE, not what is IN FLIGHT, so without this both would probe
and decode the same file. The first caller computes; the others wait and share its result, reported
as ``reused``. If the first caller fails or is cancelled, the waiters do not inherit that: each
retries on its own, so one run's cancellation can never cancel another.

Scope is one process. Across processes the owners' own coordination (for example the proxy
manager's in-flight wait) applies.
"""

import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field, replace

from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.values import TaskStatus
from media_house.shared.concurrency import CancellationToken


@dataclass(slots=True)
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    result: TaskResult | None = None


class SingleFlight:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[Hashable, _Flight] = {}

    def run(
        self,
        key: Hashable,
        work: Callable[[], TaskResult],
        cancellation: CancellationToken,
    ) -> TaskResult:
        while True:
            with self._lock:
                flight = self._flights.get(key)
                leader = flight is None
                if flight is None:
                    flight = self._flights[key] = _Flight()
            if leader:
                return self._lead(key, flight, work)
            while not flight.done.wait(0.1):
                cancellation.raise_if_cancelled()
            shared = flight.result
            if shared is not None and shared.status.delivered:
                return replace(
                    shared,
                    status=TaskStatus.REUSED,
                    detail=shared.detail or "computed by a concurrent run of the same task",
                )
            # the leader did not deliver: try again as a leader (or follow the next one)

    def _lead(self, key: Hashable, flight: _Flight, work: Callable[[], TaskResult]) -> TaskResult:
        try:
            result = work()
            flight.result = result
            return result
        finally:
            with self._lock:
                del self._flights[key]
            flight.done.set()
