"""The real application, with only the AI model of background removal replaced.

``Lab`` builds the use cases from the real container's services (library, inspection catalog,
FFmpeg extractor, measurer, exporter, enhancer, b-roll catalog) but renders cutouts with
``ColourSegmenter``: no model is downloaded on this machine, and tests must not download one.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.application.ports import Clock
from media_house.modules.broll_intelligence.application.contracts import BrollCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.visual_asset_generation.application.adjust_image import AdjustImage
from media_house.modules.visual_asset_generation.application.derive import Deriver
from media_house.modules.visual_asset_generation.application.generate_image import GenerateImage
from media_house.modules.visual_asset_generation.application.grab_frame import GrabFrame
from media_house.modules.visual_asset_generation.application.ports import (
    CutoutEnhancer,
    FrameExtractor,
    ImageExporter,
    ImageGenerator,
    ImageMeasurer,
)
from media_house.modules.visual_asset_generation.application.prepare_visual_assets import (
    PrepareVisualAssets,
)
from media_house.modules.visual_asset_generation.application.select_images import SelectImages
from media_house.modules.visual_asset_generation.infrastructure.pillow_renderer import (
    PillowImageRenderer,
)
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.support.vag_fakes import ColourSegmenter, product_png


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield application
    application.shutdown()


@dataclass
class Lab:
    app: Application
    segmenter: ColourSegmenter
    tmp: Path

    def resolve[T](self, key: type[T]) -> T:
        return self.app.container.resolve(key)

    @property
    def library(self) -> MediaLibrary:
        return self.resolve(MediaLibrary)

    def deriver(self) -> Deriver:
        return Deriver(self.library, self.resolve(AppPaths), self.resolve(ImageMeasurer))

    def grabber(self) -> GrabFrame:
        return GrabFrame(
            self.library,
            self.resolve(InspectionCatalog),
            self.resolve(FrameExtractor),
            self.deriver(),
        )

    def preparer(self) -> PrepareVisualAssets:
        return PrepareVisualAssets(
            self.library,
            self.resolve(AppPaths),
            self.resolve(Clock),
            self.deriver(),
            self.grabber(),
            PillowImageRenderer(self.segmenter),
            self.resolve(CutoutEnhancer),
            self.resolve(ImageExporter),
            None,
        )

    def adjuster(self) -> AdjustImage:
        return AdjustImage(
            self.library, PillowImageRenderer(self.segmenter), self.resolve(AppPaths)
        )

    def selector(self, generator: ImageGenerator | None = None) -> SelectImages:
        return SelectImages(
            self.library, self.resolve(BrollCatalog), generator or self.resolve(ImageGenerator)
        )

    def generation(self, generator: ImageGenerator) -> GenerateImage:
        return GenerateImage(
            self.library,
            self.resolve(AppPaths),
            self.resolve(Clock),
            self.selector(generator),
            generator,
            self.resolve(ImageMeasurer),
        )

    def product(self, name: str, **kwargs: object) -> str:
        path = product_png(self.tmp / f"{name}.png", **kwargs)  # type: ignore[arg-type]
        imported = self.library.import_file(path, display_name=name)
        assert isinstance(imported, Ok), imported
        return imported.value.asset.id


@pytest.fixture
def lab(app: Application, tmp_path: Path) -> Lab:
    sources = tmp_path / "sources"
    sources.mkdir()
    return Lab(app=app, segmenter=ColourSegmenter(), tmp=sources)
