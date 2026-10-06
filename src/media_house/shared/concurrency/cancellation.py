"""Cooperative cancellation."""

import threading

from media_house.shared.errors import OperationCancelledError


class CancellationToken:
    """Thread-safe flag that long-running work polls (or waits on)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise OperationCancelledError("Operation was cancelled")

    def wait(self, timeout: float) -> bool:
        """Interruptible sleep. Returns ``True`` if cancelled while waiting."""
        return self._event.wait(timeout)
