"""Module registration: how the VFX Engine plugs into the application.

Plans are JSON documents derived from the edit's first recording; rendered clips are derived
``vfx_render`` assets of their sources. Upstream facts come only through catalogs (video
analysis, reframing, inspection, visual style). FFmpeg runs through the ``ProcessRunner`` port;
no matting or inpainting model ships, so those ports have honest ``not_available`` adapters.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.reframing_intelligence.application.contracts import ReframingCatalog
from media_house.modules.vfx_engine.application.contracts import (
    EffectRenderer,
    Inpainter,
    SubjectMatter,
    VfxCatalog,
    VfxPlanner,
)
from media_house.modules.vfx_engine.application.plan_vfx import PlanVfx
from media_house.modules.vfx_engine.application.results import VfxResults
from media_house.modules.vfx_engine.infrastructure.ffmpeg_renderer import FfmpegEffectRenderer
from media_house.modules.vfx_engine.infrastructure.unavailable import (
    UnavailableInpainter,
    UnavailableSubjectMatter,
)
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_style_engine.application.contracts import VisualStyleCatalog
from media_house.shared.filesystem import AppPaths


class VfxEngineModule:
    name: str = "vfx_engine"

    def register(self, container: Container) -> None:
        container.register_factory(
            EffectRenderer, lambda c: FfmpegEffectRenderer(c.resolve(ProcessRunner))
        )
        container.register_factory(SubjectMatter, lambda c: UnavailableSubjectMatter())
        container.register_factory(Inpainter, lambda c: UnavailableInpainter())
        container.register_factory(
            PlanVfx,
            lambda c: PlanVfx(
                c.resolve(MediaLibrary),
                c.resolve(VideoCatalog),
                c.resolve(InspectionCatalog),
                c.resolve(ReframingCatalog),
                c.resolve(VisualStyleCatalog),
                c.resolve(EffectRenderer),
                c.resolve(SubjectMatter),
                c.resolve(Inpainter),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(AudioCatalog),
                c.resolve(AlignmentCatalog),
            ),
        )
        container.register_factory(VfxPlanner, lambda c: c.resolve(PlanVfx))
        container.register_factory(VfxResults, lambda c: VfxResults(c.resolve(MediaLibrary)))
        container.register_factory(VfxCatalog, lambda c: c.resolve(VfxResults))
