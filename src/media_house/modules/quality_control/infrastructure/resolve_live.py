"""Looks at the live timeline in Resolve through ``resolve_adapter`` (never a connection of
its own) and records nothing: a person's changes found there are counted, not imported."""

from media_house.modules.quality_control.application.ports import LiveChanges
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveSynchronizer,
    SyncCommand,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ResolveLiveProbe:
    """Structurally implements ``LiveTimelineProbe``."""

    def __init__(self, synchronizer: ResolveSynchronizer) -> None:
        self._synchronizer = synchronizer

    def look(self, anchor_asset_id: str, timeline_id: str, ctx: JobContext) -> LiveChanges:
        result = self._synchronizer.execute(
            SyncCommand(anchor_asset_id=anchor_asset_id, timeline_id=timeline_id, record=False),
            ctx,
        )
        if isinstance(result, Err):
            _log.info("Live timeline not looked at", reason=str(result.error))
            return LiveChanges(checked=False, note=str(result.error.user_message))
        found = result.value
        changed = len(found.recorded) + len(found.unrecorded)
        return LiveChanges(
            checked=True,
            changes=changed,
            note=f"{len(found.recorded)} clip edits the builder can hold, "
            f"{len(found.unrecorded)} that stay in Resolve",
        )
