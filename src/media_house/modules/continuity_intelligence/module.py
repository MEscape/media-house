"""Module registration: how Continuity Intelligence plugs into the application.

It owns no storage and no UI: analyses are JSON documents derived from the first recording of the
judged sequence in the Media Library. Every input comes from the public contract of the module that
owns it (video, audio, alignment, relationships, the B-roll analysis); nothing is recomputed here
and the edit plan is never imported: the caller hands in a ``ProposedSequence``. The editing stage
reads the result through the ``ContinuityCatalog`` contract. Pipelines and the GUI call
``ContinuityAnalyzer`` from a job.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.asset_relationships.application.contracts import RelationshipCatalog
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.broll_intelligence.application.contracts import BrollCatalog
from media_house.modules.continuity_intelligence.application.analyze_continuity import (
    AnalyzeContinuity,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    ContinuityAnalyzer,
    ContinuityCatalog,
)
from media_house.modules.continuity_intelligence.application.results import ContinuityResults
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.shared.filesystem import AppPaths


class ContinuityIntelligenceModule:
    name: str = "continuity_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            AnalyzeContinuity,
            lambda c: AnalyzeContinuity(
                c.resolve(MediaLibrary),
                c.resolve(VideoCatalog),
                c.resolve(AudioCatalog),
                c.resolve(AlignmentCatalog),
                c.resolve(RelationshipCatalog),
                c.resolve(BrollCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(ContinuityAnalyzer, lambda c: c.resolve(AnalyzeContinuity))
        container.register_factory(
            ContinuityResults, lambda c: ContinuityResults(c.resolve(MediaLibrary))
        )
        container.register_factory(ContinuityCatalog, lambda c: c.resolve(ContinuityResults))
