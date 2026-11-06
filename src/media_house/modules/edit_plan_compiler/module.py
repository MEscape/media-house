"""Module registration: how the Edit Plan Compiler plugs into the application.

No storage of its own: specifications are ``timeline_specification`` documents of the edit's
anchor asset in the Media Library. Every input comes from its owner's public contract; nothing
is probed, analysed, planned or rendered here.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.caption_engine.application.contracts import CaptionCatalog
from media_house.modules.edit_plan_compiler.application.compile_timeline import CompileTimeline
from media_house.modules.edit_plan_compiler.application.contracts import (
    TimelineCatalog,
    TimelineCompiler,
)
from media_house.modules.edit_plan_compiler.application.results import TimelineResults
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import GraphicsCatalog
from media_house.modules.music_and_sound_design.application.contracts import SoundDesignCatalog
from media_house.modules.reframing_intelligence.application.contracts import ReframingCatalog
from media_house.modules.transition_engine.application.contracts import TransitionCatalog
from media_house.modules.vfx_engine.application.contracts import VfxCatalog
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.filesystem import AppPaths


class EditPlanCompilerModule:
    name: str = "edit_plan_compiler"

    def register(self, container: Container) -> None:
        container.register_factory(
            CompileTimeline,
            lambda c: CompileTimeline(
                c.resolve(MediaLibrary),
                c.resolve(InspectionCatalog),
                c.resolve(VisualStyleCatalog),
                c.resolve(StyleValidator),
                c.resolve(FontFiles),
                c.resolve(GraphicsCatalog),
                c.resolve(CaptionCatalog),
                c.resolve(TransitionCatalog),
                c.resolve(VfxCatalog),
                c.resolve(SoundDesignCatalog),
                c.resolve(ReframingCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(TimelineCompiler, lambda c: c.resolve(CompileTimeline))
        container.register_factory(
            TimelineResults, lambda c: TimelineResults(c.resolve(MediaLibrary))
        )
        container.register_factory(TimelineCatalog, lambda c: c.resolve(TimelineResults))
