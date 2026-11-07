"""Ports of the revision engine: what it asks of the world that no other module offers directly."""

from dataclasses import dataclass
from typing import Protocol

from media_house.modules.review_orchestrator.application.contracts import TimelineRef
from media_house.shared.concurrency import JobContext


@dataclass(frozen=True, slots=True)
class ManualChanges:
    """A person's edits in the editing application, read back before anything is changed."""

    checked: bool
    #: Edits the timeline builder now holds (provenance: a person's).
    recorded: int = 0
    #: Edits that stay in the editing application only.
    unrecorded: int = 0
    note: str = ""


class ManualChangeSync(Protocol):
    def read_back(self, anchor_asset_id: str, timeline_id: str, ctx: JobContext) -> ManualChanges:
        """Read a person's edits into the built timeline. ``checked=False`` with the reason when
        the editing application cannot be reached (the revision then says it did not verify)."""
        ...


class PreviewProvider(Protocol):
    def preview_for(self, timeline: TimelineRef, ctx: JobContext) -> str | None:
        """A rendered preview (library asset id) of exactly this version, or ``None`` when none can
        be made; the quality checks that need pictures and sound then report "not run"."""
        ...


class NoPreview:
    """The default: no render is made here, the revalidation checks the timeline itself."""

    def preview_for(self, timeline: TimelineRef, ctx: JobContext) -> str | None:
        del timeline, ctx
        return None
