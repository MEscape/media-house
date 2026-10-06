"""Module registration: how the Workspace module plugs into the application.

This file is the module's own composition root. It is the only place in the
module that connects ports to adapters.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.workspace.application.contracts import WorkspaceCatalog
from media_house.modules.workspace.application.create_workspace import CreateWorkspace
from media_house.modules.workspace.application.list_workspaces import ListWorkspaces
from media_house.modules.workspace.application.verify_workspaces import VerifyWorkspaces
from media_house.modules.workspace.domain.repository import WorkspaceRepository
from media_house.modules.workspace.infrastructure.sqlite_repository import SqliteWorkspaceRepository
from media_house.modules.workspace.presentation.contributor import WorkspaceUiContributor
from media_house.modules.workspace.presentation.viewmodels.workspace_viewmodel import (
    WorkspaceViewModel,
)
from media_house.presentation.extension import UiContributor
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths

#: Demonstration pacing so progress/cancellation are visible in the UI.
_DEMO_STEP_DELAY_SECONDS = 0.25


class WorkspaceModule:
    name: str = "workspace"

    def register(self, container: Container) -> None:
        container.register_factory(
            WorkspaceRepository,
            lambda c: SqliteWorkspaceRepository(c.resolve(AppPaths).data_dir / "workspaces.db"),
        )
        container.register_factory(
            CreateWorkspace,
            lambda c: CreateWorkspace(
                c.resolve(WorkspaceRepository),
                c.resolve(Clock),
                c.resolve(EventPublisher),
            ),
        )
        container.register_factory(
            ListWorkspaces,
            lambda c: ListWorkspaces(c.resolve(WorkspaceRepository)),
        )
        # The public contract other modules may depend on:
        container.register_factory(WorkspaceCatalog, lambda c: c.resolve(ListWorkspaces))
        container.register_factory(
            VerifyWorkspaces,
            lambda c: VerifyWorkspaces(
                c.resolve(WorkspaceRepository),
                c.resolve(Clock),
                c.resolve(EventPublisher),
                step_delay_seconds=_DEMO_STEP_DELAY_SECONDS,
            ),
        )
        # UI: resolved lazily, after the desktop bootstrap registered UiJobRunner/StatusReporter.
        container.add_to_collection(
            UiContributor,
            lambda c: WorkspaceUiContributor(lambda: _build_viewmodel(c)),
        )


def _build_viewmodel(container: Container) -> WorkspaceViewModel:
    return WorkspaceViewModel(
        container.resolve(CreateWorkspace),
        container.resolve(ListWorkspaces),
        container.resolve(VerifyWorkspaces),
        container.resolve(UiJobRunner),
        container.resolve(StatusReporter),
    )
