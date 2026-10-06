"""The Workspace page. A passive view: it renders view-model state and forwards intent."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.workspace.presentation import strings
from media_house.modules.workspace.presentation.models.workspace_list_model import (
    WorkspaceListModel,
)
from media_house.modules.workspace.presentation.viewmodels.workspace_viewmodel import (
    WorkspaceViewModel,
)


class WorkspaceView(QWidget):
    def __init__(self, viewmodel: WorkspaceViewModel) -> None:
        super().__init__()
        self._vm = viewmodel
        self._model = WorkspaceListModel(self)

        self._name = QLineEdit()
        self._name.setPlaceholderText(strings.NAME_PLACEHOLDER)
        self._name.setAccessibleName(strings.NAME_LABEL)
        self._create = QPushButton(strings.BUTTON_CREATE)
        self._error = QLabel()
        self._error.setProperty("role", "alert")
        self._error.setAccessibleName("Form error")
        self._error.setWordWrap(True)
        self._error.hide()
        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setAccessibleName(strings.LIST_LABEL)
        self._verify = QPushButton(strings.BUTTON_VERIFY)
        self._cancel = QPushButton(strings.BUTTON_CANCEL)
        self._cancel.setEnabled(False)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress_text = QLabel()
        self._progress_text.setProperty("role", "muted")

        form = QHBoxLayout()
        form.addWidget(self._name, 1)
        form.addWidget(self._create)
        actions = QHBoxLayout()
        actions.addWidget(self._verify)
        actions.addWidget(self._cancel)
        actions.addWidget(self._progress, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(self._list, 1)
        layout.addLayout(actions)
        layout.addWidget(self._progress_text)
        self.setTabOrder(self._name, self._create)

        # view -> view model (intent)
        self._create.clicked.connect(self._submit)
        self._name.returnPressed.connect(self._submit)
        self._verify.clicked.connect(self._vm.verify_workspaces)
        self._cancel.clicked.connect(self._vm.cancel_verification)
        # view model -> view (state)
        self._vm.workspaces_changed.connect(self._model.set_workspaces)
        self._vm.busy_changed.connect(lambda busy: self._create.setEnabled(not busy))
        self._vm.form_error_changed.connect(self._show_error)
        self._vm.name_accepted.connect(self._name.clear)
        self._vm.verifying_changed.connect(self._on_verifying)
        self._vm.verification_progress.connect(self._on_progress)

    def _submit(self) -> None:
        self._vm.create_workspace(self._name.text())

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(bool(message))

    def _on_verifying(self, active: bool) -> None:
        self._verify.setEnabled(not active)
        self._cancel.setEnabled(active)
        self._progress.setVisible(active)
        if not active:
            self._progress.setValue(0)
            self._progress_text.clear()

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self._progress.setMaximum(total or 0)
        self._progress.setValue(current)
        self._progress_text.setText(message)
