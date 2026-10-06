"""Module registration: how Image Adjustment plugs into the application.

It owns no storage: results are stored through the Media Library contract. No UI yet; the GUI
will call the ``ImageAdjuster`` contract from a job.
"""

from media_house.core.modules import Container
from media_house.modules.image_adjustment.application.adjust_image import AdjustImage
from media_house.modules.image_adjustment.application.contracts import ImageAdjuster
from media_house.modules.image_adjustment.application.ports import ImageRenderer
from media_house.modules.image_adjustment.infrastructure.pillow_renderer import (
    PillowImageRenderer,
    Segmenter,
)
from media_house.modules.image_adjustment.infrastructure.rembg_segmenter import RembgSegmenter
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.filesystem import AppPaths


class ImageAdjustmentModule:
    name: str = "image_adjustment"

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
