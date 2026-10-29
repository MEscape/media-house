"""Module registration: how Editing Intelligence plugs into the application.

It owns no storage and no UI: edit plans are JSON documents derived from their first recording in
the Media Library. Every input comes from the public contract of the module that owns it (the
story plan, content, audio, video, alignment, the stored inspection); nothing is recomputed here
and the story planner is only asked when a caller requests a missing story plan. The pacing
analysis, the B-roll analysis and the continuity analysis are read through ``PacingCatalog``,
``BrollCatalog`` and ``ContinuityCatalog``; no advisor is wired: the baseline rules stand
where an analysis is missing, and ``Advisors`` stays the place a later module plugs in.
Pipelines and the GUI call the ``EditPlanner`` contract from a job.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.broll_intelligence.application.contracts import BrollCatalog
from media_house.modules.content_intelligence.application.contracts import ContentCatalog
from media_house.modules.continuity_intelligence.application.contracts import ContinuityCatalog
from media_house.modules.editing_intelligence.application.contracts import EditCatalog, EditPlanner
from media_house.modules.editing_intelligence.application.plan_edit import PlanEdit
from media_house.modules.editing_intelligence.application.results import EditResults
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.pacing_intelligence.application.contracts import PacingCatalog
from media_house.modules.reframing_intelligence.application.contracts import (
    ReframingCatalog,
)
from media_house.modules.story_architecture.application.contracts import StoryCatalog, StoryPlanner
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.shared.filesystem import AppPaths


class EditingIntelligenceModule:
    name: str = "editing_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            PlanEdit,
            lambda c: PlanEdit(
                c.resolve(MediaLibrary),
                c.resolve(StoryPlanner),
                c.resolve(StoryCatalog),
                c.resolve(ContentCatalog),
                c.resolve(AudioCatalog),
                c.resolve(VideoCatalog),
                c.resolve(AlignmentCatalog),
                c.resolve(InspectionCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
                pacing=c.resolve(PacingCatalog),
                broll=c.resolve(BrollCatalog),
                continuity=c.resolve(ContinuityCatalog),
                reframing=c.resolve(ReframingCatalog),
            ),
        )
        container.register_factory(EditPlanner, lambda c: c.resolve(PlanEdit))
        container.register_factory(EditResults, lambda c: EditResults(c.resolve(MediaLibrary)))
        container.register_factory(EditCatalog, lambda c: c.resolve(EditResults))
