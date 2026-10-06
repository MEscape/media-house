"""Model/View: the list of media assets."""

from collections.abc import Sequence
from typing import Any, override

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt

from media_house.modules.media_library.application.dto import MediaAssetDto


class MediaAssetListModel(QAbstractListModel):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._items: list[MediaAssetDto] = []

    def set_assets(self, assets: Sequence[MediaAssetDto]) -> None:
        self.beginResetModel()
        self._items = list(assets)
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
            return item.display_name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Role: {item.role.value}\nAdded: {item.created_at:%Y-%m-%d %H:%M} UTC"
        return None
