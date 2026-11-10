"""Small building blocks shared by the Control Center pages. Styling comes from ``base.qss``
through the ``role`` / ``status`` properties; no widget carries a stylesheet."""

from collections.abc import Sequence

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

#: Status levels the theme colours. A status is always shown WITH its text, never colour alone.
OK, WARN, ERROR, INFO, NEUTRAL = "ok", "warn", "error", "info", "neutral"


def label(text: str = "", role: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def set_status(widget: QLabel, level: str, text: str) -> None:
    widget.setProperty("status", level)
    widget.setText(text)
    widget.setAccessibleDescription(f"{level}: {text}")
    # Property-based QSS rules apply only after a re-polish.
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def status_chip(level: str, text: str) -> QLabel:
    chip = QLabel()
    set_status(chip, level, text)
    return chip


def page_header(title: str, subtitle: str) -> QWidget:
    header = QWidget()
    layout = QVBoxLayout(header)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    layout.addWidget(label(title, "title"))
    layout.addWidget(label(subtitle, "muted", wrap=True))
    return header


def table(headers: Sequence[str], accessible_name: str) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(list(headers))
    widget.setAccessibleName(accessible_name)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.setWordWrap(False)
    widget.verticalHeader().setVisible(False)
    widget.verticalHeader().setDefaultSectionSize(24)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return widget


def fill(widget: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
    widget.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            widget.setItem(r, c, item)
    widget.resizeColumnsToContents()
    widget.horizontalHeader().setStretchLastSection(True)
