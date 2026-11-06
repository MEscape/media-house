"""Retrieve stored built timelines without running anything."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.timeline_builder.domain.errors import InvalidBuiltDocument
from media_house.modules.timeline_builder.domain.model import BuiltTimeline
from media_house.modules.timeline_builder.domain.serialization import timeline_from_json
from media_house.modules.timeline_builder.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class BuiltTimelineResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> Result[tuple[BuiltTimeline, MediaAssetDto] | None, MediaError]:
        """The newest stored build of the anchor (optionally of one timeline)."""
        for asset in self.history(anchor_asset_id, timeline_id=timeline_id):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidBuiltDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored built timeline unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def history(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> list[MediaAssetDto]:
        """Every stored build of the anchor, newest first (never changed or removed)."""
        stored = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (timeline_id is None or a.metadata.get("timeline_id") == timeline_id)
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(self, build_asset_id: str) -> Result[BuiltTimeline, MediaError | InvalidBuiltDocument]:
        path = self._library.local_path(build_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(timeline_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidBuiltDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidBuiltDocument(str(exc)))
