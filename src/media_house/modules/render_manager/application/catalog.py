"""Stored render reports, read: history, one report, the latest render of a timeline version."""

from media_house.modules.media_library.application.contracts import MediaError
from media_house.modules.render_manager.application.store import RenderStore
from media_house.modules.render_manager.domain.report import RenderReport
from media_house.modules.render_manager.domain.values import RenderType
from media_house.shared.errors import Result


class RenderHistory:
    """Implements ``RenderCatalog``."""

    def __init__(self, store: RenderStore) -> None:
        self._store = store

    def history(
        self,
        anchor_asset_id: str,
        *,
        timeline_id: str | None = None,
        render_type: RenderType | None = None,
    ) -> list[RenderReport]:
        """Every render attempt of the recording, newest first."""
        found = self._store.reports(
            anchor_asset_id,
            timeline_id=timeline_id,
            render_type=render_type.value if render_type else None,
        )
        return [report for report, _ in found]

    def get(self, anchor_asset_id: str, report_id: str) -> Result[RenderReport | None, MediaError]:
        return self._store.get(anchor_asset_id, report_id)

    def latest(
        self,
        anchor_asset_id: str,
        *,
        timeline_id: str | None = None,
        content_hash: str | None = None,
        render_type: RenderType | None = None,
        succeeded_only: bool = True,
    ) -> RenderReport | None:
        """The newest render of a timeline (by content hash: one video, however many rebuilds)."""
        for report, _ in self._store.reports(
            anchor_asset_id,
            timeline_id=timeline_id,
            content_hash=content_hash,
            render_type=render_type.value if render_type else None,
        ):
            if report.succeeded or not succeeded_only:
                return report
        return None

    def for_output(self, anchor_asset_id: str, output_asset_id: str) -> RenderReport | None:
        """The report of the render that made this file."""
        found = self._store.reports(anchor_asset_id, output_asset_id=output_asset_id)
        return found[0][0] if found else None
