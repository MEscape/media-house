"""Retrieve stored video analyses without running anything."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.video_intelligence.domain.result import VideoAnalysis
from media_house.modules.video_intelligence.domain.serialization import analysis_from_json
from media_house.modules.video_intelligence.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class VideoResults:
    """Reads the analyses the Media Library holds for an asset (nothing is computed)."""

    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, asset_id: str, profile: str | None = None
    ) -> Result[tuple[VideoAnalysis, MediaAssetDto] | None, MediaError]:
        """The newest stored analysis of ``asset_id`` (of ``profile`` if given), or ``Ok(None)``."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                return path
            try:
                analysis = analysis_from_json(path.value.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
                _log.warning(
                    "Stored video analysis unusable, trying an older one",
                    asset_id=asset.id,
                    reason=str(exc),
                )
                continue
            if profile is None or analysis.provenance.profile_name == profile:
                return Ok((analysis, asset))
        return Ok(None)
