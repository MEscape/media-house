"""What other modules may react to (``revision_plan.created``, ``revision.completed``...)."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class RevisionEvent(ApplicationEvent):
    revision_plan_id: str
    review_session_id: str
    timeline_version: int


@dataclass(frozen=True, kw_only=True, slots=True)
class RevisionPlanCreated(RevisionEvent):
    changes: int
    conflicts: int


@dataclass(frozen=True, kw_only=True, slots=True)
class RevisionExecuted(RevisionEvent):
    status: str
    new_timeline_version: int | None


@dataclass(frozen=True, kw_only=True, slots=True)
class RevisionHandedOff(RevisionEvent):
    """A change passed to the module that owns it: that module's own planner does the rest."""

    proposal_id: str
    owner_module: str
    intent: str
