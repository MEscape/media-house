"""Module registration: how Pacing Intelligence plugs into the application.

It owns no storage and no UI: pacing analyses are JSON documents derived from their first recording
in the Media Library. Every input comes from the public contract of the module that owns it (audio,
content, video, alignment, the stored inspection, and the story plan when one exists); nothing is
recomputed here and the story planner is never asked. The editing stage reads the result through
the ``PacingCatalog`` contract. Pipelines and the GUI call ``PacingAnalyzer`` from a job.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.content_intelligence.application.contracts import ContentCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.pacing_intelligence.application.analyze_pacing import AnalyzePacing
from media_house.modules.pacing_intelligence.application.contracts import (
    PacingAnalyzer,
    PacingCatalog,
)
from media_house.modules.pacing_intelligence.application.results import PacingResults
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.shared.filesystem import AppPaths


class PacingIntelligenceModule:
    name: str = "pacing_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            AnalyzePacing,
            lambda c: AnalyzePacing(
                c.resolve(MediaLibrary),
                c.resolve(StoryCatalog),
                c.resolve(ContentCatalog),
                c.resolve(AudioCatalog),
                c.resolve(VideoCatalog),
                c.resolve(AlignmentCatalog),
                c.resolve(InspectionCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(PacingAnalyzer, lambda c: c.resolve(AnalyzePacing))
        container.register_factory(PacingResults, lambda c: PacingResults(c.resolve(MediaLibrary)))
        container.register_factory(PacingCatalog, lambda c: c.resolve(PacingResults))
