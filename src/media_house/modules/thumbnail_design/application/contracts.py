"""The Thumbnail Design PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``thumbnail_design``. Every call is
blocking (cut-outs, compositing, file I/O): run it as a job.

* ``ThumbnailDesigner``: a brief -> its A/B/C thumbnails, stored as derived pictures of the video
  (``created`` is ``False`` when the identical thumbnail already existed).
* ``ReactionSuggester``: good reaction moments of recordings, from their STORED video analyses,
  as exact frames to pick from.
* ``ReactionCollection``: the owner's reaction pictures (library pictures tagged ``reaction``).
* ``ThumbnailExporter``: the 1280x720 JPEG upload copy.

Typical use::

    brief = ThumbnailBrief(
        headline=Headline(("Erstmals", "online!")),
        reaction_asset_id=reaction_id,
        products=(seven_kings_id, armageddon_id),
        logo_asset_id=lidl_logo_id,
        device=DeviceCard(product_asset_id=pure_impact_id, product_name="Pure Impact",
                          price="79,99 €", screen_asset_id=shop_frame_id),
        badge=StoryBadge("04:30"),
    )
    result = designer.execute(DesignThumbnailsCommand(brief=brief, anchor_asset_id=video_id), ctx)
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.thumbnail_design.application.design_thumbnails import (
    OPERATION,
    DesignedThumbnails,
    DesignedVariant,
    DesignError,
    DesignThumbnailsCommand,
)
from media_house.modules.thumbnail_design.application.export import (
    UPLOAD_MAX_BYTES,
    UPLOAD_WIDTH,
)
from media_house.modules.thumbnail_design.application.reactions import (
    REACTION_TAG,
    ReactionCandidate,
)
from media_house.modules.thumbnail_design.domain.brief import (
    MAX_CHARS_PER_LINE,
    MAX_LINES,
    MAX_PRODUCTS,
    MAX_WORDS_PER_LINE,
    DeviceCard,
    Headline,
    StoryBadge,
    ThumbnailBrief,
)
from media_house.modules.thumbnail_design.domain.errors import (
    InputUnusable,
    StyleUnavailable,
    ThumbnailFailed,
)
from media_house.modules.thumbnail_design.domain.qc import QcIssue, Severity
from media_house.modules.thumbnail_design.domain.reactions import ReactionMoment
from media_house.modules.visual_asset_generation.application.contracts import FrameError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ThumbnailDesigner(Protocol):
    def execute(
        self, command: DesignThumbnailsCommand, ctx: JobContext
    ) -> Result[DesignedThumbnails, DesignError]: ...


class ReactionSuggester(Protocol):
    def execute(
        self, video_asset_ids: Sequence[str], ctx: JobContext, limit: int = 6
    ) -> Result[tuple[ReactionCandidate, ...], MediaError | FrameError]: ...


class ReactionCollection(Protocol):
    def list(self) -> tuple[MediaAssetDto, ...]: ...

    def add(self, path: Path) -> Result[MediaAssetDto, MediaError]: ...

    def mark(self, asset_id: str) -> Result[MediaAssetDto, MediaError]: ...


class ThumbnailExporter(Protocol):
    def execute(self, asset_id: str, destination: Path) -> Result[Path, MediaError]: ...


__all__ = [
    "MAX_CHARS_PER_LINE",
    "MAX_LINES",
    "MAX_PRODUCTS",
    "MAX_WORDS_PER_LINE",
    "OPERATION",
    "REACTION_TAG",
    "UPLOAD_MAX_BYTES",
    "UPLOAD_WIDTH",
    "DesignError",
    "DesignThumbnailsCommand",
    "DesignedThumbnails",
    "DesignedVariant",
    "DeviceCard",
    "Headline",
    "InputUnusable",
    "QcIssue",
    "ReactionCandidate",
    "ReactionCollection",
    "ReactionMoment",
    "ReactionSuggester",
    "Severity",
    "StoryBadge",
    "StyleUnavailable",
    "ThumbnailBrief",
    "ThumbnailDesigner",
    "ThumbnailExporter",
    "ThumbnailFailed",
]
