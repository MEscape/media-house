"""Commands: intent to change state. Plain data, validated by the use case."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CreateWorkspaceCommand:
    name: str
