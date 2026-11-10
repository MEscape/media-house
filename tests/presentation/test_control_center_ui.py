"""Control Center pages: view models and passive views against fakes of the other contracts."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from media_house.modules.control_center.application.resolve_status import CheckResolveStatus
from media_house.modules.control_center.application.sound_library import SoundLibraryService
from media_house.modules.control_center.presentation.contributor import (
    ControlCenterUiContributor,
)
from media_house.modules.control_center.presentation.viewmodels.pipeline_viewmodel import (
    PipelineViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.resolve_viewmodel import (
    ResolveViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.review_viewmodel import (
    ReviewViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.sound_library_viewmodel import (
    SoundLibraryViewModel,
)
from media_house.modules.control_center.presentation.views.pipeline_view import (
    MODULE_ROLE,
    PipelineView,
)
from media_house.modules.control_center.presentation.views.resolve_view import ResolveView
from media_house.modules.control_center.presentation.views.review_view import ReviewView
from media_house.modules.control_center.presentation.views.sound_library_view import (
    PLANNER_COLUMN,
    SoundLibraryView,
)
from media_house.modules.review_orchestrator.application.contracts import Verdict
from media_house.modules.revision_engine.application.contracts import ChangeStatus
from media_house.presentation.extension import UiRegistry
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.errors.handler import ErrorReport
from tests.support.fakes import ImmediateJobScheduler
from tests.unit.control_center.fakes import (
    FakeImporter,
    FakeInspector,
    FakeSoundCatalog,
    ReviewBackend,
    blocker,
    build_workspace,
    connected_inspection,
    plan,
    sound,
)

pytestmark = pytest.mark.presentation


@pytest.fixture
def jobs(dispatcher: UiDispatcher, unhandled_errors: list[ErrorReport]) -> UiJobRunner:
    return UiJobRunner(
        ImmediateJobScheduler(), dispatcher, on_unhandled_error=unhandled_errors.append
    )


@pytest.fixture
def status() -> StatusReporter:
    return StatusReporter()


def node(tree: QTreeWidget, top: int, child: int | None = None) -> QTreeWidgetItem:
    item = tree.topLevelItem(top)
    assert item is not None
    if child is not None:
        item = item.child(child)
        assert item is not None
    return item


def statuses(status: StatusReporter) -> list[str]:
    seen: list[str] = []
    status.message.connect(lambda text, _timeout: seen.append(text))
    return seen


class TestContributor:
    def test_contributes_four_pages_and_no_second_job_monitor(self, qtbot: QtBot) -> None:
        registry = UiRegistry()
        ControlCenterUiContributor(
            resolve=lambda: pytest.fail("built lazily"),
            sounds=lambda: pytest.fail("built lazily"),
            review=lambda: pytest.fail("built lazily"),
        ).contribute(registry)
        assert [v.id for v in registry.views] == [
            "control_center.pipeline",
            "control_center.resolve",
            "control_center.sounds",
            "control_center.review",
        ]
        assert registry.docks == ()


class TestPipelinePage:
    def test_layers_expand_to_modules_and_show_details(self, qtbot: QtBot) -> None:
        view = PipelineView(PipelineViewModel())
        qtbot.addWidget(view)
        tree = view.tree
        assert tree.topLevelItemCount() == 7
        assert sum(node(tree, i).childCount() for i in range(7)) == 35
        assert view.detail.currentWidget() is view.empty

        layer2 = node(tree, 1)
        content = next(
            node(tree, 1, i)
            for i in range(layer2.childCount())
            if node(tree, 1, i).data(0, MODULE_ROLE) == "content_intelligence"
        )
        tree.setCurrentItem(content)
        assert view.detail_title.text() == "Content Intelligence"
        assert view.validation_chip.text() == "Synthetic data only"
        assert view.validation_chip.property("status") == "warn"
        deps = view.dependencies.toPlainText()
        assert "audio_analysis (audio_intelligence)" in deps
        assert "not a fixed order" in deps
        assert "English and German" in view.limits.toPlainText()

        tree.setCurrentItem(layer2)  # a layer row clears the detail
        assert view.detail.currentWidget() is view.empty

    def test_resolve_modules_are_flagged(self, qtbot: QtBot) -> None:
        vm = PipelineViewModel()
        view = PipelineView(vm)
        qtbot.addWidget(view)
        vm.select("resolve_adapter")
        assert view.validation_chip.property("status") == "error"
        assert "real DaVinci Resolve" in view.validation_chip.text()


class TestResolvePage:
    def make(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter, inspector: FakeInspector
    ) -> ResolveView:
        view = ResolveView(ResolveViewModel(CheckResolveStatus(inspector), jobs, status))
        qtbot.addWidget(view)
        return view

    def test_nothing_is_checked_until_asked(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        inspector = FakeInspector()
        view = self.make(qtbot, jobs, status, inspector)
        assert inspector.calls == 0
        assert view.body.currentWidget() is view.empty

    def test_offline_check(self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter) -> None:
        view = self.make(qtbot, jobs, status, FakeInspector())
        view.test_button.click()
        assert view.connection_chip.text() == "not running"
        assert view.connection_chip.property("status") == "warn"
        assert view.fields["Running"].text() == "no"
        assert "nothing was checked" in view.capability_heading.text()
        assert view.test_button.isEnabled()

    def test_connected_check(self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter) -> None:
        seen = statuses(status)
        view = self.make(qtbot, jobs, status, FakeInspector(connected_inspection()))
        view.test_button.click()
        assert view.connection_chip.property("status") == "ok"
        assert view.fields["Version"].text() == "19.0.3.2"
        assert view.fields["Current project"].text() == "Demo"
        assert view.fields["Current timeline"].text() == "Not reported by the inspection contract."
        assert view.capabilities.rowCount() > 0
        assert seen == ["DaVinci Resolve: connected"]

    def test_failure_is_shown_on_the_page(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        class Broken(FakeInspector):
            def execute(self):  # type: ignore[no-untyped-def]
                raise OSError("fusionscript failed to load")

        view = self.make(qtbot, jobs, status, Broken())
        view.show()
        view.test_button.click()
        assert view.error.isVisible() and "The check failed" in view.error.text()
        assert view.test_button.isEnabled()


class TestSoundLibraryPage:
    def make(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter, catalog: FakeSoundCatalog
    ) -> tuple[SoundLibraryView, SoundLibraryViewModel]:
        vm = SoundLibraryViewModel(
            SoundLibraryService(catalog, FakeImporter(catalog=catalog)), jobs, status
        )
        view = SoundLibraryView(vm)
        qtbot.addWidget(view)
        return view, vm

    def test_empty_library(self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter) -> None:
        view, vm = self.make(qtbot, jobs, status, FakeSoundCatalog([]))
        vm.refresh()
        assert view.body.currentWidget() is view.empty
        assert "No sounds imported yet" in view.empty.text()

    def test_licence_state_and_filters(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        catalog = FakeSoundCatalog(
            [
                sound("a", "Bright Intro", commercial=True, attribution="Music by X"),
                sound("b", "Night Drive", commercial=None),
            ]
        )
        view, vm = self.make(qtbot, jobs, status, catalog)
        vm.refresh()
        assert view.table.rowCount() == 2
        planner = [view.table.item(r, PLANNER_COLUMN).text() for r in range(2)]  # type: ignore[union-attr]
        assert "Selectable" in planner[0]
        assert "Never selected (licence not cleared)" in planner[1]
        assert "1 selectable" in view.summary.text() and "1 never selected" in view.summary.text()

        view.table.setCurrentCell(0, 0)
        assert "Music by X" in view.detail.text()

        view.selectable.setChecked(True)
        assert view.table.rowCount() == 1
        view.search.setText("nothing like this")
        assert view.body.currentWidget() is view.empty
        assert view.empty.text() == "No sound matches the filters."

    def test_import_manifest_refreshes(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        seen = statuses(status)
        view, vm = self.make(qtbot, jobs, status, FakeSoundCatalog([]))
        vm.refresh()
        vm.import_manifest(str(Path("manifest.json")))
        assert view.table.rowCount() == 1
        assert seen[-1].startswith("Imported 1 sound(s)")


class TestReviewPage:
    def make(
        self,
        qtbot: QtBot,
        jobs: UiJobRunner,
        status: StatusReporter,
        backend: ReviewBackend | None = None,
    ) -> tuple[ReviewView, ReviewViewModel, ReviewBackend]:
        backend = backend or ReviewBackend()
        vm = ReviewViewModel(build_workspace(backend), jobs, status)
        view = ReviewView(vm)
        qtbot.addWidget(view)
        vm.load()
        return view, vm, backend

    def open_session(self, view: ReviewView) -> None:
        view.anchor.setCurrentIndex(0)
        view.sessions.setCurrentRow(0)

    def test_picker_then_session_detail(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        view, _vm, _ = self.make(qtbot, jobs, status)
        assert view.anchor.count() == 1 and view.anchor.currentIndex() == -1
        assert view.detail.currentWidget() is view.detail_empty
        self.open_session(view)
        assert view.detail.currentWidget() is view.tabs
        assert view.summary["State"].text() == "awaiting human review"
        assert "quality report qc-1" in view.summary["Attached reports and plans"].text()
        assert view.section_table.rowCount() == 2
        assert view.approvals.rowCount() == 1
        assert view.history.rowCount() == 1
        assert node(view.proposals, 0).childCount() == 1

    def test_actions_need_a_named_reviewer(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        view, _vm, backend = self.make(qtbot, jobs, status)
        self.open_session(view)
        view.section_table.setCurrentCell(0, 0)
        assert not view.lock_button.isEnabled()
        assert "Enter your name" in view.lock_button.toolTip()
        view.reviewer.setText("Bo")
        assert view.lock_button.isEnabled() and not view.unlock_button.isEnabled()
        view.lock_button.click()
        assert backend.calls[0][0] == "lock_section"

    def test_unlock_asks_for_a_reason(
        self,
        qtbot: QtBot,
        jobs: UiJobRunner,
        status: StatusReporter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        view, _vm, backend = self.make(qtbot, jobs, status)
        self.open_session(view)
        view.reviewer.setText("Bo")
        view.section_table.setCurrentCell(1, 0)  # "main" is locked
        assert view.unlock_button.isEnabled() and not view.lock_button.isEnabled()
        monkeypatch.setattr(view, "prompt", lambda *_: ("client asked", True))
        view.unlock_button.click()
        assert backend.calls[-1] == ("unlock_section", backend.calls[-1][1])
        assert backend.calls[-1][1][3] == "client asked"

    def test_feedback_and_proposal_decisions(
        self,
        qtbot: QtBot,
        jobs: UiJobRunner,
        status: StatusReporter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        view, _vm, backend = self.make(qtbot, jobs, status)
        self.open_session(view)
        view.reviewer.setText("Bo")
        assert not view.feedback_button.isEnabled()
        view.feedback_text.setPlainText("The intro drags.")
        view.feedback_button.click()
        assert backend.calls[0][0] == "add_feedback"
        assert view.feedback_text.toPlainText() == ""

        top = node(view.proposals, 0)
        view.proposals.setCurrentItem(top)
        assert not view.approve_button.isEnabled()  # a plan row is not a proposal
        view.proposals.setCurrentItem(node(view.proposals, 0, 0))
        assert view.approve_button.isEnabled()
        monkeypatch.setattr(view, "prompt", lambda *_: ("shorter trim", True))
        view.modify_button.click()
        command = backend.calls[-1][1][0]
        decision = command.decisions[0]  # type: ignore[attr-defined]
        assert decision.verdict is Verdict.MODIFIED and decision.note == "shorter trim"

    def test_decided_proposals_cannot_be_decided_again(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        backend = ReviewBackend(plans=[plan(status=ChangeStatus.EXECUTED)])
        view, _vm, _ = self.make(qtbot, jobs, status, backend)
        self.open_session(view)
        view.reviewer.setText("Bo")
        view.proposals.setCurrentItem(node(view.proposals, 0, 0))
        assert not view.approve_button.isEnabled()
        assert view.approve_button.toolTip() == "Only an open proposal can be decided."

    def test_export_authorization_needs_explicit_confirmation(
        self,
        qtbot: QtBot,
        jobs: UiJobRunner,
        status: StatusReporter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = ReviewBackend(soft=(blocker("loudness above target", hard=False),))
        view, _vm, _ = self.make(qtbot, jobs, status, backend)
        self.open_session(view)
        view.reviewer.setText("Bo")
        asked: list[str] = []
        answers = iter([False, True])

        def ask(_title: str, text: str) -> bool:
            asked.append(text)
            return next(answers)

        monkeypatch.setattr(view, "ask", ask)
        view.authorize_button.click()  # declined
        assert [c[0] for c in backend.calls] == ["readiness"]
        text = asked[0]
        assert "tl-1, version 3" in text and "cfg-hash" in text
        assert view.profile.currentData() in text
        assert "loudness above target" in text and "(Bo)" in text

        view.authorize_button.click()  # confirmed
        assert [c[0] for c in backend.calls] == ["readiness", "readiness", "authorize"]

    def test_hard_blockers_stop_before_the_dialog(
        self,
        qtbot: QtBot,
        jobs: UiJobRunner,
        status: StatusReporter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = ReviewBackend(hard=(blocker("no quality report", hard=True),))
        view, _vm, _ = self.make(qtbot, jobs, status, backend)
        view.show()
        self.open_session(view)
        view.reviewer.setText("Bo")
        monkeypatch.setattr(view, "ask", lambda *_: pytest.fail("must not ask"))
        view.authorize_button.click()
        assert view.error.isVisible() and "no quality report" in view.error.text()
        assert [c[0] for c in backend.calls] == ["readiness"]

    def test_buttons_have_text(
        self, qtbot: QtBot, jobs: UiJobRunner, status: StatusReporter
    ) -> None:
        view, _vm, _ = self.make(qtbot, jobs, status)
        assert all(b.text() for b in view.findChildren(QPushButton))
