"""Retrieve stored VFX plans without running anything."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.vfx_engine.domain.errors import InvalidVfxDocument
from media_house.modules.vfx_engine.domain.model import VfxPlan
from media_house.modules.vfx_engine.domain.serialization import plan_from_json
from media_house.modules.vfx_engine.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class VfxResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self,
        asset_id: str,
        *,
        edit_plan_id: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[VfxPlan, MediaAssetDto] | None, MediaError]:
        """The newest stored plan of the anchor recording (optionally of one edit / profile)."""
        stored = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (edit_plan_id is None or a.metadata.get("edit_plan_id") == edit_plan_id)
            and (profile is None or a.metadata.get("profile") == profile)
        ]
        for asset in sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidVfxDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored VFX plan unusable, trying an older one",
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

    def load(self, plan_asset_id: str) -> Result[VfxPlan, MediaError | InvalidVfxDocument]:
        path = self._library.local_path(plan_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(plan_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidVfxDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidVfxDocument(str(exc)))
