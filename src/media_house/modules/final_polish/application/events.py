"""What other modules may react to (``final_polish.completed``)."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class FinalPolishCompleted(ApplicationEvent):
    final_polish_report_id: str
    timeline_id: str
    timeline_version: int
    review_session_id: str | None
    readiness: str
    remaining: int
    created: bool
