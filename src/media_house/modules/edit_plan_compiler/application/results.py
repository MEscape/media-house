"""Retrieve stored timeline specifications without running anything."""

from media_house.modules.edit_plan_compiler.domain.errors import InvalidTimelineDocument
from media_house.modules.edit_plan_compiler.domain.model import TimelineSpecification
from media_house.modules.edit_plan_compiler.domain.serialization import spec_from_json
from media_house.modules.edit_plan_compiler.domain.values import RESULT_OPERATION
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class TimelineResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, anchor_asset_id: str, *, edit_plan_id: str | None = None
    ) -> Result[tuple[TimelineSpecification, MediaAssetDto] | None, MediaError]:
        """The newest stored specification of the anchor (optionally of one edit plan)."""
        for asset in self.history(anchor_asset_id):
            if edit_plan_id is not None and asset.metadata.get("edit_plan_id") != edit_plan_id:
                continue
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidTimelineDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored timeline specification unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]:
        """Every stored specification of the anchor, newest first (never changed or removed)."""
        stored = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(
        self, spec_asset_id: str
    ) -> Result[TimelineSpecification, MediaError | InvalidTimelineDocument]:
        path = self._library.local_path(spec_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(spec_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidTimelineDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidTimelineDocument(str(exc)))
