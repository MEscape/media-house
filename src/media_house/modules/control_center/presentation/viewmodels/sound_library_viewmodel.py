"""Sound library state: rows loaded in the background, filtered here, import as a job."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from media_house.modules.control_center.application.sound_library import (
    ImportSummary,
    SoundFilter,
    SoundLibraryService,
    SoundRow,
    apply_filter,
)
from media_house.modules.control_center.presentation import strings
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.errors.handler import ErrorReport


class SoundLibraryViewModel(QObject):
    #: visible rows, total count, selectable count (of all), never-selected count (of all)
    rows_changed = Signal(object, int, int, int)
    loading_changed = Signal(bool)
    error_changed = Signal(str)  # empty clears
    import_running = Signal(bool)

    def __init__(
        self,
        service: SoundLibraryService,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._jobs = jobs
        self._status = status
        self._rows: list[SoundRow] = []
        self._filter = SoundFilter()
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def refresh(self) -> None:
        self.loading_changed.emit(True)
        self.error_changed.emit("")
        self._jobs.submit(
            "Load sound library",
            lambda _ctx: self._service.rows(),
            on_success=self._on_rows,
            on_error=self._on_error,
            on_finished=lambda: self.loading_changed.emit(False),
        )

    def set_filter(self, spec: SoundFilter) -> None:
        self._filter = spec
        self._publish()

    def import_manifest(self, manifest: str) -> None:
        self.error_changed.emit("")
        self.import_running.emit(True)
        self._jobs.submit(
            "Import sound library manifest",
            lambda ctx: self._service.import_manifest(Path(manifest), ctx),
            on_success=self._on_imported,
            on_error=self._on_error,
            on_finished=lambda: self.import_running.emit(False),
        )

    def _on_rows(self, rows: list[SoundRow]) -> None:
        self._rows = rows
        self._loaded = True
        self._publish()

    def _publish(self) -> None:
        selectable = sum(1 for r in self._rows if r.selectable)
        self.rows_changed.emit(
            apply_filter(self._rows, self._filter),
            len(self._rows),
            selectable,
            len(self._rows) - selectable,
        )

    def _on_imported(self, result: Result[ImportSummary, str]) -> None:
        match result:
            case Ok(summary):
                self._status.show(
                    strings.IMPORT_DONE.format(
                        imported=summary.imported,
                        created=summary.created,
                        failed=len(summary.failed),
                        not_cleared=len(summary.not_cleared),
                    ),
                    10000,
                )
                self.refresh()  # clears the error line first, so failures below stay visible
                if summary.failed:
                    self.error_changed.emit(
                        "\n".join(f"{path}: {reason}" for path, reason in summary.failed)
                    )
            case Err(message):
                self.error_changed.emit(strings.IMPORT_FAILED.format(message=message))

    def _on_error(self, report: ErrorReport) -> None:
        self.error_changed.emit(report.user_message)
