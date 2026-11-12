"""Dein Stil: the rules the memory holds, for review (approve, reject, lock, delete)."""

from collections.abc import Sequence
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.style_review_viewmodel import (
    StyleReviewViewModel,
)
from media_house.modules.control_center.presentation.widgets import fill, label, page_header, table


class StyleReviewView(QWidget):
    def __init__(self, viewmodel: StyleReviewViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = viewmodel
        self._rows: list[Any] = []
        layout = QVBoxLayout(self)
        layout.addWidget(page_header(strings.B2C_STYLE_TITLE, strings.B2C_STYLE_SUBTITLE))
        self._message = label("", "muted")
        layout.addWidget(self._message)
        self._table = table(strings.B2C_STYLE_HEADERS, strings.B2C_STYLE_TITLE)
        layout.addWidget(self._table, 1)
        row = QHBoxLayout()
        for text, action in (
            (strings.B2C_STYLE_APPROVE, self._approve),
            (strings.B2C_STYLE_REJECT, self._reject),
            (strings.B2C_STYLE_LOCK, self._lock),
            (strings.B2C_STYLE_DELETE, self._delete),
            (strings.B2C_STYLE_REFRESH, viewmodel.load),
        ):
            button = QPushButton(text)
            button.setAccessibleName(text)
            button.clicked.connect(action)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        viewmodel.rules_changed.connect(self._show)
        viewmodel.message_changed.connect(self._message.setText)

    def _current(self) -> Any:
        row = self._table.currentRow()
        return self._rows[row].rule if 0 <= row < len(self._rows) else None

    def _approve(self) -> None:
        if (rule := self._current()) is not None:
            self._vm.approve(rule.rule_id)

    def _reject(self) -> None:
        if (rule := self._current()) is not None:
            self._vm.reject(rule.rule_id)

    def _lock(self) -> None:
        if (rule := self._current()) is not None:
            self._vm.toggle_lock(rule.rule_id, not rule.locked)

    def _delete(self) -> None:
        if (rule := self._current()) is not None:
            self._vm.delete(rule.rule_id)

    def _show(self, rows: Sequence[Any]) -> None:
        self._rows = list(rows)
        fill(
            self._table,
            [
                (
                    r.rule.text,
                    r.rule.category.value,
                    r.rule.strength.value,
                    f"{r.rule.scope.value} {r.rule.scope_key}".strip(),
                    f"{r.rule.confidence:.0%}",
                    r.rule.status.value,
                    "ja" if r.rule.locked else "",
                )
                for r in self._rows
            ],
        )
