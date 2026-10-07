"""Expected failures of audio improvement."""

from media_house.shared.errors import DomainError


class AudioImprovementError(DomainError):
    """Base class for failures the user can act on."""

    code = "audio_improvement.failed"


class InvalidProfile(AudioImprovementError):
    """The requested profile or one of its settings does not exist or is not acceptable."""

    code = "audio_improvement.invalid_profile"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid audio profile: {reason}",
            user_message=f"The audio profile is not valid: {reason}",
        )


class UnreadableSource(AudioImprovementError):
    """The media has no usable audio."""

    code = "audio_improvement.unreadable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Audio cannot be read: {reason}",
            user_message="This file could not be read as audio.",
        )
