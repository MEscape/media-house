"""Presentation state + intent handling for the Workspace page.

Talks to the application layer through use cases, runs them off the UI thread via
``UiJobRunner`` and exposes results as Qt signals. Contains no widgets.
"""

from PySide6.QtCore import QObject, Signal

from media_house.modules.workspace.application.commands import CreateWorkspaceCommand
from media_house.modules.workspace.application.create_workspace import CreateWorkspace
from media_house.modules.workspace.application.dto import VerificationSummary, WorkspaceDto
from media_house.modules.workspace.application.list_workspaces import ListWorkspaces
from media_house.modules.workspace.application.verify_workspaces import VerifyWorkspaces
from media_house.modules.workspace.presentation import strings
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.concurrency import JobHandle, JobProgress
from media_house.shared.errors import ConflictError, Err, Ok, Result, ValidationError


class WorkspaceViewModel(QObject):
    workspaces_changed = Signal(object)  # Sequence[WorkspaceDto]
    busy_changed = Signal(bool)
    verifying_changed = Signal(bool)
    verification_progress = Signal(int, int, str)  # current, total (0 = unknown), message
    form_error_changed = Signal(str)  # empty string clears the error
    name_accepted = Signal()  # the view may clear its input

    def __init__(
        self,
        create: CreateWorkspace,
        catalog: ListWorkspaces,
        verify: VerifyWorkspaces,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._create = create
        self._catalog = catalog
        self._verify = verify
        self._jobs = jobs
        self._status = status
        self._pending = 0
        self._verification: JobHandle[VerificationSummary] | None = None

    @property
    def is_verifying(self) -> bool:
        return self._verification is not None

    # -- intents from the view ------------------------------------------- #
    def refresh(self) -> None:
        self._begin()
        self._jobs.submit(
            "Load workspaces",
            lambda _ctx: self._catalog.list_workspaces(),
            on_success=self.workspaces_changed.emit,
            on_finished=self._end,
        )

    def create_workspace(self, name: str) -> None:
        self.form_error_changed.emit("")
        self._begin()
        self._jobs.submit(
            "Create workspace",
            lambda _ctx: self._create.execute(CreateWorkspaceCommand(name)),
            on_success=self._on_created,
            on_finished=self._end,
        )

    def verify_workspaces(self) -> None:
        if self._verification is not None:
            return
        self.verifying_changed.emit(True)
        self._verification = self._jobs.submit(
            "Verify workspaces",
            self._verify.execute,
            on_progress=self._on_progress,
            on_success=self._on_verified,
            on_cancelled=lambda: self._status.show(strings.STATUS_VERIFY_CANCELLED),
            on_finished=self._on_verification_finished,
        )

    def cancel_verification(self) -> None:
        if self._verification is not None:
            self._verification.cancel()

    # -- job results (always on the UI thread) ----------------------------- #
    def _on_created(self, result: Result[WorkspaceDto, ValidationError | ConflictError]) -> None:
        match result:
            case Ok(dto):
                self.name_accepted.emit()
                self._status.show(strings.STATUS_CREATED.format(name=dto.name))
                self.refresh()
            case Err(error):
                self.form_error_changed.emit(error.user_message)

    def _on_progress(self, progress: JobProgress) -> None:
        self.verification_progress.emit(progress.current, progress.total or 0, progress.message)

    def _on_verified(self, summary: VerificationSummary) -> None:
        self._status.show(
            strings.STATUS_VERIFIED.format(checked=summary.checked, problems=len(summary.problems)),
        )

    def _on_verification_finished(self) -> None:
        self._verification = None
        self.verifying_changed.emit(False)

    # -- busy bookkeeping --------------------------------------------------- #
    def _begin(self) -> None:
        self._pending += 1
        if self._pending == 1:
            self.busy_changed.emit(True)

    def _end(self) -> None:
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy_changed.emit(False)
