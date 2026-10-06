"""Status-bar messages as a small service, so view models need not know the main window."""

from PySide6.QtCore import QObject, Signal


class StatusReporter(QObject):
    message = Signal(str, int)

    def show(self, text: str, timeout_ms: int = 5000) -> None:
        self.message.emit(text, timeout_ms)
