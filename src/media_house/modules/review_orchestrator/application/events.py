"""What other modules may react to. Events are published after the step was stored, once per step;
a step that was already true (a repeated decision) publishes nothing."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewEvent(ApplicationEvent):
    session_id: str
    timeline_version: int
    summary: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewStateChanged(ReviewEvent):
    source: str
    target: str


@dataclass(frozen=True, kw_only=True, slots=True)
class TimelineVersionBound(ReviewEvent):
    """``timeline.updated`` / ``timeline.manual_change_detected`` of the workflow."""

    material: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class DecisionRecorded(ReviewEvent):
    kind: str
    verdict: str
    subject: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ApprovalInvalidated(ReviewEvent):
    approval_id: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ExportAuthorized(ReviewEvent):
    authorization_id: str
