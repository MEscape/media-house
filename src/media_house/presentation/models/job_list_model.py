"""Model/View: background jobs for the Jobs dock."""

from dataclasses import dataclass
from typing import Any, override

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt

from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.shared.concurrency import JobHandle, JobOutcome, JobProgress, JobState

JOB_ID_ROLE = Qt.ItemDataRole.UserRole + 1
IS_ACTIVE_ROLE = Qt.ItemDataRole.UserRole + 2
_MAX_ROWS = 50


@dataclass(slots=True)
class _Row:
    job_id: str
    name: str
    state: JobState
    progress: str = ""

    @property
    def text(self) -> str:
        suffix = f" ({self.progress})" if self.progress else ""
        return f"{self.name}: {self.state.value}{suffix}"


class JobListModel(QAbstractListModel):
    def __init__(self, runner: UiJobRunner, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[_Row] = []
        runner.job_submitted.connect(self._on_submitted)
        runner.job_progress.connect(self._on_progress)
        runner.job_finished.connect(self._on_finished)

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    @override
    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.text
        if role == JOB_ID_ROLE:
            return row.job_id
        if role == IS_ACTIVE_ROLE:
            return not row.state.is_terminal
        return None

    # -- runner signals (always on the UI thread) ------------------------- #
    def _on_submitted(self, handle: JobHandle[object]) -> None:
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(_Row(handle.id, handle.name, handle.state))
        self.endInsertRows()
        self._trim()

    def _on_progress(self, handle: JobHandle[object], progress: JobProgress) -> None:
        text = f"{progress.current}/{progress.total}" if progress.total else str(progress.current)
        self._update(handle.id, state=JobState.RUNNING, progress=text)

    def _on_finished(self, handle: JobHandle[object], outcome: JobOutcome[object]) -> None:
        self._update(handle.id, state=outcome.state)

    def _update(self, job_id: str, *, state: JobState, progress: str | None = None) -> None:
        for position, row in enumerate(self._rows):
            if row.job_id == job_id:
                row.state = state
                if progress is not None:
                    row.progress = progress
                changed = self.index(position)
                self.dataChanged.emit(changed, changed)
                return

    def _trim(self) -> None:
        while len(self._rows) > _MAX_ROWS:
            oldest_done = next((i for i, r in enumerate(self._rows) if r.state.is_terminal), None)
            if oldest_done is None:
                return
            self.beginRemoveRows(QModelIndex(), oldest_done, oldest_done)
            del self._rows[oldest_done]
            self.endRemoveRows()
