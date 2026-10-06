"""Synchronous in-process event bus.

Deliberately tiny: handlers run on the publisher's thread, in subscription
order. A failing handler is logged and isolated so it cannot break the
publisher or other handlers. Subscribing to a base class receives all of its
subclasses. UI code that needs thread-affinity must marshal onto the UI thread
itself (see ``presentation.qt.dispatcher``).
"""

import threading
from collections.abc import Callable
from typing import Any, Protocol

from media_house.shared.logging.api import get_logger

_log = get_logger(__name__)


class EventPublisher(Protocol):
    """The only thing use cases need to know about the bus."""

    def publish(self, event: object) -> None: ...


class Subscription:
    def __init__(self, cancel: Callable[[], None]) -> None:
        self._cancel = cancel

    def cancel(self) -> None:
        self._cancel()


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[tuple[type, Callable[[Any], None]]] = []

    def subscribe[E](self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        entry: tuple[type, Callable[[Any], None]] = (event_type, handler)
        with self._lock:
            self._handlers.append(entry)

        def cancel() -> None:
            with self._lock:
                if entry in self._handlers:
                    self._handlers.remove(entry)

        return Subscription(cancel)

    def publish(self, event: object) -> None:
        with self._lock:
            targets = [h for t, h in self._handlers if isinstance(event, t)]
        for handler in targets:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - isolation boundary: one handler must not break others
                _log.exception("Event handler failed", event_type=type(event).__name__)
