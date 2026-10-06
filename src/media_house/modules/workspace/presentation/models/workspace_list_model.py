"""Model/View: the list of workspaces."""

from collections.abc import Sequence
from typing import Any, override

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt

from media_house.modules.workspace.application.dto import WorkspaceDto


class WorkspaceListModel(QAbstractListModel):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._items: list[WorkspaceDto] = []

    def set_workspaces(self, workspaces: Sequence[WorkspaceDto]) -> None:
        self.beginResetModel()
        self._items = list(workspaces)
        self.endResetModel()

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    @override
    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Created {item.created_at:%Y-%m-%d %H:%M} UTC"
        return None
