"""Module registration: how Visual Asset Generation plugs into the application.

It owns no storage: pictures and sets are stored through the Media Library contract. No UI;
callers use the contracts from a job. The visual style catalog is optional: without the
``visual_style_engine`` module the presets keep their explicit profile values (and say so).
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container, DependencyError
from media_house.modules.broll_intelligence.application.contracts import BrollCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.visual_asset_generation.application.adjust_image import AdjustImage
from media_house.modules.visual_asset_generation.application.contracts import (
    FrameGrabber,
    ImageAdjuster,
    ImageGenerationService,
    ImageSelector,
    VisualAssetCatalog,
    VisualAssetPreparer,
)
from media_house.modules.visual_asset_generation.application.derive import Deriver
from media_house.modules.visual_asset_generation.application.generate_image import GenerateImage
from media_house.modules.visual_asset_generation.application.grab_frame import GrabFrame
from media_house.modules.visual_asset_generation.application.ports import (
    CutoutEnhancer,
    FrameExtractor,
    ImageExporter,
    ImageGenerator,
    ImageMeasurer,
    ImageRenderer,
)
from media_house.modules.visual_asset_generation.application.prepare_visual_assets import (
    PrepareVisualAssets,
)
from media_house.modules.visual_asset_generation.application.results import VisualAssetResults
from media_house.modules.visual_asset_generation.application.select_images import SelectImages
from media_house.modules.visual_asset_generation.infrastructure.enhancer import (
    PillowCutoutEnhancer,
)
from media_house.modules.visual_asset_generation.infrastructure.exporter import (
    PillowImageExporter,
)
from media_house.modules.visual_asset_generation.infrastructure.ffmpeg_frames import (
    FfmpegFrameExtractor,
)
from media_house.modules.visual_asset_generation.infrastructure.measure import PillowImageMeasurer
from media_house.modules.visual_asset_generation.infrastructure.pillow_renderer import (
    PillowImageRenderer,
    Segmenter,
)
from media_house.modules.visual_asset_generation.infrastructure.rembg_segmenter import (
    RembgSegmenter,
)
from media_house.modules.visual_asset_generation.infrastructure.unavailable_generator import (
    UnavailableImageGenerator,
)
from media_house.modules.visual_style_engine.application.contracts import VisualStyleCatalog
from media_house.shared.filesystem import AppPaths


def _style_catalog(container: Container) -> VisualStyleCatalog | None:
    try:
        return container.resolve(VisualStyleCatalog)
    except DependencyError:
        return None


class VisualAssetGenerationModule:
    name: str = "visual_asset_generation"

    def register(self, container: Container) -> None:
        container.register_factory(Segmenter, lambda _c: RembgSegmenter())
        container.register_factory(
            ImageRenderer,
            lambda c: PillowImageRenderer(c.resolve(Segmenter)),
        )
        container.register_factory(
            AdjustImage,
            lambda c: AdjustImage(
                c.resolve(MediaLibrary),
                c.resolve(ImageRenderer),
                c.resolve(AppPaths),
            ),
        )
        container.register_factory(ImageAdjuster, lambda c: c.resolve(AdjustImage))
        container.register_factory(CutoutEnhancer, lambda _c: PillowCutoutEnhancer())
        container.register_factory(ImageMeasurer, lambda _c: PillowImageMeasurer())
        container.register_factory(ImageExporter, lambda _c: PillowImageExporter())
        container.register_factory(
            FrameExtractor, lambda c: FfmpegFrameExtractor(c.resolve(ProcessRunner))
        )
        container.register_factory(ImageGenerator, lambda _c: UnavailableImageGenerator())
        container.register_factory(
            Deriver,
            lambda c: Deriver(
                c.resolve(MediaLibrary), c.resolve(AppPaths), c.resolve(ImageMeasurer)
            ),
        )
        container.register_factory(
            GrabFrame,
            lambda c: GrabFrame(
                c.resolve(MediaLibrary),
                c.resolve(InspectionCatalog),
                c.resolve(FrameExtractor),
                c.resolve(Deriver),
            ),
        )
        container.register_factory(FrameGrabber, lambda c: c.resolve(GrabFrame))
        container.register_factory(
            PrepareVisualAssets,
            lambda c: PrepareVisualAssets(
                c.resolve(MediaLibrary),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(Deriver),
                c.resolve(GrabFrame),
                c.resolve(ImageRenderer),
                c.resolve(CutoutEnhancer),
                c.resolve(ImageExporter),
                _style_catalog(c),
            ),
        )
        container.register_factory(VisualAssetPreparer, lambda c: c.resolve(PrepareVisualAssets))
        container.register_factory(
            SelectImages,
            lambda c: SelectImages(
                c.resolve(MediaLibrary), c.resolve(BrollCatalog), c.resolve(ImageGenerator)
            ),
        )
        container.register_factory(ImageSelector, lambda c: c.resolve(SelectImages))
        container.register_factory(
            GenerateImage,
            lambda c: GenerateImage(
                c.resolve(MediaLibrary),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(SelectImages),
                c.resolve(ImageGenerator),
                c.resolve(ImageMeasurer),
            ),
        )
        container.register_factory(ImageGenerationService, lambda c: c.resolve(GenerateImage))
        container.register_factory(
            VisualAssetResults, lambda c: VisualAssetResults(c.resolve(MediaLibrary))
        )
        container.register_factory(VisualAssetCatalog, lambda c: c.resolve(VisualAssetResults))
