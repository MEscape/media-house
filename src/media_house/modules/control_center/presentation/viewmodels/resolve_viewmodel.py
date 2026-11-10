"""DaVinci Resolve status: one background check through ``ResolveInspector``."""

from datetime import datetime

from PySide6.QtCore import QObject, Signal

from media_house.modules.control_center.application.resolve_status import (
    CheckResolveStatus,
    ResolveStatus,
)
from media_house.modules.control_center.presentation import strings
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.errors.handler import ErrorReport


class ResolveViewModel(QObject):
    checking_changed = Signal(bool)
    status_changed = Signal(object, object)  # ResolveStatus, datetime (local time of the check)
    error_changed = Signal(str)  # empty clears

    def __init__(
        self,
        check: CheckResolveStatus,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._check = check
        self._jobs = jobs
        self._status = status
        self._checking = False

    @property
    def is_checking(self) -> bool:
        return self._checking

    def test_connection(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.checking_changed.emit(True)
        self.error_changed.emit("")
        self._jobs.submit(
            "Test DaVinci Resolve connection",
            lambda _ctx: self._check.execute(),
            on_success=self._on_checked,
            on_error=self._on_error,
            on_finished=self._on_finished,
        )

    def _on_checked(self, result: ResolveStatus) -> None:
        self.status_changed.emit(result, datetime.now().astimezone())
        self._status.show(f"DaVinci Resolve: {result.state}")

    def _on_error(self, report: ErrorReport) -> None:
        self.error_changed.emit(strings.RESOLVE_CHECK_FAILED.format(message=report.user_message))

    def _on_finished(self) -> None:
        self._checking = False
        self.checking_changed.emit(False)
