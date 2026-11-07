"""What other modules may react to (``quality_control.completed``, ``quality_issue.detected``)."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class QualityControlCompleted(ApplicationEvent):
    quality_report_id: str
    timeline_id: str
    timeline_version: int
    review_session_id: str | None
    critical: int
    high: int
    total: int
    #: ``False`` when an identical report already existed (nothing was analysed again).
    created: bool
