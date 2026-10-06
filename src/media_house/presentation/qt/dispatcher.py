"""Run a callable on the UI thread from any thread."""

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot


class UiDispatcher(QObject):
    """Must be created on the UI thread.

    Emitting a signal from a foreign thread to a receiver living in the UI thread
    is delivered through Qt's event queue (queued connection). That is the one
    sanctioned way for worker threads to cause UI-thread work.
    """

    _requested = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._requested.connect(self._run)

    def post(self, work: Callable[[], None]) -> None:
        self._requested.emit(work)

    @Slot(object)
    def _run(self, work: Callable[[], None]) -> None:
        work()
