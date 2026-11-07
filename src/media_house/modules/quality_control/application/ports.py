"""Ports of quality control: the one thing it asks of the world that no other module offers."""

from dataclasses import dataclass
from typing import Protocol

from media_house.shared.concurrency import JobContext


@dataclass(frozen=True, slots=True)
class LiveChanges:
    """What the editing application holds now, compared with what was last written to it."""

    checked: bool
    #: Clips a person changed there since the last synchronisation (``None``: not looked at).
    changes: int | None = None
    note: str = ""


class LiveTimelineProbe(Protocol):
    def look(self, anchor_asset_id: str, timeline_id: str, ctx: JobContext) -> LiveChanges:
        """Look at the live timeline without recording anything. Never raises for an editing
        application that is not there: it answers ``checked=False`` with the reason."""
        ...
