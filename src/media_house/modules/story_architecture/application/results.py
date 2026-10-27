"""Retrieve stored story plans without running anything."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.story_architecture.domain.errors import InvalidStoryDocument
from media_house.modules.story_architecture.domain.model import StoryPlan
from media_house.modules.story_architecture.domain.serialization import plan_from_json
from media_house.modules.story_architecture.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class StoryResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self,
        asset_id: str,
        *,
        format: str | None = None,  # noqa: A002
    ) -> Result[tuple[StoryPlan, MediaAssetDto] | None, MediaError]:
        """The newest stored plan of the asset (optionally of one format), or ``Ok(None)``."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (format is None or a.metadata.get("format") == format)
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidStoryDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored plan unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored plan of the asset, newest first (the version history; never edited)."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(self, plan_asset_id: str) -> Result[StoryPlan, MediaError | InvalidStoryDocument]:
        path = self._library.local_path(plan_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(plan_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidStoryDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidStoryDocument(str(exc)))
