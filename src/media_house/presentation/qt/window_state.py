"""Persist window geometry/layout in an INI file inside the app config directory."""

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow


class WindowStateStore:
    def __init__(self, file: Path) -> None:
        self._settings = QSettings(str(file), QSettings.Format.IniFormat)

    def restore(self, window: QMainWindow) -> None:
        geometry = self._settings.value("main/geometry")
        state = self._settings.value("main/state")
        if geometry is not None:
            window.restoreGeometry(geometry)
        if state is not None:
            window.restoreState(state)

    def save(self, window: QMainWindow) -> None:
        self._settings.setValue("main/geometry", window.saveGeometry())
        self._settings.setValue("main/state", window.saveState())
        self._settings.sync()
