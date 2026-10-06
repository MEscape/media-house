"""Value objects."""

from dataclasses import dataclass
from typing import ClassVar

from media_house.shared.errors import InvariantViolation
from media_house.shared.types import new_id


@dataclass(frozen=True, slots=True)
class WorkspaceId:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvariantViolation("WorkspaceId must not be empty")

    @classmethod
    def new(cls) -> "WorkspaceId":
        return cls(new_id())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class WorkspaceName:
    """A display name: 1-80 characters, no control characters, no surrounding whitespace.

    Names are unique *case-insensitively*; use :attr:`key` for comparisons.
    """

    MAX_LENGTH: ClassVar[int] = 80
    value: str

    def __post_init__(self) -> None:
        if not self.value or self.value != self.value.strip():
            raise InvariantViolation(
                "Workspace name must be non-empty and trimmed",
                user_message="Please enter a workspace name.",
            )
        if len(self.value) > self.MAX_LENGTH:
            raise InvariantViolation(
                "Workspace name too long",
                user_message=f"A workspace name can have at most {self.MAX_LENGTH} characters.",
            )
        if any(not ch.isprintable() for ch in self.value):
            raise InvariantViolation(
                "Workspace name contains control characters",
                user_message="The workspace name contains characters that are not allowed.",
            )

    @classmethod
    def of(cls, raw: str) -> "WorkspaceName":
        """Normalise user input (trim) and validate."""
        return cls(raw.strip())

    @property
    def key(self) -> str:
        return self.value.casefold()

    def __str__(self) -> str:
        return self.value
