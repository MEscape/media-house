"""The Workspace aggregate."""

from datetime import datetime

from media_house.core.domain import AggregateRoot
from media_house.modules.workspace.domain.events import WorkspaceCreated
from media_house.modules.workspace.domain.values import WorkspaceId, WorkspaceName


class Workspace(AggregateRoot):
    def __init__(self, *, id: WorkspaceId, name: WorkspaceName, created_at: datetime) -> None:  # noqa: A002
        super().__init__()
        self.id = id
        self.name = name
        self.created_at = created_at

    @classmethod
    def create(cls, name: WorkspaceName, *, now: datetime) -> "Workspace":
        """Business operation: start a new workspace. Records ``WorkspaceCreated``."""
        workspace = cls(id=WorkspaceId.new(), name=name, created_at=now)
        workspace._record(
            WorkspaceCreated(occurred_at=now, workspace_id=workspace.id.value, name=name.value),
        )
        return workspace

    def problems(self, *, now: datetime) -> list[str]:
        """Invariant check used by verification jobs. Empty list means healthy."""
        found: list[str] = []
        if self.created_at > now:
            found.append(f"'{self.name}' has a creation time in the future")
        return found
