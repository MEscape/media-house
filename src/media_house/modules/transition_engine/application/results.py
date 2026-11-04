"""Retrieve stored transition plans without running anything."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.transition_engine.domain.errors import InvalidTransitionDocument
from media_house.modules.transition_engine.domain.model import TransitionPlan
from media_house.modules.transition_engine.domain.serialization import plan_from_json
from media_house.modules.transition_engine.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class TransitionResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self,
        anchor_asset_id: str,
        *,
        edit_plan_id: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[TransitionPlan, MediaAssetDto] | None, MediaError]:
        """The newest stored plan of the anchor (optionally of one edit plan and profile)."""
        for asset in self.history(anchor_asset_id):
            meta = asset.metadata
            if edit_plan_id is not None and meta.get("edit_plan_id") != edit_plan_id:
                continue
            if profile is not None and meta.get("profile") != profile:
                continue
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidTransitionDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored transition plan unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]:
        """Every stored plan of the anchor, newest first (earlier ones are never changed)."""
        stored = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(
        self, plan_asset_id: str
    ) -> Result[TransitionPlan, MediaError | InvalidTransitionDocument]:
        path = self._library.local_path(plan_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(plan_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidTransitionDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidTransitionDocument(str(exc)))
