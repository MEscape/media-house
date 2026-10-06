"""Domain events."""

from dataclasses import dataclass

from media_house.shared.events import DomainEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class WorkspaceCreated(DomainEvent):
    workspace_id: str
    name: str
