"""Repository port. Implemented in ``infrastructure``; consumed by use cases."""

from collections.abc import Sequence
from typing import Protocol

from media_house.modules.workspace.domain.values import WorkspaceId, WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.shared.errors import DomainError


class WorkspaceNameTaken(DomainError):
    code = "workspace.name_taken"

    def __init__(self, name: WorkspaceName) -> None:
        super().__init__(
            f"Workspace name already in use: {name}",
            user_message=f"A workspace named '{name}' already exists.",
        )


class WorkspaceRepository(Protocol):
    def add(self, workspace: Workspace) -> None:
        """Persist a new workspace. Raises :class:`WorkspaceNameTaken` on duplicates."""
        ...

    def get(self, workspace_id: WorkspaceId) -> Workspace | None: ...

    def list_all(self) -> Sequence[Workspace]:
        """All workspaces, oldest first."""
        ...

    def name_exists(self, name: WorkspaceName) -> bool: ...
