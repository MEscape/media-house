"""Who is acting. A decision carries its actor, and what the actor is decides what it may do."""

from dataclasses import dataclass

from media_house.modules.review_orchestrator.domain.values import ActorKind


@dataclass(frozen=True, slots=True)
class Actor:
    name: str
    kind: ActorKind

    @classmethod
    def human(cls, name: str) -> "Actor":
        return cls(name, ActorKind.HUMAN)

    @classmethod
    def system(cls, name: str) -> "Actor":
        """A module doing its own job (running an analysis, recording a result)."""
        return cls(name, ActorKind.SYSTEM)

    @classmethod
    def preconfigured(cls, name: str) -> "Actor":
        """Automation acting on a grant the project made in advance."""
        return cls(name, ActorKind.PRECONFIGURED)
