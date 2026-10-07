"""Expected failures of media inspection."""

from media_house.shared.errors import DomainError


class MediaInspectionError(DomainError):
    """Base class for failures the user can act on."""

    code = "media_inspection.failed"


class InvalidInspectionConfig(MediaInspectionError):
    """A threshold or option of the inspection is not acceptable."""

    code = "media_inspection.invalid_config"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid inspection configuration: {reason}",
            user_message=f"The inspection settings are not valid: {reason}",
        )


class InvalidInspectionDocument(MediaInspectionError):
    """A stored inspection cannot be read (damaged, or written by a newer schema)."""

    code = "media_inspection.invalid_document"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Inspection document unusable: {reason}",
            user_message="The stored inspection could not be read.",
        )
