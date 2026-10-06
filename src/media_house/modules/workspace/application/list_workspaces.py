"""Query: list workspaces. Also implements the module's public ``WorkspaceCatalog`` contract."""

from collections.abc import Sequence

from media_house.modules.workspace.application.create_workspace import to_dto
from media_house.modules.workspace.application.dto import WorkspaceDto
from media_house.modules.workspace.domain.repository import WorkspaceRepository


class ListWorkspaces:
    def __init__(self, repository: WorkspaceRepository) -> None:
        self._repository = repository

    def list_workspaces(self) -> Sequence[WorkspaceDto]:
        return [to_dto(workspace) for workspace in self._repository.list_all()]
