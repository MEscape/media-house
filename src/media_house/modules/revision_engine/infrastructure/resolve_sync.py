"""Reads a person's edits in the editing application back into the built timeline through
``resolve_adapter`` (never a connection of its own), so a revision starts from what the person
actually has."""

from media_house.modules.resolve_adapter.application.contracts import (
    ResolveSynchronizer,
    SyncCommand,
)
from media_house.modules.revision_engine.application.ports import ManualChanges
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ResolveReadBack:
    """Structurally implements ``ManualChangeSync``."""

    def __init__(self, synchronizer: ResolveSynchronizer) -> None:
        self._synchronizer = synchronizer

    def read_back(self, anchor_asset_id: str, timeline_id: str, ctx: JobContext) -> ManualChanges:
        result = self._synchronizer.execute(
            SyncCommand(
                anchor_asset_id=anchor_asset_id,
                timeline_id=timeline_id,
                record=True,
                editor="resolve",
            ),
            ctx,
        )
        if isinstance(result, Err):
            _log.info("Edits in Resolve not read back", reason=str(result.error))
            return ManualChanges(checked=False, note=str(result.error.user_message))
        found = result.value
        return ManualChanges(
            checked=True, recorded=len(found.recorded), unrecorded=len(found.unrecorded)
        )
