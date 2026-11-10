"""DaVinci Resolve status page. A passive view."""

from datetime import datetime

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.control_center.application.resolve_status import (
    NEVER_VERIFIED_NOTE,
    ResolveStatus,
)
from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.resolve_viewmodel import (
    ResolveViewModel,
)
from media_house.modules.control_center.presentation.widgets import (
    ERROR,
    NEUTRAL,
    OK,
    WARN,
    fill,
    label,
    page_header,
    set_status,
    status_chip,
    table,
)

_FAILED_STATES = frozenset({"connection_failed", "api_unavailable", "unsupported_version"})


def _yes_no(value: bool) -> str:
    return strings.YES if value else strings.NO


class ResolveView(QWidget):
    def __init__(self, viewmodel: ResolveViewModel) -> None:
        super().__init__()
        self._vm = viewmodel

        self.test_button = QPushButton(strings.BUTTON_TEST_CONNECTION)
        self.test_button.setProperty("role", "primary")
        self.test_button.setAccessibleName(strings.BUTTON_TEST_CONNECTION)
        self.state_label = label(strings.RESOLVE_NOT_CHECKED, "muted")
        self.error = label("", "alert", wrap=True)
        self.error.setAccessibleName("Resolve check error")
        self.error.hide()
        bar = QHBoxLayout()
        bar.addWidget(self.test_button)
        bar.addWidget(self.state_label, 1)

        self.connection_chip = status_chip(NEUTRAL, strings.UNKNOWN)
        self.fields: dict[str, QLabel] = {}
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(4)
        form.addRow(strings.FIELD_CONNECTION, self._chip_row())
        for key in (
            strings.FIELD_DETAIL,
            strings.FIELD_INSTALLED,
            strings.FIELD_RUNNING,
            strings.FIELD_PRODUCT,
            strings.FIELD_VERSION,
            strings.FIELD_EDITION,
            strings.FIELD_PROJECT,
            strings.FIELD_TIMELINE,
            strings.FIELD_PROJECTS,
        ):
            value = label("", wrap=True)
            value.setAccessibleName(key)
            self.fields[key] = value
            form.addRow(key, value)
        self.capability_heading = label("", "heading")
        self.capabilities = table(
            [strings.COL_OPERATION, strings.COL_STATE, strings.COL_SUMMARY, strings.COL_REASON],
            strings.CAPABILITY_TABLE,
        )
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.addLayout(form)
        details_layout.addWidget(self.capability_heading)
        details_layout.addWidget(self.capabilities, 1)
        self.empty = label(strings.RESOLVE_NOT_CHECKED, "empty", wrap=True)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(details)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(page_header(strings.RESOLVE_TITLE, strings.RESOLVE_SUBTITLE))
        layout.addWidget(label(NEVER_VERIFIED_NOTE, "note", wrap=True))
        layout.addLayout(bar)
        layout.addWidget(self.error)
        layout.addWidget(self.body, 1)

        self.test_button.clicked.connect(self._vm.test_connection)
        self._vm.checking_changed.connect(self._on_checking)
        self._vm.status_changed.connect(self._show)
        self._vm.error_changed.connect(self._show_error)

    def _chip_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.connection_chip)
        layout.addStretch(1)
        return row

    def _on_checking(self, active: bool) -> None:
        self.test_button.setEnabled(not active)
        if active:
            self.state_label.setText(strings.RESOLVE_CHECKING)

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
        if message:
            self.state_label.setText("")

    def _show(self, status: ResolveStatus, checked_at: datetime) -> None:
        self.body.setCurrentIndex(1)
        self.state_label.setText(strings.RESOLVE_CHECKED_AT.format(time=f"{checked_at:%H:%M:%S}"))
        level = OK if status.connected else ERROR if status.state in _FAILED_STATES else WARN
        set_status(self.connection_chip, level, status.state.replace("_", " "))
        values = {
            strings.FIELD_DETAIL: status.detail or "—",
            strings.FIELD_INSTALLED: _yes_no(status.installed),
            strings.FIELD_RUNNING: _yes_no(status.running),
            strings.FIELD_PRODUCT: status.product or strings.UNKNOWN,
            strings.FIELD_VERSION: status.version or strings.UNKNOWN,
            strings.FIELD_EDITION: status.edition or strings.UNKNOWN,
            strings.FIELD_PROJECT: status.current_project or "—",
            strings.FIELD_TIMELINE: strings.TIMELINE_NOT_EXPOSED,
            strings.FIELD_PROJECTS: ", ".join(status.projects) or "—",
        }
        for key, value in values.items():
            self.fields[key].setText(value)
        usable = sum(1 for c in status.capabilities if c.usable)
        self.capability_heading.setText(
            strings.CAPABILITIES.format(usable=usable, total=len(status.capabilities))
            if status.connected
            else strings.CAPABILITIES_OFFLINE
        )
        fill(
            self.capabilities,
            [
                (c.operation, c.state.replace("_", " "), c.summary, c.reason)
                for c in status.capabilities
            ],
        )
