"""Module registration: how the Media Pipeline plugs into the application.

It owns no storage and no UI: it only orchestrates the contracts of the other modules.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.asset_relationships.application.contracts import RelationshipDetection
from media_house.modules.audio_improvement.application.contracts import AudioImprover
from media_house.modules.audio_intelligence.application.contracts import AudioAnalyzer
from media_house.modules.audio_visual_alignment.application.contracts import AudioVisualAligner
from media_house.modules.broll_intelligence.application.contracts import BrollAnalyzer
from media_house.modules.content_intelligence.application.contracts import ContentAnalyzer
from media_house.modules.continuity_intelligence.application.contracts import ContinuityAnalyzer
from media_house.modules.editing_intelligence.application.contracts import EditPlanner
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import MediaPipeline
from media_house.modules.media_pipeline.application.executors import ModuleExecutors
from media_house.modules.media_pipeline.application.run_pipeline import RunPipeline
from media_house.modules.pacing_intelligence.application.contracts import PacingAnalyzer
from media_house.modules.proxy_manager.application.contracts import ProxyManager
from media_house.modules.story_architecture.application.contracts import StoryPlanner
from media_house.modules.video_improvement.application.contracts import VideoImprover
from media_house.modules.video_intelligence.application.contracts import VideoAnalyzer


class MediaPipelineModule:
    name: str = "media_pipeline"

    def register(self, container: Container) -> None:
        container.register_factory(
            RunPipeline,
            lambda c: RunPipeline(
                c.resolve(MediaLibrary),
                lambda settings: ModuleExecutors(
                    library=c.resolve(MediaLibrary),
                    clock=c.resolve(Clock),
                    inspector=c.resolve(MediaInspector),
                    proxies=c.resolve(ProxyManager),
                    audio_improver=c.resolve(AudioImprover),
                    video_improver=c.resolve(VideoImprover),
                    audio_analyzer=c.resolve(AudioAnalyzer),
                    video_analyzer=c.resolve(VideoAnalyzer),
                    content_analyzer=c.resolve(ContentAnalyzer),
                    aligner=c.resolve(AudioVisualAligner),
                    story_planner=c.resolve(StoryPlanner),
                    pacing_analyzer=c.resolve(PacingAnalyzer),
                    broll_analyzer=c.resolve(BrollAnalyzer),
                    continuity_analyzer=c.resolve(ContinuityAnalyzer),
                    edit_planner=c.resolve(EditPlanner),
                    relationships=c.resolve(RelationshipDetection),
                    settings=settings,
                ),
            ),
        )
        container.register_factory(MediaPipeline, lambda c: c.resolve(RunPipeline))
