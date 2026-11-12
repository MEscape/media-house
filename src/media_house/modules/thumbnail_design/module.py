"""Module registration: how Thumbnail Design plugs into the application.

It owns no storage: thumbnails are derived pictures of the video in the Media Library, cut-outs
and frames come from visual asset generation, the look from the visual style engine and the
reaction moments from stored video analyses. Its UI is a Control Center page.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.thumbnail_design.application.contracts import (
    ReactionCollection,
    ReactionSuggester,
    ThumbnailDesigner,
    ThumbnailExporter,
)
from media_house.modules.thumbnail_design.application.design_thumbnails import DesignThumbnails
from media_house.modules.thumbnail_design.application.export import ExportThumbnail
from media_house.modules.thumbnail_design.application.ports import ThumbnailRenderer
from media_house.modules.thumbnail_design.application.reactions import (
    SuggestReactions,
    TaggedReactions,
)
from media_house.modules.thumbnail_design.infrastructure.pillow_renderer import (
    PillowThumbnailRenderer,
)
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_asset_generation.application.contracts import (
    FrameGrabber,
    ImageAdjuster,
)
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    VisualStyleCatalog,
)
from media_house.shared.filesystem import AppPaths


class ThumbnailDesignModule:
    name: str = "thumbnail_design"

    def register(self, container: Container) -> None:
        container.register_factory(ThumbnailRenderer, lambda _c: PillowThumbnailRenderer())
        container.register_factory(
            ThumbnailDesigner,
            lambda c: DesignThumbnails(
                library=c.resolve(MediaLibrary),
                paths=c.resolve(AppPaths),
                clock=c.resolve(Clock),
                adjuster=c.resolve(ImageAdjuster),
                frames=c.resolve(FrameGrabber),
                styles=c.resolve(VisualStyleCatalog),
                fonts=c.resolve(FontFiles),
                renderer=c.resolve(ThumbnailRenderer),
            ),
        )
        container.register_factory(
            ReactionSuggester,
            lambda c: SuggestReactions(
                c.resolve(MediaLibrary), c.resolve(VideoCatalog), c.resolve(FrameGrabber)
            ),
        )
        container.register_factory(
            ReactionCollection, lambda c: TaggedReactions(c.resolve(MediaLibrary))
        )
        container.register_factory(
            ThumbnailExporter,
            lambda c: ExportThumbnail(c.resolve(MediaLibrary), c.resolve(ThumbnailRenderer)),
        )
