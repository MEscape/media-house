"""Retrieve stored analyses without running anything."""

from media_house.modules.content_intelligence.domain.errors import InvalidAnalysisDocument
from media_house.modules.content_intelligence.domain.model import ContentAnalysis
from media_house.modules.content_intelligence.domain.serialization import analysis_from_json
from media_house.modules.content_intelligence.domain.values import RESULT_OPERATION
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ContentResults:
    """Reads the content analyses the Media Library holds for an asset."""

    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, asset_id: str
    ) -> Result[tuple[ContentAnalysis, MediaAssetDto] | None, MediaError | InvalidAnalysisDocument]:
        """The most recently stored analysis of ``asset_id``, or ``Ok(None)`` if there is none."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            _log.warning("Stored content analysis unusable, trying an older one", asset_id=asset.id)
        return Ok(None)

    def load(
        self, analysis_asset_id: str
    ) -> Result[ContentAnalysis, MediaError | InvalidAnalysisDocument]:
        path = self._library.local_path(analysis_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(analysis_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidAnalysisDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, MediaHouseError) as exc:
            return Err(InvalidAnalysisDocument(f"unreadable: {exc}"))
