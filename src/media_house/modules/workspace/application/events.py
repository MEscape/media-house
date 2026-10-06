"""Application events (workflow-level, as opposed to domain events)."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class WorkspaceVerificationCompleted(ApplicationEvent):
    checked: int
    problem_count: int
