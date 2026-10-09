"""Audio-specific guard in front of the shared derived-document store."""

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaType
from media_house.shared.errors import Err, ValidationError


def require_audio_or_video(source: MediaAssetDto, action: str) -> Err[ValidationError] | None:
    """An ``Err`` for any asset that is neither audio nor video, else ``None``."""
    if source.media_type in {MediaType.AUDIO, MediaType.VIDEO}:
        return None
    return Err(
        ValidationError(
            f"Asset {source.id} is {source.media_type.value}, not audio or video",
            field="source_asset_id",
            user_message=f"Only audio and video files can be {action}.",
        ),
    )
