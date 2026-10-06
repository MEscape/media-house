"""Presentation state + intent handling for the Media Library page."""

from PySide6.QtCore import QObject, Signal

from media_house.modules.media_library.application.contracts import MediaLibrary, MediaQuery
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter


class MediaLibraryViewModel(QObject):
    assets_changed = Signal(object)  # Sequence[MediaAssetDto]
    busy_changed = Signal(bool)
    error_changed = Signal(str)

    def __init__(
        self,
        library: MediaLibrary,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._jobs = jobs
        self._status = status
        self._pending = 0

    def refresh(self) -> None:
        self._begin()
        self._jobs.submit(
            "Load media assets",
            lambda _ctx: self._library.search(MediaQuery(page_size=100)).items,
            on_success=self.assets_changed.emit,
            on_finished=self._end,
        )

    def import_media(self, file_path: str) -> None:
        from pathlib import Path

        self.error_changed.emit("")
        self._begin()
        self._jobs.submit(
            "Import media",
            lambda _ctx: self._library.import_file(Path(file_path)),
            on_success=self._on_imported,
            on_finished=self._end,
        )

    def _on_imported(self, result: object) -> None:
        from media_house.shared.errors import Err, Ok

        match result:
            case Ok(_):
                self._status.show("Media imported successfully")
                self.refresh()
            case Err(error):
                self.error_changed.emit(error.user_message)

    # -- busy bookkeeping --------------------------------------------------- #
    def _begin(self) -> None:
        self._pending += 1
        if self._pending == 1:
            self.busy_changed.emit(True)

    def _end(self) -> None:
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy_changed.emit(False)
