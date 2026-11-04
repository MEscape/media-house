"""``GraphicsCatalog``: stored graphics plans, read-only (nothing is planned or rendered)."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicsDocument
from media_house.modules.motion_graphics_engine.domain.model import GraphicsPlan
from media_house.modules.motion_graphics_engine.domain.serialization import plan_from_json
from media_house.modules.motion_graphics_engine.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class GraphicsResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self, anchor_asset_id: str, *, project_id: str | None = None
    ) -> Result[tuple[GraphicsPlan, MediaAssetDto] | None, MediaError]:
        """The newest readable plan of the anchor (optionally of one project)."""
        for asset in self.history(anchor_asset_id):
            if project_id is not None and asset.metadata.get("project_id") != project_id:
                continue
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidGraphicsDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored graphics plan unusable", asset_id=asset.id, reason=str(loaded.error)
            )
        return Ok(None)

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]:
        """Every stored plan of the anchor, newest first."""
        stored = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(
        self, plan_asset_id: str
    ) -> Result[GraphicsPlan, MediaError | InvalidGraphicsDocument]:
        path = self._library.local_path(plan_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(plan_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidGraphicsDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError) as exc:
            return Err(InvalidGraphicsDocument(str(exc)))
