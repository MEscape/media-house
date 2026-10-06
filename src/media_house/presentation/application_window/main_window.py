"""The main window: a thin shell that renders whatever was contributed.

It knows *contribution types*, never modules.
"""

from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDockWidget, QMainWindow

from media_house.presentation import strings
from media_house.presentation.application_window.menus import build_menus
from media_house.presentation.application_window.navigation import ViewNavigator
from media_house.presentation.extension import UiRegistry
from media_house.presentation.qt.status import StatusReporter
from media_house.presentation.qt.window_state import WindowStateStore


class MainWindow(QMainWindow):
    def __init__(
        self,
        registry: UiRegistry,
        *,
        title: str = strings.APP_TITLE,
        status: StatusReporter | None = None,
        state_store: WindowStateStore | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1100, 700)
        self._state_store = state_store

        self._navigator = ViewNavigator(registry.views, self)
        self.setCentralWidget(self._navigator.stack)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._navigator.dock)

        required = [strings.MENU_VIEW] if registry.docks else []
        menus = build_menus(self, registry.actions, required=required)
        self.docks: dict[str, QDockWidget] = {}
        for contribution in registry.docks:
            dock = QDockWidget(contribution.title, self)
            dock.setObjectName(f"dock-{contribution.id}")
            dock.setWidget(contribution.factory())
            self.addDockWidget(contribution.area, dock)
            self.docks[contribution.id] = dock
            view_menu = menus[strings.MENU_VIEW]
            view_menu.addAction(dock.toggleViewAction())

        self.statusBar().showMessage(strings.READY)
        if status is not None:
            status.message.connect(self.statusBar().showMessage)
        if state_store is not None:
            state_store.restore(self)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._state_store is not None:
            self._state_store.save(self)
        super().closeEvent(event)
