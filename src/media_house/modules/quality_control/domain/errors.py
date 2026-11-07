"""Business errors of quality control."""

from media_house.shared.errors import DomainError


class QualityControlError(DomainError):
    code = "quality_control.error"


class InvalidQualityDocument(QualityControlError):
    code = "quality_control.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored quality report is unreadable: {detail}",
            user_message="A stored quality report could not be read.",
        )


class NothingToAnalyze(QualityControlError):
    code = "quality_control.nothing_to_analyze"

    def __init__(self) -> None:
        super().__init__(
            "No built timeline to analyse",
            user_message="There is no built timeline to check yet.",
        )


class StaleReview(QualityControlError):
    """The review session is bound to another version of the timeline than the stored one."""

    code = "quality_control.stale_review"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"The review does not match the timeline: {detail}",
            user_message="The review is not at the current version of the timeline.",
        )
