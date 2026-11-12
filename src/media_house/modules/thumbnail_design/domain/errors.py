from media_house.shared.errors import DomainError


class ThumbnailFailed(DomainError):
    code = "thumbnail_design.failed"


class InputUnusable(ThumbnailFailed):
    """A picture of the brief cannot be used (wrong type, unreadable, no cut-out)."""

    code = "thumbnail_design.input_unusable"

    def __init__(self, role: str, reason: str) -> None:
        super().__init__(
            f"The {role} picture cannot be used: {reason}",
            user_message=f"The {role} picture cannot be used: {reason}",
        )


class StyleUnavailable(ThumbnailFailed):
    code = "thumbnail_design.style_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"No channel style: {reason}",
            user_message=f"The channel style could not be loaded: {reason}",
        )
