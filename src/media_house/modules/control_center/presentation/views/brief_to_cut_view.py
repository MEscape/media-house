"""Brief to Cut: the five-step wizard as passive widgets over ``BriefToCutViewModel``.

Nothing here decides anything: each button hands what the person typed to the view model and each
signal fills a widget. Styling comes from ``base.qss`` through ``role`` / ``status`` properties.
"""

import difflib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.brief_to_cut_viewmodel import (
    BriefToCutViewModel,
)
from media_house.modules.control_center.presentation.widgets import (
    ERROR,
    OK,
    WARN,
    fill,
    label,
    page_header,
    set_status,
    table,
)
from media_house.modules.production_project.application.contracts import (
    FlagKind,
    Resolution,
    RewriteProposal,
)

_FORMATS = (
    ("Ranking", "ranking"),
    ("Prospekt-Besprechung", "brochure_review"),
    ("Allgemein", "generic"),
)


def _button(text: str, name: str) -> QPushButton:
    button = QPushButton(text.replace("&", "&&"))
    button.setAccessibleName(name)
    return button


def _context_line(project: Any, variant: Any) -> str:
    """The word count of a variant and, when it was run, what the run's checks found."""
    run = next((r for r in project.eval_runs if r.variant == variant.level), None)
    words = strings.B2C_CONTEXT_WORDS.format(words=variant.word_count)
    if run is None:
        return words
    summary = "; ".join(s.note for s in run.scores if s.note)
    return strings.B2C_CONTEXT_SCORE.format(
        words=variant.word_count, summary=summary or "ohne Messwerte"
    )


class DropZone(QLabel):
    """Accepts a dragged folder anywhere on it: "Alles hier reinziehen, ich sortiere"."""

    def __init__(self, on_folder: Any) -> None:
        super().__init__(f"{strings.B2C_DROP}\n{strings.B2C_DROP_HINT}")
        self._on_folder = on_folder
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setProperty("role", "dropzone")
        self.setMinimumHeight(90)
        self.setAccessibleName(strings.B2C_DROP)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                self._on_folder(path)
                event.acceptProposedAction()
                return
            if path.parent.is_dir():
                # A single file must not implicitly import its entire parent directory.
                self.setText(strings.B2C_DROP_HINT)


class BriefTextEdit(QPlainTextEdit):
    """Drop a document into the briefing editor without inserting its path as text."""

    def __init__(self, on_file: Any) -> None:
        super().__init__()
        self._on_file = on_file

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: Any) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = Path(url.toLocalFile())
                if path.is_file():
                    self._on_file(path)
                    event.acceptProposedAction()
                    return
        else:
            super().dropEvent(event)


class BriefToCutView(QWidget):
    def __init__(self, viewmodel: BriefToCutViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = viewmodel
        self._project: Any = None
        self._proposal: RewriteProposal | None = None
        self._risks: list[Any] = []
        self._entries: list[Any] = []
        self._concepts: list[Any] = []
        self._saved_script = ""
        self._saved_brief = ""
        self._busy = False
        self.setObjectName("briefToCut")
        self.setAccessibleName(strings.B2C_TITLE)

        root = QVBoxLayout(self)
        root.addWidget(page_header(strings.B2C_TITLE, strings.B2C_SUBTITLE))
        root.addLayout(self._header())
        self._summary = label(strings.B2C_SUMMARY_EMPTY, "heading", wrap=True)
        root.addWidget(self._summary)
        self._dirty = label("", "note", wrap=True)
        self._dirty.hide()
        root.addWidget(self._dirty)
        self._banner = label("", "muted", wrap=True)
        root.addWidget(self._banner)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._brief_page())
        self._stack.addWidget(self._risk_page())
        self._stack.addWidget(self._story_page())
        self._stack.addWidget(self._material_page())
        self._stack.addWidget(self._final_page())
        root.addLayout(self._stepper())
        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(self._stack)
        self._brainstorm_dock = self._dock()
        self._brainstorm_dock.setMinimumWidth(280)
        self._brainstorm_dock.setMaximumWidth(420)
        body.addWidget(self._brainstorm_dock)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 1)
        body.setSizes([1000, 340])
        root.addWidget(body, 1)
        navigation = QHBoxLayout()
        self._back = _button(strings.B2C_BACK, strings.B2C_BACK)
        self._back.clicked.connect(lambda: self._go(self._stack.currentIndex() - 1))
        self._next = _button(strings.B2C_NEXT, strings.B2C_NEXT)
        self._next.clicked.connect(lambda: self._go(self._stack.currentIndex() + 1))
        navigation.addWidget(self._back)
        self._operation = label("", "muted", wrap=True)
        navigation.addWidget(self._operation, 1)
        navigation.addWidget(self._next)
        root.addLayout(navigation)
        self._activity = QProgressBar()
        self._activity.setRange(0, 0)
        self._activity.setMaximumHeight(4)
        self._activity.setTextVisible(False)
        self._activity.hide()
        root.addWidget(self._activity)
        self._go(0)
        self._lint_timer = QTimer(self)
        self._lint_timer.setSingleShot(True)
        self._lint_timer.setInterval(500)
        self._lint_timer.timeout.connect(
            lambda: self._vm.refresh_report(self._script.toPlainText())
        )
        self._script.textChanged.connect(lambda: self._lint_timer.start())

        viewmodel.projects_changed.connect(self._set_projects)
        viewmodel.project_changed.connect(self._show_project)
        viewmodel.report_changed.connect(self._show_report)
        viewmodel.ingest_changed.connect(self._show_ingest)
        viewmodel.gate_changed.connect(self._show_gate)
        viewmodel.proposal_changed.connect(self._show_proposal)
        viewmodel.brief_text_loaded.connect(self._brief.setPlainText)
        viewmodel.message_changed.connect(self._set_message)
        viewmodel.busy_changed.connect(self._set_busy)
        viewmodel.operation_changed.connect(self._operation.setText)
        viewmodel.refresh_projects()

    # --- header and stepper ---------------------------------------------------------------------
    def _header(self) -> QVBoxLayout:
        toolbar = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(label(strings.B2C_PROJECT, "muted"))
        self._projects = QComboBox()
        self._projects.setAccessibleName(strings.B2C_PROJECT)
        self._projects.setMinimumWidth(220)
        self._projects.activated.connect(self._open_selected)
        row.addWidget(self._projects)
        self._title = QLineEdit()
        self._title.setPlaceholderText(strings.B2C_PROJECT_TITLE)
        self._title.setAccessibleName(strings.B2C_PROJECT_TITLE)
        self._client = QLineEdit()
        self._client.setPlaceholderText(strings.B2C_PROJECT_CLIENT)
        self._client.setAccessibleName(strings.B2C_PROJECT_CLIENT)
        self._format = QComboBox()
        self._format.setAccessibleName(strings.B2C_PROJECT_FORMAT)
        for text, key in _FORMATS:
            self._format.addItem(text, key)
        self._year = QSpinBox()
        self._year.setRange(2000, 2100)
        self._year.setValue(QDate.currentDate().year())
        self._year.setAccessibleName(strings.B2C_YEAR)
        self._year.setToolTip(strings.B2C_YEAR)
        self._sponsored = QCheckBox(strings.B2C_SPONSORED)
        create = _button(strings.B2C_CREATE, strings.B2C_NEW_PROJECT)
        create.clicked.connect(self._create)
        row.addWidget(self._sponsored)
        row.addStretch(1)
        toolbar.addLayout(row)
        new_project = QHBoxLayout()
        for widget in (self._title, self._client, self._format, self._year, create):
            new_project.addWidget(widget)
        toolbar.addLayout(new_project)
        return toolbar

    def _stepper(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._steps: list[QPushButton] = []
        for index, text in enumerate(strings.B2C_STEPS):
            button = _button(text, text)
            button.setCheckable(True)
            button.setProperty("role", "step")
            button.clicked.connect(lambda _c=False, i=index: self._go(i))
            self._steps.append(button)
            row.addWidget(button)
        self._steps[0].setChecked(True)
        return row

    def _go(self, index: int) -> None:
        index = max(0, min(index, len(self._steps) - 1))
        self._stack.setCurrentIndex(index)
        for i, button in enumerate(self._steps):
            button.setChecked(i == index)
        if index == 2:
            self._vm.refresh_report()
        if hasattr(self, "_back"):
            self._back.setEnabled(index > 0)
            self._next.setEnabled(index < len(self._steps) - 1)

    # --- step 1: briefing -----------------------------------------------------------------------
    def _brief_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        left = QVBoxLayout()
        left.addWidget(label(strings.B2C_BRIEF_HINT, "muted", wrap=True))
        left.addWidget(label(strings.B2C_BRIEF_LABEL, "muted"))
        self._brief = BriefTextEdit(self._vm.load_brief_file)
        self._brief.setPlaceholderText(strings.B2C_BRIEF_PLACEHOLDER)
        self._brief.setAccessibleName(strings.B2C_BRIEF_LABEL)
        left.addWidget(self._brief, 3)
        load = _button(strings.B2C_BRIEF_LOAD, strings.B2C_BRIEF_LOAD)
        load.clicked.connect(self._load_brief)
        left.addWidget(load)
        left.addWidget(label(strings.B2C_IDEAS_LABEL, "muted"))
        self._ideas = QPlainTextEdit()
        self._ideas.setPlaceholderText(strings.B2C_IDEAS_PLACEHOLDER)
        self._ideas.setAccessibleName(strings.B2C_IDEAS_LABEL)
        left.addWidget(self._ideas, 1)
        self._enrich = QCheckBox(strings.B2C_MODEL_OPT_IN)
        self._enrich.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._enrich.setToolTip(strings.B2C_MODEL_OPT_IN)
        left.addWidget(self._enrich)
        extract = _button(strings.B2C_EXTRACT, strings.B2C_EXTRACT)
        extract.setProperty("role", "primary")
        extract.clicked.connect(
            lambda: self._vm.extract(
                self._brief.toPlainText(),
                self._ideas.toPlainText(),
                use_model=self._enrich.isChecked(),
            )
        )
        left.addWidget(extract)
        layout.addLayout(left, 1)
        right = QVBoxLayout()
        self._model = QTreeWidget()
        self._model.setHeaderLabels(list(strings.B2C_MODEL_TREE))
        self._model.setAccessibleName("Brief Model")
        self._model.header().setStretchLastSection(False)
        self._model.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._model.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._model.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        right.addWidget(self._model, 1)
        export = _button(strings.B2C_EXPORT_DOCS, strings.B2C_EXPORT_DOCS)
        export.clicked.connect(self._export_docs)
        right.addWidget(export)
        layout.addLayout(right, 1)
        return page

    def _load_brief(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, strings.B2C_BRIEF_LOAD, "", strings.B2C_BRIEF_FILTER
        )
        if name:
            self._vm.load_brief_file(Path(name))

    def _export_docs(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, strings.B2C_EXPORT_DOCS)
        if folder:
            self._vm.export_brief_documents(Path(folder))

    # --- step 2: risks --------------------------------------------------------------------------
    def _risk_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        run = _button(strings.B2C_RISK_RUN, strings.B2C_RISK_RUN)
        run.clicked.connect(self._vm.run_risks)
        replace = _button(strings.B2C_RISK_REPLACE, strings.B2C_RISK_REPLACE)
        replace.clicked.connect(lambda: self._decide(keep=False))
        keep = _button(strings.B2C_RISK_KEEP, strings.B2C_RISK_KEEP)
        keep.clicked.connect(lambda: self._decide(keep=True))
        for widget in (run, replace, keep):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self._risk_table = table(strings.B2C_RISK_HEADERS, "Risiken")
        layout.addWidget(self._risk_table, 1)
        self._risk_status = label("", "muted")
        layout.addWidget(self._risk_status)
        return page

    def _decide(self, *, keep: bool) -> None:
        row = self._risk_table.currentRow()
        if not 0 <= row < len(self._risks):
            return
        risk = self._risks[row]
        reason = ""
        if keep:
            reason, accepted = QInputDialog.getText(
                self, strings.B2C_RISK_KEEP, strings.B2C_RISK_REASON
            )
            if not accepted:
                return
        self._vm.decide_risk(risk.risk_id, keep, reason)

    # --- step 3: story and script ---------------------------------------------------------------
    def _story_page(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setAccessibleName("Story und Script")
        story = QWidget()
        sl = QVBoxLayout(story)
        row = QHBoxLayout()
        brainstorm = _button(strings.B2C_BRAINSTORM, strings.B2C_BRAINSTORM)
        brainstorm.clicked.connect(lambda: self._vm.brainstorm())
        choose = _button(strings.B2C_CHOOSE, strings.B2C_CHOOSE)
        choose.clicked.connect(self._choose)
        generate = _button(strings.B2C_GENERATE, strings.B2C_GENERATE)
        generate.clicked.connect(self._generate_script)
        for widget in (brainstorm, choose, generate):
            row.addWidget(widget)
        row.addStretch(1)
        sl.addLayout(row)
        self._concept_list = QListWidget()
        self._concept_list.setAccessibleName("Konzepte")
        sl.addWidget(self._concept_list, 1)
        tabs.addTab(story, "Story")
        tabs.addTab(self._script_tab(), "Script")
        self._slots = table(strings.B2C_SLOT_HEADERS, "Technik-Felder")
        self._slots.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self._slots.itemChanged.connect(self._slot_edited)
        self._facts = table(strings.B2C_FACT_HEADERS, "Fakten")
        facts_tab = QWidget()
        fl = QVBoxLayout(facts_tab)
        fl.addWidget(label("Fakten", "muted"))
        fl.addWidget(self._facts, 1)
        fl.addWidget(label("Technik-Felder (Doppelklick zum Ausfüllen)", "muted"))
        fl.addWidget(self._slots, 2)
        tabs.addTab(facts_tab, "Fakten & Technik")
        self._scenes = table(strings.B2C_SCENE_HEADERS, "Szenenplan")
        tabs.addTab(self._scenes, "Szenenplan")
        self._requirements = table(strings.B2C_REQ_HEADERS, "Asset-Empfehlungen")
        tabs.addTab(self._requirements, "Asset-Empfehlungen")
        tabs.addTab(self._context_tab(), "Kontext")
        return tabs

    def _script_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        self._script = QPlainTextEdit()
        self._script.setAccessibleName("Script")
        ll.addWidget(self._script, 3)
        ll.addWidget(label(strings.B2C_DRAFT_NOTE, "muted", wrap=True))
        versions = QHBoxLayout()
        self._versions = QComboBox()
        self._versions.setAccessibleName(strings.B2C_VERSION)
        restore = _button(strings.B2C_RESTORE, strings.B2C_RESTORE)
        restore.clicked.connect(self._restore_script)
        versions.addWidget(self._versions, 1)
        versions.addWidget(restore)
        ll.addLayout(versions)
        save = _button(strings.B2C_SAVE_SCRIPT, strings.B2C_SAVE_SCRIPT)
        save.clicked.connect(lambda: self._vm.save_script(self._script.toPlainText()))
        ll.addWidget(save)
        self._length = label("", "muted")
        ll.addWidget(self._length)
        ll.addWidget(label(strings.B2C_LINT_TITLE, "muted"))
        self._lint = QListWidget()
        self._lint.setAccessibleName(strings.B2C_LINT_TITLE)
        ll.addWidget(self._lint, 2)
        split.addWidget(left)
        layout.addWidget(split)
        return tab

    def _dock(self) -> QWidget:
        """The brainstorm dock: an instruction for one scene, a diff, and "Übernehmen"."""
        dock = QWidget()
        layout = QVBoxLayout(dock)
        layout.addWidget(label(strings.B2C_REWRITE_TITLE, "title"))
        self._scene_pick = QComboBox()
        self._scene_pick.setAccessibleName("Szene")
        layout.addWidget(self._scene_pick)
        self._instruction = QLineEdit()
        self._instruction.setPlaceholderText(strings.B2C_REWRITE_INSTRUCTION)
        self._instruction.setAccessibleName(strings.B2C_REWRITE_INSTRUCTION)
        layout.addWidget(self._instruction)
        quick = QVBoxLayout()
        for text in strings.B2C_QUICK:
            b = _button(text, text)
            b.clicked.connect(lambda _c=False, t=text: self._propose(t))
            quick.addWidget(b)
        layout.addLayout(quick)
        run = _button(strings.B2C_REWRITE_RUN, strings.B2C_REWRITE_RUN)
        run.clicked.connect(lambda: self._propose(self._instruction.text()))
        layout.addWidget(run)
        self._diff = QPlainTextEdit()
        self._diff.setReadOnly(True)
        self._diff.setAccessibleName("Änderung")
        layout.addWidget(self._diff, 1)
        row = QVBoxLayout()
        self._apply = _button(strings.B2C_REWRITE_APPLY, strings.B2C_REWRITE_APPLY)
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._apply_proposal)
        self._discard = _button(strings.B2C_REWRITE_DISCARD, strings.B2C_REWRITE_DISCARD)
        self._discard.clicked.connect(self._vm.discard_proposal)
        row.addWidget(self._apply)
        row.addWidget(self._discard)
        layout.addLayout(row)
        return dock

    def _propose(self, instruction: str) -> None:
        if self._has_unsaved_script():
            self._set_message(strings.B2C_UNSAVED)
            return
        scene = self._scene_pick.currentData()
        if scene and instruction.strip():
            self._vm.propose_rewrite(str(scene), instruction.strip())
        else:
            self._set_message(strings.B2C_NO_SCENE)

    def _apply_proposal(self) -> None:
        if self._has_unsaved_script():
            self._set_message(strings.B2C_UNSAVED)
            return
        if self._proposal is not None:
            self._vm.apply_proposal(self._proposal)

    def _context_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        build = _button(strings.B2C_CONTEXT_BUILD, strings.B2C_CONTEXT_BUILD)
        build.clicked.connect(self._vm.build_context)
        layout.addWidget(build)
        run_row = QHBoxLayout()
        run_row.addWidget(label(strings.B2C_VARIANT_LEVEL, "muted"))
        self._variant_level = QComboBox()
        self._variant_level.setAccessibleName(strings.B2C_VARIANT_LEVEL)
        for level, text in strings.B2C_LEVEL_LABELS.items():
            self._variant_level.addItem(text, level)
        run_row.addWidget(self._variant_level)
        run = _button(strings.B2C_VARIANT_RUN, strings.B2C_VARIANT_RUN)
        run.clicked.connect(self._run_variant)
        choose = _button(strings.B2C_CONTEXT_CHOOSE, strings.B2C_CONTEXT_CHOOSE)
        choose.clicked.connect(self._vm.choose_context)
        run_row.addWidget(run)
        run_row.addWidget(choose)
        run_row.addStretch(1)
        layout.addLayout(run_row)
        self._winner = label("", "muted")
        layout.addWidget(self._winner)
        self._context_views: dict[str, QPlainTextEdit] = {}
        self._context_counts: dict[str, QLabel] = {}
        variants = QTabWidget()
        layout.addWidget(variants, 1)
        for level, title in strings.B2C_LEVEL_LABELS.items():
            variant_tab = QWidget()
            variant_layout = QVBoxLayout(variant_tab)
            edit = QPlainTextEdit()
            edit.setAccessibleName(f"Kontext {level}")
            variant_layout.addWidget(edit, 1)
            count = label("", "muted")
            variant_layout.addWidget(count)
            save = _button(strings.B2C_CONTEXT_SAVE, f"{strings.B2C_CONTEXT_SAVE} {title}")
            save.clicked.connect(
                lambda _checked=False, lv=level, field=edit: self._vm.edit_context(
                    lv, field.toPlainText()
                )
            )
            variant_layout.addWidget(save)
            variants.addTab(variant_tab, title)
            self._context_views[level] = edit
            self._context_counts[level] = count
        return tab

    def _run_variant(self) -> None:
        if self._has_unsaved_drafts():
            self._set_message(strings.B2C_UNSAVED_DRAFTS)
            return
        files, _filter = QFileDialog.getOpenFileNames(self, strings.B2C_VARIANT_PICK)
        if files:
            self._vm.run_variant(str(self._variant_level.currentData()), [Path(f) for f in files])

    def _choose(self) -> None:
        row = self._concept_list.currentRow()
        if 0 <= row < len(self._concepts):
            self._vm.choose_concept(self._concepts[row].concept_id)

    def _slot_edited(self, item: Any) -> None:
        if item.column() == 2:
            key = self._slots.item(item.row(), 0)
            if key is not None:
                self._vm.fill_slot(key.data(Qt.ItemDataRole.UserRole), item.text())

    # --- step 4: material -----------------------------------------------------------------------
    def _material_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        plan = _button(strings.B2C_PLAN, strings.B2C_PLAN)
        plan.clicked.connect(self._vm.plan_material)
        pick = _button(strings.B2C_PICK_FOLDER, strings.B2C_PICK_FOLDER)
        pick.clicked.connect(self._pick_folder)
        row.addWidget(plan)
        row.addWidget(pick)
        row.addStretch(1)
        layout.addLayout(row)
        slots = QSplitter(Qt.Orientation.Horizontal)
        self._material_slots = QTreeWidget()
        self._material_slots.setHeaderLabels(list(strings.B2C_MATERIAL_HEADERS))
        self._material_slots.setAccessibleName(strings.B2C_PLAN)
        self._material_slots.header().setStretchLastSection(False)
        self._material_slots.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._material_slots.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._material_slots.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._material_slots.setColumnWidth(2, 90)
        self._material_slots.currentItemChanged.connect(self._show_requirement)
        slots.addWidget(self._material_slots)
        self._material_detail = QPlainTextEdit()
        self._material_detail.setReadOnly(True)
        self._material_detail.setPlaceholderText(strings.B2C_MATERIAL_EMPTY)
        slots.addWidget(self._material_detail)
        layout.addWidget(slots, 2)
        layout.addWidget(DropZone(self._vm.ingest))
        self._messages = QListWidget()
        self._messages.setAccessibleName("Hinweise")
        self._messages.hide()
        layout.addWidget(self._messages, 1)
        self._ingest_table = table(strings.B2C_INGEST_HEADERS, "Material")
        self._ingest_table.hide()
        layout.addWidget(self._ingest_table, 3)
        flags = QHBoxLayout()
        for text, how in (
            (strings.B2C_SETTLE_APPROVE, Resolution.APPROVED),
            (strings.B2C_SETTLE_BLUR, Resolution.BLURRED),
            (strings.B2C_SETTLE_REPLACE, Resolution.REPLACED),
        ):
            b = _button(text, text)
            b.clicked.connect(lambda _c=False, h=how: self._settle(h))
            flags.addWidget(b)
        flags.addStretch(1)
        layout.addLayout(flags)
        return page

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, strings.B2C_PICK_FOLDER)
        if folder:
            self._vm.ingest(Path(folder))

    def _settle(self, how: Resolution) -> None:
        row = self._ingest_table.currentRow()
        if not 0 <= row < len(self._entries):
            return
        entry = self._entries[row]
        flag = next((f for f in entry.flags if f.open), None)
        if flag is None:
            return
        note = ""
        if how is Resolution.APPROVED:
            note, accepted = QInputDialog.getText(
                self, strings.B2C_SETTLE_APPROVE, strings.B2C_SETTLE_NOTE
            )
            if not accepted:
                return
        self._vm.settle(entry.file, FlagKind(flag.kind), how, note or how.value)

    # --- step 5: finalisation -------------------------------------------------------------------
    def _final_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(label(strings.B2C_TRACKING, "muted"))
        self._tracking = QLineEdit()
        self._tracking.setAccessibleName(strings.B2C_TRACKING)
        row.addWidget(self._tracking, 1)
        meta = _button(strings.B2C_METADATA, strings.B2C_METADATA)
        meta.clicked.connect(lambda: self._vm.build_metadata(self._tracking.text()))
        timeline = _button(strings.B2C_TIMELINE, strings.B2C_TIMELINE)
        timeline.clicked.connect(self._vm.attach_timeline)
        gate = _button(strings.B2C_GATE, strings.B2C_GATE)
        gate.clicked.connect(self._vm.check_gate)
        export = _button(strings.B2C_EXPORT, strings.B2C_EXPORT)
        export.clicked.connect(self._export)
        for widget in (meta, timeline, gate, export):
            row.addWidget(widget)
        layout.addLayout(row)
        self._metadata = QPlainTextEdit()
        self._metadata.setReadOnly(True)
        self._metadata.setAccessibleName("Metadaten")
        layout.addWidget(self._metadata, 2)
        self._gate_status = label("", "muted")
        layout.addWidget(self._gate_status)
        self._gate_list = QListWidget()
        self._gate_list.setAccessibleName("Prüfergebnis")
        layout.addWidget(self._gate_list, 2)
        return page

    def _export(self) -> None:
        if self._has_unsaved_drafts():
            self._set_message(strings.B2C_UNSAVED_DRAFTS)
            return
        folder = QFileDialog.getExistingDirectory(self, strings.B2C_EXPORT)
        if folder:
            self._vm.export_package(Path(folder))

    # --- showing state --------------------------------------------------------------------------
    def _set_message(self, text: str) -> None:
        self._banner.setText(text)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.setCursor(Qt.CursorShape.BusyCursor if busy else Qt.CursorShape.ArrowCursor)
        self._activity.setVisible(busy)
        for button in self.findChildren(QPushButton):
            if button not in (*self._steps, self._back, self._next):
                button.setEnabled(not busy)
        self._projects.setEnabled(not busy)
        if not busy:
            self._apply.setEnabled(self._proposal is not None)

    def _has_unsaved_script(self) -> bool:
        return self._script.toPlainText() != self._saved_script

    def _has_unsaved_drafts(self) -> bool:
        if self._project is None:
            return False
        contexts = {v.level: v.text for v in self._project.contexts}
        return (
            self._has_unsaved_script()
            or self._brief.toPlainText() != self._project.brief_text
            or self._ideas.toPlainText() != self._project.ideas
            or any(
                field.toPlainText() != contexts.get(level, "")
                for level, field in self._context_views.items()
            )
        )

    def _generate_script(self) -> None:
        if self._has_unsaved_script():
            self._set_message(strings.B2C_UNSAVED)
            return
        self._vm.generate_script()

    def _restore_script(self) -> None:
        if self._has_unsaved_script():
            self._set_message(strings.B2C_UNSAVED)
            return
        if (version := self._versions.currentData()) is not None:
            self._vm.restore_script(int(version))

    def _create(self) -> None:
        if self._has_unsaved_drafts():
            self._set_message(strings.B2C_UNSAVED_DRAFTS)
            return
        self._vm.create_project(
            self._title.text(),
            self._client.text(),
            str(self._format.currentData()),
            self._year.value(),
            sponsored=self._sponsored.isChecked(),
        )

    def _open_selected(self, index: int) -> None:
        if self._has_unsaved_drafts():
            self._set_message(strings.B2C_UNSAVED_DRAFTS)
            selected = self._projects.findData(self._vm.project_id)
            if selected >= 0:
                self._projects.setCurrentIndex(selected)
            return
        project_id = self._projects.itemData(index)
        if project_id:
            self._vm.open_project(str(project_id))

    def _set_projects(self, rows: Sequence[Any]) -> None:
        self._projects.clear()
        for row in rows:
            self._projects.addItem(f"{row.title} ({row.client})", row.project_id)
        if self._vm.project_id:
            at = self._projects.findData(self._vm.project_id)
            if at >= 0:
                self._projects.setCurrentIndex(at)

    def _show_project(self, project: Any) -> None:
        previous = self._project
        same_project = previous is not None and previous.project_id == project.project_id
        locally_edited = self._has_unsaved_script()
        self._project = project
        if not same_project:
            for field in self._context_views.values():
                field.clear()
            for count in self._context_counts.values():
                count.clear()
            self._metadata.clear()
            self._gate_list.clear()
            self._gate_status.clear()
            self._ingest_table.setRowCount(0)
            self._ingest_table.hide()
            self._messages.hide()
            self._messages.clear()
            self._lint.clear()
            self._length.clear()
            self._entries = []
            self._show_proposal(None)
        if not same_project or self._brief.toPlainText() == self._saved_brief:
            self._brief.setPlainText(project.brief_text)
        self._saved_brief = project.brief_text
        if not same_project or self._ideas.toPlainText() == previous.ideas:
            self._ideas.setPlainText(project.ideas)
        if not same_project:
            self._sponsored.setChecked(project.settings.sponsored)
        self._versions.clear()
        for version in reversed(project.script_versions):
            self._versions.addItem(f"{version.version} · {version.label}", version.version)
        self._show_model(project)
        self._show_risks(project)
        self._concepts = list(project.concepts)
        self._concept_list.clear()
        for c in self._concepts:
            mark = "✓ " if c.concept_id == project.chosen_concept else ""
            self._concept_list.addItem(
                f"{mark}{c.logline}\n   Hook: {c.hook}\n   Faden: {c.red_thread}"
            )
        if not same_project or not locally_edited or project.script == self._script.toPlainText():
            self._script.blockSignals(True)
            self._script.setPlainText(project.script)
            self._script.blockSignals(False)
            self._saved_script = project.script
        self._show_facts(project)
        self._show_plan(project)
        for variant in project.contexts:
            field = self._context_views[variant.level]
            old = (
                next((v.text for v in previous.contexts if v.level == variant.level), "")
                if same_project
                else ""
            )
            if (
                not same_project
                or field.toPlainText() == old
                or field.toPlainText() == variant.text
            ):
                field.setPlainText(variant.text)
            self._context_counts[variant.level].setText(_context_line(project, variant))
        self._winner.setText(
            strings.B2C_CONTEXT_WINNER.format(level=project.context_winner)
            if project.context_winner
            else ""
        )
        if project.metadata is not None:
            meta = project.metadata
            self._metadata.setPlainText(
                "\n".join(meta.titles) + "\n\n" + meta.description + "\n\n" + ", ".join(meta.tags)
            )
        from media_house.modules.production_project.application.contracts import parse_script

        self._summary.setText(
            strings.B2C_SUMMARY.format(
                products=sum(f.kind == "product" for f in project.facts),
                facts=len(project.facts),
                questions=len(project.brief.open_questions) if project.brief else 0,
                scenes=len(parse_script(project.script).scenes) if project.script else 0,
            )
        )
        self._dirty.setVisible(bool(project.dirty))
        self._dirty.setText(
            strings.B2C_DIRTY.format(
                items=", ".join(
                    strings.B2C_ARTIFACT_LABELS.get(a.value, a.value) for a in project.dirty
                )
            )
        )

    def _show_requirement(self, item: Any, _previous: Any = None) -> None:
        requirement = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if requirement is None:
            self._material_detail.clear()
            return
        self._material_detail.setPlainText(
            strings.B2C_MATERIAL_DETAIL.format(
                instruction=requirement.instruction,
                checklist="\n".join(f"☐ {step}" for step in requirement.howto),
                name=requirement.target_name_pattern,
                types=", ".join(requirement.accepts),
                duration=requirement.duration_hint or "-",
                depends=", ".join(requirement.depends_on) or "-",
            )
        )

    def _show_model(self, project: Any) -> None:
        self._model.clear()
        brief = project.brief
        if brief is None:
            return
        groups: dict[str, QTreeWidgetItem] = {}

        def group(name: str) -> QTreeWidgetItem:
            if name not in groups:
                groups[name] = QTreeWidgetItem(self._model, [name])
            return groups[name]

        for c in brief.constraints:
            QTreeWidgetItem(
                group("Vorgaben"), [c.text, strings.B2C_KIND_LABELS[c.kind.value], c.source.ref]
            ).setToolTip(0, c.source.quote)
        for f in brief.facts:
            QTreeWidgetItem(
                group("Fakten"),
                [
                    f"{f.label}: {f.value}",
                    "ungeprüft" if not f.verified else "geprüft",
                    f.source.ref if f.source else "",
                ],
            )
        for d in brief.deadlines:
            QTreeWidgetItem(
                group("Termine"), [f"{d.label} {d.date} {d.time}", d.kind, d.source.ref]
            )
        for m in brief.mandatory:
            QTreeWidgetItem(group("Pflichtelemente"), [m.text, m.kind.value, m.source.ref])
        for q in brief.open_questions:
            QTreeWidgetItem(
                group("Offene Fragen"), [q.text, str(q.priority), q.source.ref if q.source else ""]
            )
        for name, node in groups.items():
            node.setText(1, str(node.childCount()))
            node.setExpanded(name in {"Vorgaben", "Offene Fragen"})

    def _show_risks(self, project: Any) -> None:
        self._risks = list(project.risks)
        decided = {d.risk_id: d.action.value for d in project.decisions}
        fill(
            self._risk_table,
            [
                (
                    r.severity.value + (f" ({decided[r.risk_id]})" if r.risk_id in decided else ""),
                    r.kind.value,
                    r.where,
                    r.why,
                    r.safe_alternative,
                )
                for r in self._risks
            ],
        )
        set_status(
            self._risk_status,
            OK if not self._risks else WARN,
            strings.B2C_RISK_NONE if not self._risks else f"{len(self._risks)} Risiken",
        )

    def _show_facts(self, project: Any) -> None:
        fill(
            self._facts,
            [
                (
                    f.key,
                    f"{f.value} {f.unit}".strip(),
                    f.source.ref if f.source else "",
                    "ja" if f.verified else "nein",
                )
                for f in project.facts
                if f.kind != "product"
            ],
        )
        self._slots.blockSignals(True)
        fill(self._slots, [(s.key, s.label, s.value) for s in project.slots])
        for row, slot in enumerate(project.slots):
            for col in (0, 1):
                if (cell := self._slots.item(row, col)) is not None:
                    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if (first := self._slots.item(row, 0)) is not None:
                first.setData(Qt.ItemDataRole.UserRole, slot.key)
        self._slots.blockSignals(False)

    def _show_plan(self, project: Any) -> None:
        from media_house.modules.production_project.application.contracts import parse_script

        scenes = parse_script(project.script).scenes if project.script else ()
        fill(
            self._scenes, [(s.scene_id, s.function, s.time_hint, s.graphics_intent) for s in scenes]
        )
        self._scene_pick.clear()
        for s in scenes:
            self._scene_pick.addItem(f"{s.scene_id} {s.heading}", s.scene_id)
        fill(
            self._requirements,
            [
                (
                    str(r.order),
                    r.title,
                    r.folder,
                    strings.B2C_PRIORITY_LABELS[r.priority.value],
                    strings.B2C_TIMING_LABELS[r.timing.value],
                    strings.B2C_STATUS_LABELS[r.status.value],
                )
                for r in project.requirements
            ],
        )
        self._material_slots.clear()
        folders: dict[str, QTreeWidgetItem] = {}
        for requirement in project.requirements:
            folder = requirement.folder
            if folder not in folders:
                items = [r for r in project.requirements if r.folder == folder]
                filled = sum(r.status.value == "filled" for r in items)
                folders[folder] = QTreeWidgetItem(
                    self._material_slots, [folder, f"{filled} / {len(items)}"]
                )
            row = QTreeWidgetItem(
                folders[folder],
                [
                    requirement.title,
                    strings.B2C_STATUS_LABELS.get(
                        requirement.status.value, requirement.status.value
                    ),
                    ", ".join(requirement.scene_ids),
                ],
            )
            row.setData(0, Qt.ItemDataRole.UserRole, requirement)
            row.setToolTip(0, requirement.instruction)
        self._material_slots.expandAll()

    def _show_report(self, report: Any) -> None:
        self._lint.clear()
        for f in report.findings:
            item = QListWidgetItem(f"[{f.severity.value}] {f.where}: {f.message}")
            item.setToolTip(f.safe_alternative)
            self._lint.addItem(item)
        self._length.setText(strings.B2C_LENGTH.format(minutes=f"{report.seconds / 60:.1f}"))

    def _show_proposal(self, proposal: Any) -> None:
        self._proposal = proposal
        self._apply.setEnabled(proposal is not None)
        if proposal is None:
            self._diff.clear()
            return
        diff = difflib.unified_diff(
            proposal.before.splitlines(),
            proposal.after.splitlines(),
            "vorher",
            "nachher",
            lineterm="",
            n=1,
        )
        self._diff.setPlainText("\n".join(diff) or "Keine Änderung.")

    def _show_ingest(self, report: Any) -> None:
        manifest = report.project.manifest
        self._entries = list(manifest.entries) if manifest else []
        self._ingest_table.setVisible(bool(self._entries))
        fill(
            self._ingest_table,
            [
                (
                    e.file,
                    e.role or "?",
                    e.slot or "-",
                    "ok"
                    if e.usable
                    else "prüfen: " + "; ".join(f.message for f in e.flags if f.open),
                )
                for e in self._entries
            ],
        )
        self._messages.clear()
        for message in report.reconciliation.messages:
            self._messages.addItem(message)
        for name in report.ignored:
            self._messages.addItem(f"Ignoriert: {name}")
        self._messages.setVisible(self._messages.count() > 0)

    def _show_gate(self, report: Any) -> None:
        self._gate_list.clear()
        for f in report.findings:
            overridden = f" (bewusst behalten: {f.overridden})" if f.overridden else ""
            self._gate_list.addItem(f"[{f.severity.value}] {f.where}: {f.message}{overridden}")
        if report.can_export:
            set_status(self._gate_status, OK, strings.B2C_GATE_OK)
        else:
            set_status(
                self._gate_status,
                ERROR,
                strings.B2C_GATE_BLOCKED.format(count=len(report.blocking)),
            )
