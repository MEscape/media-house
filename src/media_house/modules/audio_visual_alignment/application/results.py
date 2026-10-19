"""Retrieve stored alignments without running anything."""

from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument
from media_house.modules.audio_visual_alignment.domain.serialization import alignment_from_json
from media_house.modules.audio_visual_alignment.domain.values import RESULT_OPERATION
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class AlignmentResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, video_asset_id: str
    ) -> Result[tuple[AlignmentDocument, MediaAssetDto] | None, MediaError]:
        """The newest stored alignment of the video, or ``Ok(None)``."""
        stored = [
            a
            for a in self._library.list_derived(video_asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                return path
            try:
                return Ok((alignment_from_json(path.value.read_text(encoding="utf-8")), asset))
            except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
                _log.warning(
                    "Stored alignment unusable, trying an older one",
                    asset_id=asset.id,
                    reason=str(exc),
                )
        return Ok(None)
