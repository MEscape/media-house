"""Data crossing the application boundary. Presentation sees these, never domain objects."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WorkspaceDto:
    id: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VerificationSummary:
    checked: int
    problems: tuple[str, ...]

    @property
    def is_healthy(self) -> bool:
        return not self.problems
