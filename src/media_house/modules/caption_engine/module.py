"""Module registration: how the Caption Engine plugs into the application.

No storage of its own: plans are ``caption_plan`` documents of the edit's anchor in the Media
Library; rendered caption groups are motion graphics' derived assets.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.caption_engine.application.contracts import (
    CaptionCatalog,
    CaptionPlanner,
)
from media_house.modules.caption_engine.application.plan_captions import PlanCaptions
from media_house.modules.caption_engine.application.results import CaptionResults
from media_house.modules.content_intelligence.application.contracts import ContentCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import (
    GraphicsCatalog,
    GraphicsRenderer,
)
from media_house.modules.reframing_intelligence.application.contracts import ReframingCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_style_engine.application.contracts import (
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.filesystem import AppPaths


class CaptionEngineModule:
    name: str = "caption_engine"

    def register(self, container: Container) -> None:
        container.register_factory(
            PlanCaptions,
            lambda c: PlanCaptions(
                c.resolve(MediaLibrary),
                c.resolve(AudioCatalog),
                c.resolve(ContentCatalog),
                c.resolve(VideoCatalog),
                c.resolve(ReframingCatalog),
                c.resolve(InspectionCatalog),
                c.resolve(GraphicsCatalog),
                c.resolve(VisualStyleCatalog),
                c.resolve(StyleValidator),
                c.resolve(GraphicsRenderer),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(CaptionPlanner, lambda c: c.resolve(PlanCaptions))
        container.register_factory(
            CaptionResults, lambda c: CaptionResults(c.resolve(MediaLibrary))
        )
        container.register_factory(CaptionCatalog, lambda c: c.resolve(CaptionResults))
