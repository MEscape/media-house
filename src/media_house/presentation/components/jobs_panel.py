"""Widget: list of background jobs with a cancel button. Pure view."""

from PySide6.QtWidgets import QListView, QPushButton, QVBoxLayout, QWidget

from media_house.presentation import strings
from media_house.presentation.models.job_list_model import IS_ACTIVE_ROLE, JOB_ID_ROLE, JobListModel
from media_house.presentation.qt.job_bridge import UiJobRunner


class JobsPanel(QWidget):
    def __init__(self, model: JobListModel, runner: UiJobRunner) -> None:
        super().__init__()
        self._runner = runner
        self._view = QListView()
        self._view.setModel(model)
        self._view.setAccessibleName(strings.DOCK_JOBS)
        self._cancel = QPushButton(strings.BUTTON_CANCEL_JOB)
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._cancel_selected)
        self._view.selectionModel().currentChanged.connect(self._update_button)
        model.dataChanged.connect(lambda *_: self._update_button())

        layout = QVBoxLayout(self)
        layout.addWidget(self._view)
        layout.addWidget(self._cancel)

    def _update_button(self, *_: object) -> None:
        index = self._view.currentIndex()
        self._cancel.setEnabled(index.isValid() and bool(index.data(IS_ACTIVE_ROLE)))

    def _cancel_selected(self) -> None:
        index = self._view.currentIndex()
        if index.isValid():
            self._runner.cancel(str(index.data(JOB_ID_ROLE)))
