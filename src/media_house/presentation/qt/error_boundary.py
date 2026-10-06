"""Global error boundary for the UI process.

Catches exceptions that escape Qt slots (``sys.excepthook``) and worker threads
(``threading.excepthook``), logs them once with full diagnostics via the
``ErrorHandler``, and shows a friendly dialog on the UI thread. The application
keeps running; it never shows a raw traceback.
"""

import sys
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Protocol

from PySide6.QtWidgets import QWidget

from media_house.presentation.dialogs.error_dialog import show_error_dialog
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.shared.errors.handler import ErrorHandler, ErrorReport


class ErrorPresenter(Protocol):
    def present(self, report: ErrorReport) -> None: ...


class QtErrorPresenter:
    def __init__(self, parent_provider: Callable[[], QWidget | None]) -> None:
        self._parent_provider = parent_provider

    def present(self, report: ErrorReport) -> None:
        show_error_dialog(self._parent_provider(), report)


class GlobalErrorBoundary:
    def __init__(
        self,
        handler: ErrorHandler,
        presenter: ErrorPresenter,
        dispatcher: UiDispatcher,
    ) -> None:
        self._handler = handler
        self._presenter = presenter
        self._dispatcher = dispatcher
        self._previous_hook = sys.excepthook
        self._previous_thread_hook = threading.excepthook
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self._previous_hook = sys.excepthook
        self._previous_thread_hook = threading.excepthook
        sys.excepthook = self._on_exception
        threading.excepthook = self._on_thread_exception
        self._installed = True

    def uninstall(self) -> None:
        if self._installed:
            sys.excepthook = self._previous_hook
            threading.excepthook = self._previous_thread_hook
            self._installed = False

    def handle(self, exc: BaseException, *, operation: str | None = None) -> ErrorReport:
        """Log, then present on the UI thread. Safe to call from any thread."""
        report = self._handler.handle(exc, operation=operation)
        self._dispatcher.post(lambda: self._presenter.present(report))
        return report

    def _on_exception(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            self._previous_hook(exc_type, exc, tb)
            return
        self.handle(exc, operation="uncaught exception")

    def _on_thread_exception(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and not issubclass(args.exc_type, SystemExit):
            self.handle(args.exc_value, operation=f"thread {getattr(args.thread, 'name', '?')}")
