"""User-facing error dialog. Shows a friendly message, a reference id and optional details."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from media_house.presentation import strings
from media_house.shared.errors.handler import ErrorReport


def show_error_dialog(parent: QWidget | None, report: ErrorReport) -> QMessageBox:
    """Non-blocking dialog (never nests an event loop inside a slot)."""
    icon = QMessageBox.Icon.Warning if report.recoverable else QMessageBox.Icon.Critical
    box = QMessageBox(icon, strings.ERROR_DIALOG_TITLE, report.user_message, parent=parent)
    box.setInformativeText(strings.ERROR_REFERENCE.format(error_id=report.error_id))
    box.setDetailedText(f"{report.code}\n{report.technical_summary}")
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.setAccessibleName(strings.ERROR_DIALOG_TITLE)
    box.open()
    return box
