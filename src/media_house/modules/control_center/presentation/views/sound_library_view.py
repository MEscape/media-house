"""Sound library page: licence state at a glance, filters, manifest import. A passive view."""

from collections.abc import Sequence
from html import escape

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.control_center.application.sound_library import (
    CommercialUse,
    SoundFilter,
    SoundRow,
)
from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.sound_library_viewmodel import (
    SoundLibraryViewModel,
)
from media_house.modules.control_center.presentation.widgets import fill, label, page_header, table

PLANNER_COLUMN = 4


def _duration(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _planner(row: SoundRow) -> str:
    return f"✓ {strings.PLANNER_OK}" if row.selectable else f"✕ {strings.PLANNER_NEVER}"


class SoundLibraryView(QWidget):
    def __init__(self, viewmodel: SoundLibraryViewModel) -> None:
        super().__init__()
        self._vm = viewmodel
        self._rows: list[SoundRow] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText(strings.SEARCH_PLACEHOLDER)
        self.search.setAccessibleName(strings.SEARCH_LABEL)
        self.search.setClearButtonEnabled(True)
        self.kind = QComboBox()
        self.kind.setAccessibleName(strings.FILTER_KIND)
        for text, value in (
            (strings.KIND_ALL, ""),
            (strings.KIND_MUSIC, "music"),
            (strings.KIND_SFX, "sfx"),
        ):
            self.kind.addItem(text, value)
        self.commercial = QComboBox()
        self.commercial.setAccessibleName(strings.FILTER_COMMERCIAL)
        for text, use in (
            (strings.COMMERCIAL_ANY, None),
            (strings.COMMERCIAL_YES, CommercialUse.YES),
            (strings.COMMERCIAL_NO, CommercialUse.NO),
            (strings.COMMERCIAL_UNKNOWN, CommercialUse.UNKNOWN),
        ):
            self.commercial.addItem(text, use)
        self.selectable = QCheckBox(strings.FILTER_SELECTABLE)
        self.refresh_button = QPushButton(strings.BUTTON_REFRESH)
        self.import_button = QPushButton(strings.BUTTON_IMPORT_MANIFEST)
        self.import_button.setProperty("role", "primary")
        filters = QHBoxLayout()
        filters.setSpacing(6)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.kind)
        filters.addWidget(self.commercial)
        filters.addWidget(self.selectable)
        filters.addStretch(1)
        filters.addWidget(self.refresh_button)
        filters.addWidget(self.import_button)

        self.summary = label("", "muted")
        self.error = label("", "alert", wrap=True)
        self.error.setAccessibleName("Sound library error")
        self.error.hide()
        self.table = table(
            [
                strings.COL_TITLE,
                strings.COL_KIND,
                strings.COL_DURATION,
                strings.COL_COMMERCIAL,
                strings.COL_PLANNER,
                strings.COL_ATTRIBUTION,
                strings.COL_SOURCE,
                strings.COL_AUTHOR,
            ],
            strings.SOUNDS_TABLE,
        )
        self.empty = label(strings.SOUNDS_LOADING, "empty", wrap=True)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(self.table)
        self.detail = label(strings.SOUND_DETAIL_EMPTY, "muted", wrap=True)
        self.detail.setAccessibleName("Sound licence details")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(page_header(strings.SOUNDS_TITLE, strings.SOUNDS_SUBTITLE))
        layout.addLayout(filters)
        layout.addWidget(self.summary)
        layout.addWidget(self.error)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.detail)

        self.search.textChanged.connect(self._filter_changed)
        self.kind.currentIndexChanged.connect(self._filter_changed)
        self.commercial.currentIndexChanged.connect(self._filter_changed)
        self.selectable.toggled.connect(self._filter_changed)
        self.refresh_button.clicked.connect(self._vm.refresh)
        self.import_button.clicked.connect(self._choose_manifest)
        self.table.currentCellChanged.connect(lambda row, *_: self._show_detail(row))
        self._vm.rows_changed.connect(self._show_rows)
        self._vm.loading_changed.connect(self._on_loading)
        self._vm.import_running.connect(lambda busy: self.import_button.setEnabled(not busy))
        self._vm.error_changed.connect(self._show_error)

    def _filter_changed(self, *_: object) -> None:
        use = self.commercial.currentData()
        self._vm.set_filter(
            SoundFilter(
                kind=str(self.kind.currentData() or ""),
                commercial_use=use if isinstance(use, CommercialUse) else None,
                selectable_only=self.selectable.isChecked(),
                text=self.search.text(),
            )
        )

    def _choose_manifest(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, strings.IMPORT_DIALOG, "", strings.IMPORT_FILTER
        )
        if path:
            self._vm.import_manifest(path)

    def _on_loading(self, loading: bool) -> None:
        self.refresh_button.setEnabled(not loading)
        if loading and not self._vm.loaded:
            self.empty.setText(strings.SOUNDS_LOADING)
            self.body.setCurrentWidget(self.empty)

    def _show_rows(
        self, rows: Sequence[SoundRow], total: int, selectable: int, blocked: int
    ) -> None:
        self._rows = list(rows)
        self.summary.setText(
            strings.SOUNDS_SUMMARY.format(
                shown=len(rows), total=total, selectable=selectable, blocked=blocked
            )
        )
        if not rows:
            self.empty.setText(strings.SOUNDS_EMPTY if total == 0 else strings.SOUNDS_NO_MATCH)
            self.body.setCurrentWidget(self.empty)
        else:
            self.body.setCurrentWidget(self.table)
        fill(
            self.table,
            [
                (
                    r.title,
                    r.kind,
                    _duration(r.duration_seconds),
                    r.commercial_use.value,
                    _planner(r),
                    strings.ATTRIBUTION_REQUIRED
                    if r.attribution_required
                    else strings.ATTRIBUTION_NONE,
                    r.source or "—",
                    r.author or "—",
                )
                for r in rows
            ],
        )
        for index, row in enumerate(rows):
            item = self.table.item(index, PLANNER_COLUMN)
            if item is not None and not row.selectable:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        self.table.resizeColumnsToContents()
        self._show_detail(self.table.currentRow())

    def _show_detail(self, index: int) -> None:
        if not 0 <= index < len(self._rows):
            self.detail.setText(strings.SOUND_DETAIL_EMPTY)
            return
        r = self._rows[index]
        attribution = r.attribution_text or (
            strings.ATTRIBUTION_REQUIRED if r.attribution_required else strings.ATTRIBUTION_NONE
        )
        self.detail.setText(
            strings.SOUND_DETAIL.format(
                title=escape(r.title),
                licence=escape(r.licence_name or "—"),
                attribution=escape(attribution),
                restrictions=escape(r.restrictions or "—"),
                measured=strings.YES if r.measured else strings.NO,
                asset_id=escape(r.asset_id),
            )
        )

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
        if message and not self._vm.loaded:
            self.empty.setText(strings.SOUNDS_LOAD_FAILED)
