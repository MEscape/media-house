"""Module registration: how Reframing Intelligence plugs into the application.

It owns no storage and no UI: analyses are JSON documents derived from the first recording of the
analysed set in the Media Library. Every input comes from the public contract of the module that
owns it (the picture analysis, the stored inspection, the alignment, the story plan, the
continuity analysis); nothing is detected, probed or rendered here. The editing stage reads the
result through the ``ReframingCatalog`` contract. Pipelines and the GUI call ``ReframingAnalyzer``
from a job.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.continuity_intelligence.application.contracts import ContinuityCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.reframing_intelligence.application.analyze_reframing import (
    AnalyzeReframing,
)
from media_house.modules.reframing_intelligence.application.contracts import (
    ReframingAnalyzer,
    ReframingCatalog,
)
from media_house.modules.reframing_intelligence.application.results import ReframingResults
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.shared.filesystem import AppPaths


class ReframingIntelligenceModule:
    name: str = "reframing_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            AnalyzeReframing,
            lambda c: AnalyzeReframing(
                c.resolve(MediaLibrary),
                c.resolve(VideoCatalog),
                c.resolve(InspectionCatalog),
                c.resolve(AlignmentCatalog),
                c.resolve(StoryCatalog),
                c.resolve(ContinuityCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(ReframingAnalyzer, lambda c: c.resolve(AnalyzeReframing))
        container.register_factory(
            ReframingResults, lambda c: ReframingResults(c.resolve(MediaLibrary))
        )
        container.register_factory(ReframingCatalog, lambda c: c.resolve(ReframingResults))
