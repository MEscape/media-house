"""Module registration: how Media Inspection plugs into the application.

It owns no storage: inspections are JSON documents derived from their source asset in the Media
Library. No UI yet; pipelines and the GUI call the ``MediaInspector`` contract from a job.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import (
    InspectionCatalog,
    MediaInspector,
    MediaRelations,
)
from media_house.modules.media_inspection.application.inspect_media import InspectMedia
from media_house.modules.media_inspection.application.ports import MediaProber
from media_house.modules.media_inspection.application.related_media import FindRelatedMedia
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.filesystem import AppPaths


class MediaInspectionModule:
    name: str = "media_inspection"

    def register(self, container: Container) -> None:
        container.register_factory(MediaProber, lambda c: FfprobeProber(c.resolve(ProcessRunner)))
        container.register_factory(
            InspectMedia,
            lambda c: InspectMedia(
                c.resolve(MediaLibrary),
                c.resolve(MediaProber),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(MediaInspector, lambda c: c.resolve(InspectMedia))
        container.register_factory(InspectionCatalog, lambda c: c.resolve(InspectMedia))
        container.register_factory(
            FindRelatedMedia, lambda c: FindRelatedMedia(c.resolve(MediaLibrary))
        )
        container.register_factory(MediaRelations, lambda c: c.resolve(FindRelatedMedia))
