"""Expected failures of image adjustment."""

from media_house.shared.errors import DomainError


class AdjustmentFailed(DomainError):
    """Base class: the image could not be adjusted for a reason the user can act on."""

    code = "image_adjustment.failed"


class ImageUnreadable(AdjustmentFailed):
    """The source file could not be decoded as an image."""

    code = "image_adjustment.unreadable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Source image cannot be decoded: {reason}",
            user_message="This image could not be read.",
        )


class SegmentationRejected(AdjustmentFailed):
    """The background-removal mask failed validation, so nothing is stored."""

    code = "image_adjustment.mask_rejected"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Background removal rejected: {reason}",
            user_message=(
                "Background removal did not find a usable subject in this image "
                f"({reason}). Try a different image or adjust the mask limits."
            ),
        )
