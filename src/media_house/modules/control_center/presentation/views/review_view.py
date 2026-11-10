"""Review workspace page. A passive view: it renders the review and forwards a person's intent.

Questions to the person (confirmations, reasons) go through ``ask`` / ``prompt`` so tests can
answer them without a modal dialog.
"""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.control_center.application.review_workspace import (
    OPEN_PROPOSALS,
    AnchorAsset,
    ExportProfile,
    ReadinessReport,
    ReviewSession,
    ReviewSnapshot,
    SectionStatus,
    Verdict,
)
from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.review_viewmodel import (
    ReviewViewModel,
)
from media_house.modules.control_center.presentation.widgets import (
    fill,
    label,
    page_header,
    table,
)

ID_ROLE = Qt.ItemDataRole.UserRole + 1
PLAN_ROLE = Qt.ItemDataRole.UserRole + 2
OPEN_ROLE = Qt.ItemDataRole.UserRole + 3
STATUS_ROLE = Qt.ItemDataRole.UserRole + 4


def _short(value: str, keep: int = 12) -> str:
    return value[:keep] if len(value) > keep else value


def _bulleted(items: Sequence[str]) -> str:
    return "\n".join(f"• {item}" for item in items)


class ReviewView(QWidget):
    def __init__(self, viewmodel: ReviewViewModel) -> None:
        super().__init__()
        self._vm = viewmodel
        self._profiles: list[ExportProfile] = []

        # --- top bar ------------------------------------------------------------------------
        self.anchor = QComboBox()
        self.anchor.setAccessibleName(strings.ANCHOR_LABEL)
        self.anchor.setMinimumContentsLength(28)
        self.anchor.setPlaceholderText(strings.ANCHOR_PLACEHOLDER)
        self.reviewer = QLineEdit()
        self.reviewer.setAccessibleName(strings.REVIEWER_LABEL)
        self.reviewer.setPlaceholderText(strings.REVIEWER_PLACEHOLDER)
        self.refresh_button = QPushButton(strings.BUTTON_REFRESH)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel(strings.ANCHOR_LABEL))
        bar.addWidget(self.anchor, 2)
        bar.addSpacing(12)
        bar.addWidget(QLabel(strings.REVIEWER_LABEL))
        bar.addWidget(self.reviewer, 1)
        bar.addWidget(self.refresh_button)
        self.error = label("", "alert", wrap=True)
        self.error.setAccessibleName("Review error")
        self.error.hide()

        # --- sessions -----------------------------------------------------------------------
        self.sessions = QListWidget()
        self.sessions.setAccessibleName(strings.SESSIONS_LABEL)
        self.sessions.setWordWrap(True)
        self.sessions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sessions_empty = label(strings.ANCHOR_PLACEHOLDER, "empty", wrap=True)
        self.sessions_stack = QStackedWidget()
        self.sessions_stack.addWidget(self.sessions_empty)
        self.sessions_stack.addWidget(self.sessions)
        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(label(strings.SESSIONS_LABEL, "heading"))
        left_layout.addWidget(self.sessions_stack, 1)

        # --- detail tabs --------------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._summary_tab(), strings.TAB_SUMMARY)
        self.tabs.addTab(self._sections_tab(), strings.TAB_SECTIONS)
        self.tabs.addTab(self._proposals_tab(), strings.TAB_PROPOSALS)
        self.approvals = table(
            [
                strings.COL_KIND_SHORT,
                strings.COL_SUBJECT,
                strings.COL_VERDICT,
                strings.COL_ACTOR,
                strings.COL_CURRENT,
                strings.COL_WHEN,
            ],
            strings.APPROVAL_TABLE,
        )
        self.tabs.addTab(self.approvals, strings.TAB_APPROVALS)
        self.tabs.addTab(self._feedback_tab(), strings.TAB_FEEDBACK)
        self.history = table(
            [strings.COL_WHEN, strings.COL_ACTOR, strings.COL_KIND_SHORT, strings.COL_SUMMARY],
            strings.HISTORY_TABLE,
        )
        self.tabs.addTab(self.history, strings.TAB_HISTORY)
        self.tabs.addTab(self._export_tab(), strings.TAB_EXPORT)
        self.detail_empty = label(strings.NEED_SESSION, "empty", wrap=True)
        self.detail_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QStackedWidget()
        self.detail.addWidget(self.detail_empty)
        self.detail.addWidget(self.tabs)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(page_header(strings.REVIEW_TITLE, strings.REVIEW_SUBTITLE))
        layout.addLayout(bar)
        layout.addWidget(self.error)
        layout.addWidget(splitter, 1)

        # view -> view model
        self.anchor.currentIndexChanged.connect(self._anchor_chosen)
        self.reviewer.textChanged.connect(self._vm.set_reviewer)
        self.refresh_button.clicked.connect(self._refresh)
        self.sessions.currentItemChanged.connect(self._session_chosen)
        self.section_table.itemSelectionChanged.connect(self._update_actions)
        self.proposals.currentItemChanged.connect(lambda *_: self._update_actions())
        self.lock_button.clicked.connect(self._lock)
        self.unlock_button.clicked.connect(self._unlock)
        self.approve_button.clicked.connect(lambda: self._decide(Verdict.APPROVED))
        self.reject_button.clicked.connect(lambda: self._decide(Verdict.REJECTED))
        self.modify_button.clicked.connect(lambda: self._decide(Verdict.MODIFIED))
        self.feedback_button.clicked.connect(self._add_feedback)
        self.feedback_text.textChanged.connect(self._update_actions)
        self.authorize_button.clicked.connect(self._authorize)
        # view model -> view
        self._vm.anchors_changed.connect(self._show_anchors)
        self._vm.profiles_changed.connect(self._show_profiles)
        self._vm.sessions_changed.connect(self._show_sessions)
        self._vm.snapshot_changed.connect(self._show_snapshot)
        self._vm.error_changed.connect(self._show_error)
        self._vm.can_act_changed.connect(lambda _: self._update_actions())
        self._vm.busy_changed.connect(lambda busy: self.refresh_button.setEnabled(not busy))
        self._vm.confirm_export_requested.connect(self._confirm_export)
        self._vm.feedback_accepted.connect(self.feedback_text.clear)
        self._update_actions()

    # --- tab builders -------------------------------------------------------------------------
    def _summary_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(16)
        self.summary: dict[str, QLabel] = {}
        for key in (
            strings.SUMMARY_STATE,
            strings.SUMMARY_REVIEW_VERSION,
            strings.SUMMARY_TIMELINE,
            strings.SUMMARY_REVIEWER,
            strings.SUMMARY_EXPORT,
            strings.SUMMARY_CHECKPOINTS,
            strings.SUMMARY_ATTACHED,
        ):
            value = label("", wrap=True)
            value.setAccessibleName(key)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.summary[key] = value
            form.addRow(key, value)
        return page

    def _sections_tab(self) -> QWidget:
        self.section_table = table(
            [strings.COL_SECTION, strings.COL_STATUS, strings.COL_DECIDED_BY, strings.COL_DRIFT],
            strings.SECTION_TABLE,
        )
        self.lock_note = QLineEdit()
        self.lock_note.setPlaceholderText(strings.LOCK_NOTE_PLACEHOLDER)
        self.lock_note.setAccessibleName(strings.LOCK_NOTE_PLACEHOLDER)
        self.lock_button = QPushButton(strings.BUTTON_LOCK)
        self.unlock_button = QPushButton(strings.BUTTON_UNLOCK)
        actions = QHBoxLayout()
        actions.addWidget(self.lock_note, 1)
        actions.addWidget(self.lock_button)
        actions.addWidget(self.unlock_button)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.section_table, 1)
        layout.addLayout(actions)
        return page

    def _proposals_tab(self) -> QWidget:
        self.proposals = QTreeWidget()
        self.proposals.setAccessibleName(strings.PROPOSAL_TREE)
        self.proposals.setHeaderLabels(
            [
                strings.COL_PROPOSAL,
                strings.COL_STATUS,
                strings.COL_LEVEL,
                strings.COL_OWNER,
                strings.COL_CONFLICTS,
            ]
        )
        self.proposals.setUniformRowHeights(True)
        self.proposals.header().setStretchLastSection(False)
        self.proposals.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.proposals_note = label("", "muted", wrap=True)
        self.proposal_detail = label("", wrap=True)
        self.approve_button = QPushButton(strings.BUTTON_APPROVE)
        self.reject_button = QPushButton(strings.BUTTON_REJECT)
        self.modify_button = QPushButton(strings.BUTTON_MODIFY)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.approve_button)
        actions.addWidget(self.modify_button)
        actions.addWidget(self.reject_button)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.proposals_note)
        layout.addWidget(self.proposals, 1)
        layout.addWidget(self.proposal_detail)
        layout.addLayout(actions)
        return page

    def _feedback_tab(self) -> QWidget:
        self.feedback = QListWidget()
        self.feedback.setAccessibleName(strings.FEEDBACK_LIST)
        self.feedback.setWordWrap(True)
        self.feedback_section = QComboBox()
        self.feedback_section.setAccessibleName(strings.COL_SECTION)
        self.feedback_text = QPlainTextEdit()
        self.feedback_text.setAccessibleName(strings.FEEDBACK_LABEL)
        self.feedback_text.setPlaceholderText(strings.FEEDBACK_PLACEHOLDER)
        self.feedback_text.setMaximumHeight(90)
        self.feedback_button = QPushButton(strings.BUTTON_ADD_FEEDBACK)
        self.feedback_button.setProperty("role", "primary")
        actions = QHBoxLayout()
        actions.addWidget(self.feedback_section)
        actions.addStretch(1)
        actions.addWidget(self.feedback_button)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.feedback, 1)
        layout.addWidget(label(strings.FEEDBACK_LABEL, "heading"))
        layout.addWidget(self.feedback_text)
        layout.addLayout(actions)
        return page

    def _export_tab(self) -> QWidget:
        self.profile = QComboBox()
        self.profile.setAccessibleName(strings.EXPORT_PROFILE)
        self.authorize_button = QPushButton(strings.BUTTON_AUTHORIZE)
        self.authorize_button.setProperty("role", "primary")
        self.authorizations = QListWidget()
        self.authorizations.setAccessibleName(strings.EXPORT_AUTHORIZATIONS)
        row = QHBoxLayout()
        row.addWidget(QLabel(strings.EXPORT_PROFILE))
        row.addWidget(self.profile, 1)
        row.addWidget(self.authorize_button)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(strings.EXPORT_NOTE, "note", wrap=True))
        layout.addLayout(row)
        layout.addWidget(label(strings.EXPORT_AUTHORIZATIONS, "heading"))
        layout.addWidget(self.authorizations, 1)
        return page

    # --- person's questions (overridable in tests) ---------------------------------------------
    def ask(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def prompt(self, title: str, text: str) -> tuple[str, bool]:
        return QInputDialog.getMultiLineText(self, title, text)

    # --- intents --------------------------------------------------------------------------------
    def _refresh(self) -> None:
        self._vm.load()
        self._anchor_chosen()

    def _anchor_chosen(self, *_: object) -> None:
        self._vm.select_anchor(str(self.anchor.currentData() or ""))

    def _session_chosen(self, item: QListWidgetItem | None, _previous: object) -> None:
        session_id = item.data(ID_ROLE) if item is not None else ""
        if session_id != self._vm.session_id:
            self._vm.select_session(str(session_id or ""))

    def _selected_section(self) -> tuple[str, str] | None:
        row = self.section_table.currentRow()
        item = self.section_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        return str(item.data(ID_ROLE)), str(item.data(STATUS_ROLE))

    def _selected_proposal(self) -> tuple[str, str, bool] | None:
        item = self.proposals.currentItem()
        if item is None or item.data(0, ID_ROLE) is None:
            return None
        return (
            str(item.data(0, PLAN_ROLE)),
            str(item.data(0, ID_ROLE)),
            bool(item.data(0, OPEN_ROLE)),
        )

    def _lock(self) -> None:
        selected = self._selected_section()
        if selected is not None:
            self._vm.lock_section(selected[0], self.lock_note.text().strip())

    def _unlock(self) -> None:
        selected = self._selected_section()
        if selected is None:
            return
        reason, accepted = self.prompt(strings.UNLOCK_TITLE, strings.UNLOCK_PROMPT)
        if accepted:
            self._vm.unlock_section(selected[0], reason)

    def _decide(self, verdict: Verdict) -> None:
        selected = self._selected_proposal()
        if selected is None:
            return
        plan_id, proposal_id, _ = selected
        note = ""
        if verdict is Verdict.REJECTED:
            note, accepted = self.prompt(strings.REJECT_TITLE, strings.REJECT_PROMPT)
            if not accepted:
                return
        elif verdict is Verdict.MODIFIED:
            note, accepted = self.prompt(strings.MODIFY_TITLE, strings.MODIFY_PROMPT)
            if not accepted:
                return
        self._vm.decide(plan_id, proposal_id, verdict, note)

    def _add_feedback(self) -> None:
        section = self.feedback_section.currentData()
        self._vm.add_feedback(self.feedback_text.toPlainText(), str(section) if section else None)

    def _authorize(self) -> None:
        profile_id = self.profile.currentData()
        if profile_id:
            self._vm.request_export_authorization(str(profile_id))

    def _confirm_export(self, report: ReadinessReport) -> None:
        names = {p.profile_id: p.name for p in self._profiles}
        ref = report.timeline
        soft = report.readiness.soft
        text = strings.CONFIRM_TEXT.format(
            timeline_id=ref.timeline_id,
            version=ref.version,
            hash=ref.content_hash,
            profile=f"{names.get(report.profile_id, report.profile_id)} ({report.profile_id})",
            config=report.output_config_hash,
            soft=strings.CONFIRM_SOFT.format(items=_bulleted([b.message for b in soft]))
            if soft
            else "",
            warnings=strings.CONFIRM_WARNINGS.format(items=_bulleted(report.readiness.warnings))
            if report.readiness.warnings
            else "",
            reviewer=self._vm.reviewer,
        )
        if self.ask(strings.CONFIRM_TITLE, text):
            self._vm.authorize_export(report)

    # --- state -> widgets -----------------------------------------------------------------------
    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def _show_anchors(self, anchors: Sequence[AnchorAsset]) -> None:
        current = self.anchor.currentData()
        self.anchor.blockSignals(True)
        self.anchor.clear()
        for a in anchors:
            self.anchor.addItem(f"{a.name} ({a.media_type})", a.asset_id)
        index = self.anchor.findData(current) if current else -1
        self.anchor.setCurrentIndex(index)
        self.anchor.blockSignals(False)
        self.sessions_empty.setText(strings.ANCHOR_PLACEHOLDER if anchors else strings.ANCHOR_EMPTY)
        self.anchor.setEnabled(bool(anchors))

    def _show_profiles(self, profiles: Sequence[ExportProfile]) -> None:
        self._profiles = list(profiles)
        self.profile.clear()
        for p in profiles:
            self.profile.addItem(f"{p.name} · {p.purpose}", p.profile_id)
        self._update_actions()

    def _show_sessions(self, sessions: Sequence[ReviewSession]) -> None:
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for s in sessions:
            item = QListWidgetItem(
                strings.SESSION_ITEM.format(
                    state=s.state.value.replace("_", " "),
                    version=s.timeline.version,
                    updated=s.updated_at,
                )
            )
            item.setData(ID_ROLE, s.session_id)
            item.setToolTip(s.session_id)
            self.sessions.addItem(item)
            if s.session_id == self._vm.session_id:
                self.sessions.setCurrentItem(item)
        self.sessions.blockSignals(False)
        if not sessions and self.anchor.currentData():
            self.sessions_empty.setText(strings.SESSIONS_EMPTY)
        self.sessions_stack.setCurrentIndex(1 if sessions else 0)

    def _show_snapshot(self, snapshot: ReviewSnapshot | None) -> None:
        if snapshot is None:
            self.detail.setCurrentWidget(self.detail_empty)
            self._update_actions()
            return
        self.detail.setCurrentWidget(self.tabs)
        s = snapshot.session
        self._show_summary(s)
        names = {sec.section_id: sec.name for sec in s.sections}
        fill(
            self.section_table,
            [
                (
                    sec.name or sec.section_id,
                    sec.status.value.replace("_", " "),
                    sec.decided_by or "—",
                    strings.YES if sec.drift else strings.NO,
                )
                for sec in s.sections
            ],
        )
        for row, sec in enumerate(s.sections):
            item = self.section_table.item(row, 0)
            if item is not None:
                item.setData(ID_ROLE, sec.section_id)
                item.setData(STATUS_ROLE, sec.status.value)
        self._show_proposals(snapshot)
        fill(
            self.approvals,
            [
                (
                    a.kind.value,
                    a.subject_id,
                    a.verdict.value,
                    f"{a.actor} ({a.actor_kind.value})",
                    strings.YES if s.is_current(a) else strings.NO,
                    f"{a.decided_at:%Y-%m-%d %H:%M}",
                )
                for a in s.approvals
            ],
        )
        self.feedback.clear()
        for f in s.feedback:
            section = f" · {names.get(f.section_id, f.section_id)}" if f.section_id else ""
            handled = strings.FEEDBACK_HANDLED.format(plan=f.handled_by) if f.handled_by else ""
            self.feedback.addItem(
                strings.FEEDBACK_ITEM.format(
                    author=f.author,
                    version=f.timeline_version,
                    section=section,
                    text=f.text,
                    handled=handled,
                )
            )
        if not s.feedback:
            self.feedback.addItem(strings.FEEDBACK_EMPTY)
        self.feedback_section.clear()
        self.feedback_section.addItem(strings.FEEDBACK_SECTION_ANY, None)
        for sec in s.sections:
            self.feedback_section.addItem(sec.name or sec.section_id, sec.section_id)
        fill(
            self.history,
            [
                (f"{e.at:%Y-%m-%d %H:%M}", f"{e.actor} ({e.actor_kind.value})", e.kind, e.summary)
                for e in reversed(snapshot.history)
            ],
        )
        self.authorizations.clear()
        for auth in s.authorizations:
            state = (
                strings.EXPORT_AUTH_VALID
                if auth.valid
                else strings.EXPORT_AUTH_INVALID.format(reason=auth.invalidated_reason)
            )
            self.authorizations.addItem(
                strings.EXPORT_AUTH_ITEM.format(
                    profile=auth.export_profile_id,
                    version=auth.timeline.version,
                    by=auth.authorized_by,
                    state=state,
                )
            )
        self._update_actions()

    def _show_summary(self, s: ReviewSession) -> None:
        attached = [
            *([f"quality report {s.quality_report_id}"] if s.quality_report_id else []),
            *([f"final polish report {s.polish_report_id}"] if s.polish_report_id else []),
            *(f"revision plan {p}" for p in s.revision_plan_ids),
            *(f"render report {r}" for r in s.render_report_ids),
            *([f"export validation {s.export_validation_id}"] if s.export_validation_id else []),
        ]
        values = {
            strings.SUMMARY_STATE: s.state.value.replace("_", " "),
            strings.SUMMARY_REVIEW_VERSION: str(s.review_version),
            strings.SUMMARY_TIMELINE: strings.TIMELINE_TEXT.format(
                timeline_id=s.timeline.timeline_id,
                version=s.timeline.version,
                hash=_short(s.timeline.content_hash),
            ),
            strings.SUMMARY_REVIEWER: s.reviewer or "—",
            strings.SUMMARY_EXPORT: s.export_status.value.replace("_", " "),
            strings.SUMMARY_CHECKPOINTS: str(len(s.checkpoints)),
            strings.SUMMARY_ATTACHED: "\n".join(attached) or strings.ATTACHED_NONE,
        }
        for key, value in values.items():
            self.summary[key].setText(value)

    def _show_proposals(self, snapshot: ReviewSnapshot) -> None:
        self.proposals.clear()
        for plan in snapshot.plans:
            top = QTreeWidgetItem(
                [
                    f"Plan {plan.revision_plan_id} (v{plan.plan_version})",
                    plan.approval_status,
                    plan.approval_requirement.value,
                    ", ".join(plan.responsible_modules),
                    "",
                ]
            )
            for change in plan.proposed_changes:
                child = QTreeWidgetItem(
                    [
                        change.title,
                        change.status.value,
                        change.level.value,
                        change.owner_module,
                        "; ".join(change.conflicts),
                    ]
                )
                child.setToolTip(0, change.description)
                child.setData(0, ID_ROLE, change.proposal_id)
                child.setData(0, PLAN_ROLE, plan.revision_plan_id)
                child.setData(0, OPEN_ROLE, change.status in OPEN_PROPOSALS)
                top.addChild(child)
            self.proposals.addTopLevelItem(top)
            top.setExpanded(True)
        for column in range(1, self.proposals.columnCount()):
            self.proposals.resizeColumnToContents(column)
        if snapshot.plans_error:
            note = strings.PROPOSALS_ERROR.format(message=snapshot.plans_error)
        else:
            note = "" if snapshot.plans else strings.PROPOSALS_EMPTY
        self.proposals_note.setText(note)
        self.proposals_note.setVisible(bool(note))
        self.proposal_detail.clear()

    def _update_actions(self) -> None:
        can_act = self._vm.can_act
        reason = "" if self._vm.reviewer else strings.NEED_REVIEWER
        section = self._selected_section()
        locked = section is not None and section[1] == SectionStatus.LOCKED.value
        self.lock_button.setEnabled(can_act and section is not None and not locked)
        self.unlock_button.setEnabled(can_act and locked)
        proposal = self._selected_proposal()
        is_open = proposal is not None and proposal[2]
        for button in (self.approve_button, self.reject_button, self.modify_button):
            button.setEnabled(can_act and is_open)
            button.setToolTip(
                reason or ("" if is_open or proposal is None else strings.PROPOSAL_NOT_OPEN)
            )
        item = self.proposals.currentItem()
        self.proposal_detail.setText(item.toolTip(0) if item is not None else "")
        self.feedback_button.setEnabled(can_act and bool(self.feedback_text.toPlainText().strip()))
        self.authorize_button.setEnabled(can_act and self.profile.count() > 0)
        self.authorize_button.setToolTip(
            reason or ("" if self.profile.count() else strings.EXPORT_NO_PROFILES)
        )
        for button in (self.lock_button, self.unlock_button, self.feedback_button):
            button.setToolTip(reason)
