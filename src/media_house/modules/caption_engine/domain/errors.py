"""Business errors of the caption engine."""

from collections.abc import Sequence

from media_house.shared.errors import DomainError


class CaptionEngineError(DomainError):
    code = "caption_engine.error"


class InvalidCaptionConfig(CaptionEngineError):
    code = "caption_engine.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid caption configuration: {detail}",
            user_message=f"The caption settings are not valid: {detail}",
        )


class InvalidCaptionTimeline(CaptionEngineError):
    """The timeline the caller built cannot be captioned (empty, negative or reversed ranges)."""

    code = "caption_engine.invalid_timeline"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid caption timeline: {detail}",
            user_message=f"The edit cannot be captioned: {detail}",
        )


class StyleNotAvailable(CaptionEngineError):
    """The visual style named in the command cannot be read."""

    code = "caption_engine.style_not_available"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Visual style not available: {detail}",
            user_message="The channel style of this project could not be read.",
            details={"detail": detail},
        )


class InvalidCaptionPlan(CaptionEngineError):
    """The planned captions break a critical rule; nothing was stored."""

    code = "caption_engine.invalid_plan"

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(
            "Caption plan is invalid: " + "; ".join(problems),
            user_message="The captions could not be planned consistently.",
            details={"problems": list(problems)},
        )


class InvalidCaptionDocument(CaptionEngineError):
    code = "caption_engine.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored caption plan unreadable: {detail}",
            user_message="A stored caption plan could not be read.",
        )
