"""What other modules may react to, published once when a validation was made (not when a stored
one was served)."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class ExportValidated(ApplicationEvent):
    """``export.validation.completed``."""

    export_validation_id: str
    export_id: str
    timeline_version: int
    #: ``pass`` / ``warning`` / ``fail`` / ``manual_review_required``
    status: str
