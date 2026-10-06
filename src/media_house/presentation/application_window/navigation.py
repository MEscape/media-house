"""Navigation list + lazily created pages."""

from collections.abc import Sequence

from PySide6.QtWidgets import QDockWidget, QListWidget, QMainWindow, QStackedWidget, QWidget

from media_house.presentation import strings
from media_house.presentation.extension import ViewContribution


class ViewNavigator:
    """Owns the central stacked widget and the navigation dock. Pages are built on first visit."""

    def __init__(self, views: Sequence[ViewContribution], parent: QMainWindow) -> None:
        self._views = tuple(views)
        self._pages: dict[str, QWidget] = {}
        self.stack = QStackedWidget()
        self._list = QListWidget()
        self._list.setAccessibleName(strings.DOCK_NAVIGATION)
        self._list.addItems([view.title for view in self._views])
        self._list.currentRowChanged.connect(self._show)

        self.dock = QDockWidget(strings.DOCK_NAVIGATION, parent)
        self.dock.setObjectName("dock-navigation")
        self.dock.setWidget(self._list)

        if self._views:
            self._list.setCurrentRow(0)

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self._views):
            return
        view = self._views[row]
        page = self._pages.get(view.id)
        if page is None:
            page = view.factory()
            self._pages[view.id] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
