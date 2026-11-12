"""Thumbnail page state: the library catalogue, footage suggestions and designed variants.

Every library or pixel call runs as a job; results come back on the UI thread as plain rows.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from media_house.modules.control_center.application.thumbnail_studio import (
    AssetRow,
    StudioCatalogue,
    ThumbnailForm,
    ThumbnailStudio,
    VariantRow,
)
from media_house.modules.control_center.presentation import strings
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.concurrency import JobContext, JobProgress
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.errors.handler import ErrorReport


class ThumbnailViewModel(QObject):
    catalogue_changed = Signal(object)  # StudioCatalogue
    candidates_changed = Signal(object)  # tuple[AssetRow, ...]
    variants_changed = Signal(object)  # tuple[VariantRow, ...]
    #: A picture just added (upload, import): the role it was added for and its row.
    picture_added = Signal(str, object)
    busy_changed = Signal(bool)
    progress_changed = Signal(int, int, str)
    error_changed = Signal(str)  # empty clears

    def __init__(
        self,
        studio: ThumbnailStudio,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._studio = studio
        self._jobs = jobs
        self._status = status
        self._pending = 0
        self.catalogue = StudioCatalogue((), (), ())
        self.variants: tuple[VariantRow, ...] = ()

    def load(self) -> None:
        self._run(
            "Load thumbnail pictures", lambda _ctx: Ok(self._studio.catalogue()), self._loaded
        )

    def add_reaction(self, path: str) -> None:
        self._run(
            "Add reaction picture",
            lambda _ctx: self._studio.add_reaction(Path(path)),
            lambda row: self._added("reaction", row),
        )

    def import_picture(self, path: str, role: str) -> None:
        self._run(
            "Import picture",
            lambda _ctx: self._studio.import_picture(Path(path)),
            lambda row: self._added(role, row),
        )

    def suggest(self, video_id: str) -> None:
        if not video_id:
            self.error_changed.emit(strings.THUMB_NO_SUGGESTIONS)
            return
        self._run(
            "Find reaction moments",
            lambda ctx: self._studio.suggest((video_id,), ctx),
            self._suggested,
        )

    def design(self, form: ThumbnailForm) -> None:
        self._run("Design thumbnails", lambda ctx: self._studio.design(form, ctx), self._designed)

    def export(self, asset_id: str, destination: str) -> None:
        self._run(
            "Export thumbnail",
            lambda _ctx: self._studio.export(asset_id, Path(destination)),
            lambda path: self._status.show(strings.THUMB_EXPORTED.format(path=path), 6000),
        )

    # --- results -------------------------------------------------------------------------------

    def _loaded(self, catalogue: StudioCatalogue) -> None:
        self.catalogue = catalogue
        self.catalogue_changed.emit(catalogue)

    def _added(self, role: str, row: AssetRow) -> None:
        self.picture_added.emit(role, row)
        self.load()

    def _suggested(self, rows: tuple[AssetRow, ...]) -> None:
        if not rows:
            self.error_changed.emit(strings.THUMB_NO_SUGGESTIONS)
        self.candidates_changed.emit(rows)
        self.load()  # the picked frames joined the reaction collection

    def _designed(self, rows: tuple[VariantRow, ...]) -> None:
        self.variants = rows
        self.variants_changed.emit(rows)
        made = sum(1 for r in rows if r.asset_id)
        self._status.show(strings.THUMB_DONE.format(count=made), 5000)

    # --- plumbing ------------------------------------------------------------------------------

    def _run[T](
        self,
        name: str,
        work: Callable[[JobContext], Result[T, str]],
        done: Callable[[T], None],
    ) -> None:
        self.error_changed.emit("")
        self._pending += 1
        self.busy_changed.emit(True)

        def finish(result: Result[T, str]) -> None:
            match result:
                case Ok(value):
                    done(value)
                case Err(message):
                    self.error_changed.emit(message)

        self._jobs.submit(
            name,
            work,
            on_success=finish,
            on_error=self._on_error,
            on_progress=self._on_progress,
            on_finished=self._end,
        )

    def _on_progress(self, progress: JobProgress) -> None:
        self.progress_changed.emit(progress.current, progress.total or 0, progress.message)

    def _on_error(self, report: ErrorReport) -> None:
        self.error_changed.emit(report.user_message)

    def _end(self) -> None:
        self._pending = max(0, self._pending - 1)
        self.busy_changed.emit(self._pending > 0)
