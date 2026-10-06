"""The Media Library page. A passive view: it renders view-model state and forwards intent."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.media_library.presentation import strings
from media_house.modules.media_library.presentation.models.media_asset_list_model import (
    MediaAssetListModel,
)
from media_house.modules.media_library.presentation.viewmodels.media_library_viewmodel import (
    MediaLibraryViewModel,
)


class MediaLibraryView(QWidget):
    def __init__(self, viewmodel: MediaLibraryViewModel) -> None:
        super().__init__()
        self._vm = viewmodel
        self._model = MediaAssetListModel(self)

        self._import_button = QPushButton(strings.BUTTON_IMPORT)
        self._error = QLabel()
        self._error.setProperty("role", "alert")
        self._error.setAccessibleName("Form error")
        self._error.setWordWrap(True)
        self._error.hide()

        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setAccessibleName(strings.LIST_LABEL)

        form = QHBoxLayout()
        form.addWidget(self._import_button)
        form.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(self._list, 1)

        # view -> view model (intent)
        self._import_button.clicked.connect(self._on_import_clicked)

        # view model -> view (state)
        self._vm.assets_changed.connect(self._model.set_assets)
        self._vm.busy_changed.connect(lambda busy: self._import_button.setEnabled(not busy))
        self._vm.error_changed.connect(self._show_error)

    def _on_import_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Media",
            "",
            "Media Files (*.png *.jpg *.jpeg *.gif *.mp4 *.mov *.mp3 *.wav);;All Files (*)",
        )
        if file_path:
            self._vm.import_media(file_path)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(bool(message))
