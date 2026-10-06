"""Use case: create a workspace."""

from media_house.core.application.ports import Clock
from media_house.modules.workspace.application.commands import CreateWorkspaceCommand
from media_house.modules.workspace.application.dto import WorkspaceDto
from media_house.modules.workspace.domain.repository import WorkspaceNameTaken, WorkspaceRepository
from media_house.modules.workspace.domain.values import WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.shared.errors import ConflictError, DomainError, Err, Ok, Result, ValidationError
from media_house.shared.events import EventPublisher


class CreateWorkspace:
    """Expected failures (bad name, duplicate) are returned as ``Err``.

    Unexpected failures (e.g. the database is unreachable) propagate as exceptions
    to the error boundary.
    """

    def __init__(
        self,
        repository: WorkspaceRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._events = events

    def execute(
        self,
        command: CreateWorkspaceCommand,
    ) -> Result[WorkspaceDto, ValidationError | ConflictError]:
        try:
            name = WorkspaceName.of(command.name)
        except DomainError as exc:
            return Err(ValidationError(str(exc), field="name", user_message=exc.user_message))

        conflict = ConflictError(
            f"Workspace name already in use: {name}",
            user_message=f"A workspace named '{name}' already exists.",
        )
        if self._repository.name_exists(name):
            return Err(conflict)

        workspace = Workspace.create(name, now=self._clock.now())
        try:
            self._repository.add(workspace)
        except WorkspaceNameTaken:  # lost a race with another writer
            return Err(conflict)

        for event in workspace.pull_events():
            self._events.publish(event)
        return Ok(to_dto(workspace))


def to_dto(workspace: Workspace) -> WorkspaceDto:
    return WorkspaceDto(
        id=workspace.id.value,
        name=workspace.name.value,
        created_at=workspace.created_at,
    )
