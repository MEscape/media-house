"""Retrieve stored audio intelligence timelines without running anything."""

from media_house.modules.audio_intelligence.domain.analysis.config import ANALYSIS_OPERATION
from media_house.modules.audio_intelligence.domain.analysis.serialization import (
    timeline_from_json,
)
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class AudioResults:
    """Reads the timelines the Media Library holds for an asset (nothing is computed)."""

    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, asset_id: str
    ) -> Result[tuple[AudioIntelligenceTimeline, MediaAssetDto] | None, MediaError]:
        """The most recently stored timeline of ``asset_id``, or ``Ok(None)`` if there is none."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == ANALYSIS_OPERATION
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                return path
            try:
                return Ok((timeline_from_json(path.value.read_text(encoding="utf-8")), asset))
            except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
                _log.warning(
                    "Stored audio timeline unusable, trying an older one",
                    asset_id=asset.id,
                    reason=str(exc),
                )
        return Ok(None)
