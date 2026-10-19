"""Expected failures of audio-visual alignment (the user can act on them)."""

from media_house.shared.errors import DomainError


class AlignmentError(DomainError):
    code = "audio_visual_alignment.failed"


class MissingPrerequisite(AlignmentError):
    """A required upstream analysis is absent and was not allowed to be computed."""

    code = "audio_visual_alignment.missing_prerequisite"

    def __init__(self, what: str, how_to_get: str) -> None:
        super().__init__(
            f"Missing prerequisite: {what}. {how_to_get}",
            user_message=f"{what} is needed first. {how_to_get}",
        )


class IncompatibleInputs(AlignmentError):
    code = "audio_visual_alignment.incompatible_inputs"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"The audio and video analyses do not fit together: {reason}",
            user_message=f"The audio and video analyses do not fit together: {reason}",
        )


class InvalidAlignmentConfig(AlignmentError):
    code = "audio_visual_alignment.invalid_config"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid alignment settings: {reason}",
            user_message=f"The alignment settings are not valid: {reason}",
        )


class InvalidAlignmentDocument(AlignmentError):
    """A stored document cannot be read (the caller recomputes instead of guessing)."""

    code = "audio_visual_alignment.invalid_document"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Stored alignment unusable: {reason}",
            user_message="A stored alignment result could not be read and will be recomputed.",
        )
