"""Pipeline overview: layers -> expandable module list -> module detail. A passive view."""

from collections.abc import Iterable
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.control_center.application.catalogue import (
    DEPENDENCY_NOTE,
    VALIDATION_NOTE,
    ImplementationStatus,
    ModuleEntry,
    ValidationLevel,
    dependants,
    task_label,
)
from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.pipeline_viewmodel import (
    PipelineViewModel,
)
from media_house.modules.control_center.presentation.widgets import (
    ERROR,
    OK,
    WARN,
    label,
    page_header,
    set_status,
    status_chip,
)

MODULE_ROLE = Qt.ItemDataRole.UserRole + 1

_STATUS_LEVEL = {
    ImplementationStatus.IMPLEMENTED: OK,
    ImplementationStatus.IMPLEMENTED_WITH_GAPS: WARN,
}
_VALIDATION_LEVEL = {
    ValidationLevel.SYNTHETIC: WARN,
    ValidationLevel.SYNTHETIC_AND_PIPELINE_RUN: WARN,
    ValidationLevel.NO_REAL_RESOLVE: ERROR,
}


def _bullets(items: Iterable[str]) -> str:
    listed = "".join(f"<li>{escape(i)}</li>" for i in items)
    return f"<ul>{listed}</ul>" if listed else f"<p>{strings.NONE}</p>"


class PipelineView(QWidget):
    def __init__(self, viewmodel: PipelineViewModel) -> None:
        super().__init__()
        self._vm = viewmodel

        self.tree = QTreeWidget()
        self.tree.setAccessibleName(strings.PIPELINE_TREE)
        self.tree.setHeaderLabels([strings.COL_MODULE, strings.COL_STATUS, strings.COL_VALIDATION])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for layer in viewmodel.layers:
            top = QTreeWidgetItem(
                [layer.title, strings.LAYER_COUNT.format(count=len(layer.modules))]
            )
            top.setToolTip(0, layer.summary)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for module in layer.modules:
                child = QTreeWidgetItem(
                    [module.title, module.status.value, module.validation.value]
                )
                child.setData(0, MODULE_ROLE, module.name)
                child.setToolTip(0, module.name)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(layer.key == "layer1")

        # detail panel
        self.detail_title = label("", "title")
        self.detail_name = label("", "muted")
        self.status_chip = status_chip(OK, "")
        self.validation_chip = status_chip(WARN, "")
        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(self.status_chip)
        chips.addWidget(self.validation_chip)
        chips.addStretch(1)
        self.overview = QTextBrowser()
        self.overview.setAccessibleName(strings.TAB_OVERVIEW)
        self.limits = QTextBrowser()
        self.limits.setAccessibleName(strings.TAB_LIMITS)
        self.dependencies = QTextBrowser()
        self.dependencies.setAccessibleName(strings.TAB_DEPENDENCIES)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.overview, strings.TAB_OVERVIEW)
        self.tabs.addTab(self.limits, strings.TAB_LIMITS)
        self.tabs.addTab(self.dependencies, strings.TAB_DEPENDENCIES)
        filled = QWidget()
        filled_layout = QVBoxLayout(filled)
        filled_layout.setContentsMargins(10, 0, 0, 0)
        filled_layout.addWidget(self.detail_title)
        filled_layout.addWidget(self.detail_name)
        filled_layout.addLayout(chips)
        filled_layout.addWidget(self.tabs, 1)
        self.empty = label(strings.DETAIL_EMPTY, "empty", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QStackedWidget()
        self.detail.addWidget(self.empty)
        self.detail.addWidget(filled)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(
            page_header(
                strings.PIPELINE_TITLE,
                strings.PIPELINE_SUBTITLE.format(modules=viewmodel.module_count),
            )
        )
        layout.addWidget(label(VALIDATION_NOTE, "note", wrap=True))
        layout.addWidget(splitter, 1)

        self.tree.currentItemChanged.connect(self._on_current)
        self._vm.module_selected.connect(self._show)
        self._show(self._vm.selected)

    def _on_current(self, item: QTreeWidgetItem | None, _previous: object) -> None:
        name = item.data(0, MODULE_ROLE) if item is not None else None
        self._vm.select(name if isinstance(name, str) else None)

    def _show(self, module: ModuleEntry | None) -> None:
        if module is None:
            self.detail.setCurrentWidget(self.empty)
            return
        self.detail.setCurrentIndex(1)
        self.detail_title.setText(module.title)
        self.detail_name.setText(module.name)
        set_status(self.status_chip, _STATUS_LEVEL[module.status], module.status.value)
        set_status(
            self.validation_chip, _VALIDATION_LEVEL[module.validation], module.validation.value
        )
        self.overview.setHtml(self._overview_html(module))
        self.limits.setHtml(
            _bullets(module.limits) + f"<p><i>{escape(strings.LIMITS_SOURCE)}</i></p>"
        )
        self.dependencies.setHtml(self._dependencies_html(module))

    @staticmethod
    def _overview_html(module: ModuleEntry) -> str:
        task = (
            strings.PIPELINE_TASK.format(
                task=escape(module.pipeline.task), lane=module.pipeline.lane
            )
            if module.pipeline
            else escape(strings.NOT_A_TASK)
        )
        ui = strings.UI_AT.format(where=module.ui) if module.ui else strings.NO_UI
        return f"<p>{escape(module.description)}</p><p>{task}</p><p>{escape(ui)}</p>"

    @staticmethod
    def _dependencies_html(module: ModuleEntry) -> str:
        parts = [f"<p>{escape(DEPENDENCY_NOTE)}</p>"]
        if module.pipeline is not None:
            users = dependants(module.name)
            sections = (
                (strings.REQUIRES, [task_label(t) for t in module.pipeline.needs]),
                (strings.USES, [task_label(t) for t in module.pipeline.wants]),
                (strings.REQUIRED_BY, [n for n, hard in users if hard]),
                (strings.USED_BY, [n for n, hard in users if not hard]),
            )
            for heading, items in sections:
                parts.append(f"<p><b>{escape(heading)}</b></p>{_bullets(items)}")
        else:
            parts.append(f"<p>{escape(strings.NOT_A_TASK)}</p>")
        parts.append(f"<p><b>{escape(strings.READS)}</b></p>{_bullets(module.reads_contracts)}")
        parts.append(f"<p><i>{escape(strings.DEPS_SOURCE)}</i></p>")
        return "".join(parts)
