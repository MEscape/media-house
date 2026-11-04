"""Read-only access to stored visual asset sets (nothing is computed here)."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.visual_asset_generation.domain.errors import InvalidVisualAssetDocument
from media_house.modules.visual_asset_generation.domain.model import SET_OPERATION, VisualAssetSet
from media_house.modules.visual_asset_generation.domain.serialization import set_from_json
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class VisualAssetResults:
    """Implements ``VisualAssetCatalog`` over the library's derived documents."""

    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, asset_id: str, *, project_id: str | None = None
    ) -> Result[tuple[VisualAssetSet, MediaAssetDto] | None, MediaError]:
        anchor = self._library.get(asset_id)
        if isinstance(anchor, Err):
            return anchor
        for stored in self.history(asset_id):
            if project_id is not None and stored.metadata.get("project_id") != project_id:
                continue
            loaded = self.load(stored.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, stored))
            _log.warning("Unreadable visual asset set skipped", asset_id=stored.id)
        return Ok(None)

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored set of the anchor, newest first (earlier ones are never changed)."""
        sets = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == SET_OPERATION
        ]
        return sorted(sets, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(
        self, set_asset_id: str
    ) -> Result[VisualAssetSet, MediaError | InvalidVisualAssetDocument]:
        path = self._library.local_path(set_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(set_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidVisualAssetDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError) as exc:
            return Err(InvalidVisualAssetDocument(str(exc)))
