"""Expected failures of video improvement."""

from media_house.shared.errors import DomainError


class VideoImprovementError(DomainError):
    """Base class for failures the user can act on."""

    code = "video_improvement.failed"


class InvalidProfile(VideoImprovementError):
    """The requested profile or one of its settings does not exist or is not acceptable."""

    code = "video_improvement.invalid_profile"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid video profile: {reason}",
            user_message=f"The video profile is not valid: {reason}",
        )


class UnreadableSource(VideoImprovementError):
    """The media has no usable video."""

    code = "video_improvement.unreadable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Video cannot be read: {reason}",
            user_message="This file could not be read as video.",
        )


class UnsupportedSource(VideoImprovementError):
    """Valid video that this version does not process (it would only be degraded)."""

    code = "video_improvement.unsupported"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Video not supported: {reason}",
            user_message=f"This video cannot be improved yet: {reason}",
        )


class InvalidLut(VideoImprovementError):
    """A LUT file is missing, unreadable or not a usable 3D ``.cube`` LUT."""

    code = "video_improvement.invalid_lut"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid LUT: {reason}",
            user_message=f"The LUT cannot be used: {reason}",
        )


class VerificationFailed(VideoImprovementError):
    """The processed video changed something it must preserve; it was not stored."""

    code = "video_improvement.verification_failed"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Output verification failed: {reason}",
            user_message="The improved video did not pass its quality checks and was discarded.",
        )
