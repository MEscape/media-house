"""What other modules may react to: an export started, ended, a delivery ready."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class ExportEvent(ApplicationEvent):
    export_id: str
    review_session_id: str
    timeline_version: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ExportStarted(ExportEvent):
    profile_id: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ExportCompleted(ExportEvent):
    """The export reached an end: rendered and checked, or failed. ``status`` says which."""

    status: str
    validation_status: str


@dataclass(frozen=True, kw_only=True, slots=True)
class DeliveryReady(ExportEvent):
    delivered_as: str
