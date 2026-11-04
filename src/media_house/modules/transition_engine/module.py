"""Module registration: how the Transition Engine plugs into the application.

No storage of its own: plans are ``transition_plan`` documents of the edit's anchor asset in the
Media Library; graphic overlays are ``motion_graphic_render`` assets made by
motion_graphics_engine (which must be installed: ``GraphicsRenderer``). Every input comes from
its owner's public contract; nothing is probed, analysed or rendered here.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.continuity_intelligence.application.contracts import ContinuityCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import GraphicsRenderer
from media_house.modules.pacing_intelligence.application.contracts import PacingCatalog
from media_house.modules.transition_engine.application.contracts import (
    TransitionCatalog,
    TransitionPlanner,
)
from media_house.modules.transition_engine.application.plan_transitions import PlanTransitions
from media_house.modules.transition_engine.application.results import TransitionResults
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_style_engine.application.contracts import (
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.filesystem import AppPaths


class TransitionEngineModule:
    name: str = "transition_engine"

    def register(self, container: Container) -> None:
        container.register_factory(
            PlanTransitions,
            lambda c: PlanTransitions(
                c.resolve(MediaLibrary),
                c.resolve(InspectionCatalog),
                c.resolve(AudioCatalog),
                c.resolve(VideoCatalog),
                c.resolve(ContinuityCatalog),
                c.resolve(PacingCatalog),
                c.resolve(VisualStyleCatalog),
                c.resolve(StyleValidator),
                c.resolve(GraphicsRenderer),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(TransitionPlanner, lambda c: c.resolve(PlanTransitions))
        container.register_factory(
            TransitionResults, lambda c: TransitionResults(c.resolve(MediaLibrary))
        )
        container.register_factory(TransitionCatalog, lambda c: c.resolve(TransitionResults))
