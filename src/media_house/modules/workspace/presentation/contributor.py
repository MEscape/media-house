"""What the Workspace module adds to the shell: a page, a menu and shortcuts."""

from collections.abc import Callable

from media_house.modules.workspace.presentation import strings
from media_house.modules.workspace.presentation.viewmodels.workspace_viewmodel import (
    WorkspaceViewModel,
)
from media_house.modules.workspace.presentation.views.workspace_view import WorkspaceView
from media_house.presentation.extension import ActionContribution, UiRegistry, ViewContribution


class WorkspaceUiContributor:
    def __init__(self, viewmodel_factory: Callable[[], WorkspaceViewModel]) -> None:
        self._viewmodel_factory = viewmodel_factory
        self._viewmodel: WorkspaceViewModel | None = None

    def contribute(self, registry: UiRegistry) -> None:
        registry.add_view(ViewContribution("workspace.main", strings.NAV_TITLE, self._create_view))
        registry.add_action(
            ActionContribution(
                "workspace.refresh",
                strings.ACTION_REFRESH,
                strings.MENU,
                lambda: self._vm().refresh(),
                shortcut="F5",
            ),
        )
        registry.add_action(
            ActionContribution(
                "workspace.verify",
                strings.ACTION_VERIFY,
                strings.MENU,
                lambda: self._vm().verify_workspaces(),
                shortcut="Ctrl+Shift+V",
            ),
        )

    def _vm(self) -> WorkspaceViewModel:
        if self._viewmodel is None:
            self._viewmodel = self._viewmodel_factory()
        return self._viewmodel

    def _create_view(self) -> WorkspaceView:
        viewmodel = self._vm()
        view = WorkspaceView(viewmodel)
        viewmodel.refresh()
        return view
