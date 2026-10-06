from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QListView, QProgressBar, QPushButton
from pytestqt.qtbot import QtBot

from media_house.modules.workspace.application.commands import CreateWorkspaceCommand
from media_house.modules.workspace.application.create_workspace import CreateWorkspace
from media_house.modules.workspace.application.dto import WorkspaceDto
from media_house.modules.workspace.application.list_workspaces import ListWorkspaces
from media_house.modules.workspace.application.verify_workspaces import VerifyWorkspaces
from media_house.modules.workspace.presentation.viewmodels.workspace_viewmodel import (
    WorkspaceViewModel,
)
from media_house.modules.workspace.presentation.views.workspace_view import WorkspaceView
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.concurrency import ThreadPoolJobScheduler
from media_house.shared.errors import Ok
from media_house.shared.errors.handler import ErrorHandler, ErrorReport
from tests.support.fakes import (
    FixedClock,
    ImmediateJobScheduler,
    InMemoryWorkspaceRepository,
    RecordingPublisher,
)

pytestmark = pytest.mark.presentation


@dataclass
class Stack:
    viewmodel: WorkspaceViewModel
    repository: InMemoryWorkspaceRepository
    create: CreateWorkspace
    statuses: list[str]
    errors: list[ErrorReport]


def build(
    dispatcher: UiDispatcher,
    scheduler: ImmediateJobScheduler | ThreadPoolJobScheduler,
    *,
    step_delay: float = 0.0,
) -> Stack:
    repository, clock, events = InMemoryWorkspaceRepository(), FixedClock(), RecordingPublisher()
    errors: list[ErrorReport] = []
    status = StatusReporter()
    statuses: list[str] = []
    status.message.connect(lambda text, _timeout: statuses.append(text))
    create = CreateWorkspace(repository, clock, events)
    viewmodel = WorkspaceViewModel(
        create,
        ListWorkspaces(repository),
        VerifyWorkspaces(repository, clock, events, step_delay_seconds=step_delay),
        UiJobRunner(scheduler, dispatcher, on_unhandled_error=errors.append),
        status,
    )
    return Stack(viewmodel, repository, create, statuses, errors)


@pytest.fixture
def sync_stack(dispatcher: UiDispatcher) -> Stack:
    return build(dispatcher, ImmediateJobScheduler())


@pytest.fixture
def threaded_stack(dispatcher: UiDispatcher) -> Iterator[Stack]:
    scheduler = ThreadPoolJobScheduler(max_workers=2, error_handler=ErrorHandler())
    yield build(dispatcher, scheduler, step_delay=0.05)
    scheduler.shutdown(timeout=5)


class TestViewModel:
    def test_create_success_clears_input_refreshes_list_and_reports_status(
        self,
        qtbot: QtBot,
        sync_stack: Stack,
    ) -> None:
        vm = sync_stack.viewmodel
        listed: list[list[WorkspaceDto]] = []
        vm.workspaces_changed.connect(lambda items: listed.append(list(items)))
        with qtbot.waitSignal(vm.name_accepted, timeout=3000):
            vm.create_workspace("Docs")
        qtbot.waitUntil(lambda: bool(listed), timeout=3000)
        assert [w.name for w in listed[-1]] == ["Docs"]
        assert sync_stack.statuses == ["Workspace 'Docs' created"]

    def test_expected_failures_become_form_errors_not_dialogs(self, sync_stack: Stack) -> None:
        errors: list[str] = []
        sync_stack.viewmodel.form_error_changed.connect(errors.append)
        sync_stack.viewmodel.create_workspace("   ")
        assert errors[-1] == "Please enter a workspace name."
        assert sync_stack.errors == []

    def test_duplicate_name_is_a_form_error(self, sync_stack: Stack) -> None:
        assert isinstance(sync_stack.create.execute(CreateWorkspaceCommand("Docs")), Ok)
        errors: list[str] = []
        sync_stack.viewmodel.form_error_changed.connect(errors.append)
        sync_stack.viewmodel.create_workspace("docs")
        assert "already exists" in errors[-1]

    def test_busy_flag_tracks_in_flight_work(self, sync_stack: Stack) -> None:
        states: list[bool] = []
        sync_stack.viewmodel.busy_changed.connect(states.append)
        sync_stack.viewmodel.refresh()
        assert states == [True, False]

    def test_unexpected_failures_go_to_the_error_presenter_and_busy_resets(
        self,
        sync_stack: Stack,
    ) -> None:
        class Broken(InMemoryWorkspaceRepository):
            def list_all(self):  # type: ignore[no-untyped-def]
                raise OSError("disk gone")

        sync_stack.viewmodel._catalog = ListWorkspaces(Broken())
        states: list[bool] = []
        sync_stack.viewmodel.busy_changed.connect(states.append)
        sync_stack.viewmodel.refresh()
        assert len(sync_stack.errors) == 1
        assert states == [True, False]

    def test_verification_reports_progress_and_completion(
        self,
        qtbot: QtBot,
        threaded_stack: Stack,
    ) -> None:
        for name in ("a", "b", "c"):
            threaded_stack.create.execute(CreateWorkspaceCommand(name))
        vm = threaded_stack.viewmodel
        progress: list[tuple[int, int, str]] = []
        vm.verification_progress.connect(lambda *args: progress.append(args))
        vm.verify_workspaces()
        assert vm.is_verifying
        qtbot.waitUntil(lambda: not vm.is_verifying, timeout=5000)
        assert [(c, t) for c, t, _ in progress] == [(1, 3), (2, 3), (3, 3)]
        assert threaded_stack.statuses[-1] == "Verified 3 workspace(s): 0 problem(s)"

    def test_verification_can_be_cancelled(self, qtbot: QtBot, threaded_stack: Stack) -> None:
        for index in range(10):
            threaded_stack.create.execute(CreateWorkspaceCommand(f"w{index}"))
        vm = threaded_stack.viewmodel
        vm.verify_workspaces()
        vm.verify_workspaces()  # second call while running is ignored
        with qtbot.waitSignal(vm.verification_progress, timeout=3000):
            pass
        vm.cancel_verification()
        qtbot.waitUntil(lambda: not vm.is_verifying, timeout=5000)
        assert threaded_stack.statuses[-1] == "Verification cancelled"


class TestView:
    def test_user_flow_create_then_duplicate_error(self, qtbot: QtBot, sync_stack: Stack) -> None:
        view = WorkspaceView(sync_stack.viewmodel)
        qtbot.addWidget(view)
        view.show()
        name, create = view.findChild(QLineEdit), view.findChildren(QPushButton)[0]
        listing = view.findChild(QListView)
        assert name and listing

        name.setText("Docs")
        create.click()
        qtbot.waitUntil(lambda: listing.model().rowCount() == 1, timeout=3000)
        assert name.text() == ""

        name.setText("DOCS")
        name.returnPressed.emit()
        error = next(
            label for label in view.findChildren(QLabel) if label.accessibleName() == "Form error"
        )
        qtbot.waitUntil(error.isVisible, timeout=3000)
        assert "already exists" in error.text()
        assert listing.model().rowCount() == 1

    def test_verify_controls_follow_view_model_state(
        self, qtbot: QtBot, threaded_stack: Stack
    ) -> None:
        threaded_stack.create.execute(CreateWorkspaceCommand("a"))
        view = WorkspaceView(threaded_stack.viewmodel)
        qtbot.addWidget(view)
        view.show()
        _create, verify, cancel = view.findChildren(QPushButton)
        progress = view.findChild(QProgressBar)
        assert progress and not progress.isVisible()
        assert verify.isEnabled() and not cancel.isEnabled()

        verify.click()
        assert not verify.isEnabled() and cancel.isEnabled() and progress.isVisible()
        qtbot.waitUntil(verify.isEnabled, timeout=5000)
        assert not cancel.isEnabled() and not progress.isVisible()

    def test_widgets_have_accessible_names(self, qtbot: QtBot, sync_stack: Stack) -> None:
        view = WorkspaceView(sync_stack.viewmodel)
        qtbot.addWidget(view)
        assert view.findChild(QLineEdit).accessibleName() == "Workspace name"  # type: ignore[union-attr]
        assert view.findChild(QListView).accessibleName() == "Workspace list"  # type: ignore[union-attr]
