"""Retrieve stored reframing analyses without running anything."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.reframing_intelligence.domain.errors import InvalidReframingDocument
from media_house.modules.reframing_intelligence.domain.model import ReframingAnalysis
from media_house.modules.reframing_intelligence.domain.serialization import analysis_from_json
from media_house.modules.reframing_intelligence.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ReframingResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self,
        asset_id: str,
        *,
        target: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[ReframingAnalysis, MediaAssetDto] | None, MediaError]:
        """The newest stored analysis of the asset (optionally of one target format and profile).

        A target is its aspect text (``9:16``); with none given the newest of any target answers,
        which is only right for a consumer that does not care which format it was planned for."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (target is None or a.metadata.get("target") == target)
            and (profile is None or a.metadata.get("profile") == profile)
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidReframingDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored reframing analysis unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored analysis of the asset, newest first (earlier ones are never changed)."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(
        self, analysis_asset_id: str
    ) -> Result[ReframingAnalysis, MediaError | InvalidReframingDocument]:
        path = self._library.local_path(analysis_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(analysis_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidReframingDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidReframingDocument(str(exc)))
