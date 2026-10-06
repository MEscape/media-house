import sys
import threading

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from media_house.presentation.dialogs.error_dialog import show_error_dialog
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.error_boundary import GlobalErrorBoundary
from media_house.shared.errors import ErrorCategory, ToolNotFoundError
from media_house.shared.errors.handler import ErrorHandler, ErrorReport

pytestmark = pytest.mark.presentation


class RecordingPresenter:
    def __init__(self) -> None:
        self.reports: list[ErrorReport] = []

    def present(self, report: ErrorReport) -> None:
        self.reports.append(report)
        self.main_thread = threading.current_thread() is threading.main_thread()


@pytest.fixture
def boundary(dispatcher: UiDispatcher) -> tuple[GlobalErrorBoundary, RecordingPresenter]:
    presenter = RecordingPresenter()
    return GlobalErrorBoundary(ErrorHandler(), presenter, dispatcher), presenter


def test_handle_logs_and_presents_a_safe_report_on_the_ui_thread(
    qtbot: QtBot,
    boundary: tuple[GlobalErrorBoundary, RecordingPresenter],
) -> None:
    guard, presenter = boundary
    report = guard.handle(RuntimeError("password=hunter2 in /home/alice"))
    qtbot.waitUntil(lambda: bool(presenter.reports), timeout=3000)
    assert presenter.reports == [report]
    assert presenter.main_thread
    assert "hunter2" not in report.user_message
    assert report.category is ErrorCategory.UNEXPECTED


def test_uncaught_exceptions_are_intercepted_and_hooks_are_restored(
    qtbot: QtBot,
    boundary: tuple[GlobalErrorBoundary, RecordingPresenter],
) -> None:
    guard, presenter = boundary
    original_hook, original_thread_hook = sys.excepthook, threading.excepthook
    guard.install()
    guard.install()  # idempotent
    try:
        assert sys.excepthook is not original_hook
        try:
            raise ValueError("escaped a slot")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        qtbot.waitUntil(lambda: len(presenter.reports) == 1, timeout=3000)

        def fail_in_thread() -> None:
            raise KeyError("worker bug")

        thread = threading.Thread(target=fail_in_thread)
        thread.start()
        thread.join()
        qtbot.waitUntil(lambda: len(presenter.reports) == 2, timeout=3000)
    finally:
        guard.uninstall()
    assert sys.excepthook is original_hook
    assert threading.excepthook is original_thread_hook


def test_keyboard_interrupt_is_not_swallowed(
    boundary: tuple[GlobalErrorBoundary, RecordingPresenter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard, presenter = boundary
    seen: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, _e, _tb: seen.append(t))
    guard.install()
    try:
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        guard.uninstall()
    assert seen == [KeyboardInterrupt]
    assert presenter.reports == []


def test_error_dialog_shows_message_reference_and_details_but_no_traceback(
    qtbot: QtBot,
    qapp: QApplication,
) -> None:
    error = ToolNotFoundError("ffmpeg")
    report = ErrorHandler().handle(error)
    box: QMessageBox = show_error_dialog(None, report)
    qtbot.addWidget(box)
    assert "ffmpeg" in box.text()
    assert error.error_id in box.informativeText()
    assert "external.tool_not_found" in box.detailedText()
    assert "Traceback" not in box.detailedText()
    assert box.icon() == QMessageBox.Icon.Warning
