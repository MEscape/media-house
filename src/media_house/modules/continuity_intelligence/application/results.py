"""Retrieve stored continuity analyses without running anything."""

from media_house.modules.continuity_intelligence.domain.errors import InvalidContinuityDocument
from media_house.modules.continuity_intelligence.domain.model import ContinuityAnalysis
from media_house.modules.continuity_intelligence.domain.serialization import analysis_from_json
from media_house.modules.continuity_intelligence.domain.values import RESULT_OPERATION
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ContinuityResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self,
        asset_id: str,
        *,
        profile: str | None = None,
        edit_plan_id: str | None = None,
    ) -> Result[tuple[ContinuityAnalysis, MediaAssetDto] | None, MediaError]:
        """The newest stored analysis of the asset (optionally of one profile and one edit plan)."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (profile is None or a.metadata.get("profile") == profile)
            and (edit_plan_id is None or a.metadata.get("edit_plan_id") == edit_plan_id)
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidContinuityDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored pacing analysis unusable, trying an older one",
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
    ) -> Result[ContinuityAnalysis, MediaError | InvalidContinuityDocument]:
        path = self._library.local_path(analysis_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(analysis_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidContinuityDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidContinuityDocument(str(exc)))
