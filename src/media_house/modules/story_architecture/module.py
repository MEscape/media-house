"""Module registration: how Story Architecture plugs into the application.

It owns no storage and no UI: plans are JSON documents derived from their first source asset in the
Media Library. The content analysis and alignment come from the public contracts of Content
Intelligence and Audio-Visual Alignment (never recomputed here), and how a story's recordings
relate comes from Asset Relationships (read only; detection stays with that module). The optional
wording model is the one Content Intelligence already owns, reached through its ``TextGenerator``
contract; nothing is downloaded and a missing model only makes that stage ``not_available``.
Pipelines and the GUI call the ``StoryPlanner`` contract from a job.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.asset_relationships.application.contracts import RelationshipCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalyzer,
    ContentCatalog,
    TextGenerator,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.story_architecture.application.contracts import (
    StoryCatalog,
    StoryPlanner,
)
from media_house.modules.story_architecture.application.plan_story import PlanStory
from media_house.modules.story_architecture.application.results import StoryResults
from media_house.shared.filesystem import AppPaths


class StoryArchitectureModule:
    name: str = "story_architecture"

    def register(self, container: Container) -> None:
        container.register_factory(
            PlanStory,
            lambda c: PlanStory(
                c.resolve(MediaLibrary),
                c.resolve(ContentCatalog),
                c.resolve(ContentAnalyzer),
                c.resolve(AlignmentCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(TextGenerator),
                c.resolve(RelationshipCatalog),
            ),
        )
        container.register_factory(StoryPlanner, lambda c: c.resolve(PlanStory))
        container.register_factory(StoryResults, lambda c: StoryResults(c.resolve(MediaLibrary)))
        container.register_factory(StoryCatalog, lambda c: c.resolve(StoryResults))
