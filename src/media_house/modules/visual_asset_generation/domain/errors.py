"""Expected failures of visual asset preparation (cutouts, variants, frames, exports)."""

from media_house.shared.errors import DomainError


class AdjustmentFailed(DomainError):
    """Base class: the image could not be adjusted for a reason the user can act on."""

    code = "visual_asset_generation.failed"


class ImageUnreadable(AdjustmentFailed):
    """The source file could not be decoded as an image."""

    code = "visual_asset_generation.unreadable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Source image cannot be decoded: {reason}",
            user_message="This image could not be read.",
        )


class SegmentationRejected(AdjustmentFailed):
    """The background-removal mask failed validation, so nothing is stored."""

    code = "visual_asset_generation.mask_rejected"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Background removal rejected: {reason}",
            user_message=(
                "Background removal did not find a usable subject in this image "
                f"({reason}). Try a different image or adjust the mask limits."
            ),
        )


class VisualAssetError(AdjustmentFailed):
    """Base class of the visual-asset preparation failures added on top of the cutout."""

    code = "visual_asset_generation.error"


class InvalidVisualAssetConfig(VisualAssetError):
    """A profile name, an override path or a value is not acceptable."""

    code = "visual_asset_generation.invalid_config"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid visual asset configuration: {reason}",
            user_message=f"The visual asset settings are not valid: {reason}.",
        )


class InvalidVisualAssetRequest(VisualAssetError):
    """The batch itself is unusable (no request, duplicate ids, a video without a time)."""

    code = "visual_asset_generation.invalid_request"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid visual asset request: {reason}",
            user_message=f"The visual asset request is not valid: {reason}.",
        )


class NoVideoStream(VisualAssetError):
    """A frame was asked of an asset that has no picture (pipeline: skipped, not failed)."""

    code = "visual_asset_generation.no_video_stream"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"Asset {asset_id} has no video stream",
            user_message="A frame can only be taken from a video.",
        )


class FrameGrabFailed(VisualAssetError):
    """FFmpeg could not deliver the requested frame (missing tool, decode error, past the end)."""

    code = "visual_asset_generation.frame_grab_failed"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Frame grab failed: {reason}",
            user_message=f"The frame could not be taken from the video ({reason}).",
        )


class ExportRefused(VisualAssetError):
    """An export would silently lose something (transparency) or is impossible."""

    code = "visual_asset_generation.export_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Export refused: {reason}",
            user_message=f"The image was not exported: {reason}.",
        )


class GenerationRefused(VisualAssetError):
    """The generation policy does not allow this request (logo, product photo, evidence, ...)."""

    code = "visual_asset_generation.generation_refused"

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__(
            f"Image generation refused: {'; '.join(reasons)}",
            user_message=f"No image was generated: {'; '.join(reasons)}.",
        )
        self.reasons = reasons


class GenerationNotAvailable(VisualAssetError):
    """No image generator is installed or usable; nothing is faked."""

    code = "visual_asset_generation.generation_not_available"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Image generation is not available: {reason}",
            user_message=f"Image generation is not available here: {reason}.",
        )


class InvalidVisualAssetDocument(VisualAssetError):
    """A stored visual asset set cannot be read (never reinterpreted)."""

    code = "visual_asset_generation.invalid_document"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Unreadable visual asset set: {reason}",
            user_message="A stored visual asset set could not be read.",
        )
