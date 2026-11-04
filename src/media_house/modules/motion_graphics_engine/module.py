"""Module registration: how the Motion Graphics Engine plugs into the application.

No storage of its own: plans are ``graphics_plan`` documents and renders are
``motion_graphic_render`` derived assets of the edit's anchor in the Media Library. The
renderer is a pinned HyperFrames install in the app cache (``scripts/setup_hyperframes.py``);
without it every render reports ``not_available`` with that setup command.
"""

from pathlib import Path

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import (
    GraphicsCatalog,
    GraphicsRenderer,
    MotionGraphicsPlanner,
)
from media_house.modules.motion_graphics_engine.application.plan_graphics import PlanGraphics
from media_house.modules.motion_graphics_engine.application.render_graphics import (
    GraphicsProducer,
    RenderGraphics,
)
from media_house.modules.motion_graphics_engine.application.results import GraphicsResults
from media_house.modules.motion_graphics_engine.infrastructure.font_metrics import (
    SfntFontMetrics,
)
from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import (
    HyperFramesBackend,
    install_dir,
)
from media_house.modules.motion_graphics_engine.infrastructure.render_probe import (
    FfmpegRenderProbe,
)
from media_house.modules.visual_asset_generation.application.contracts import (
    VisualAssetPreparer,
)
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.filesystem import AppPaths


class MotionGraphicsEngineModule:
    name: str = "motion_graphics_engine"

    def __init__(self, hyperframes_dir: Path | None = None) -> None:
        #: Where the pinned HyperFrames runtime lives (default: the app cache).
        self._hyperframes_dir = hyperframes_dir

    def register(self, container: Container) -> None:
        def producer(c: Container) -> GraphicsProducer:
            paths = c.resolve(AppPaths)
            runner = c.resolve(ProcessRunner)
            return GraphicsProducer(
                c.resolve(MediaLibrary),
                c.resolve(VisualStyleCatalog),
                c.resolve(StyleValidator),
                c.resolve(FontFiles),
                SfntFontMetrics(),
                HyperFramesBackend(runner, self._hyperframes_dir or install_dir(paths)),
                FfmpegRenderProbe(runner),
                paths,
            )

        container.register_factory(GraphicsProducer, producer)
        container.register_factory(
            RenderGraphics,
            lambda c: RenderGraphics(c.resolve(MediaLibrary), c.resolve(GraphicsProducer)),
        )
        container.register_factory(GraphicsRenderer, lambda c: c.resolve(RenderGraphics))
        container.register_factory(
            PlanGraphics,
            lambda c: PlanGraphics(
                c.resolve(MediaLibrary),
                c.resolve(GraphicsProducer),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(VisualAssetPreparer),
            ),
        )
        container.register_factory(MotionGraphicsPlanner, lambda c: c.resolve(PlanGraphics))
        container.register_factory(
            GraphicsResults, lambda c: GraphicsResults(c.resolve(MediaLibrary))
        )
        container.register_factory(GraphicsCatalog, lambda c: c.resolve(GraphicsResults))
