"""Module registration: how the Media Pipeline plugs into the application.

It owns no storage and no UI: it only orchestrates the contracts of the other modules.
"""

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.core.modules import Container
from media_house.modules.asset_relationships.application.contracts import RelationshipDetection
from media_house.modules.audio_improvement.application.contracts import AudioImprover
from media_house.modules.audio_intelligence.application.contracts import AudioAnalyzer
from media_house.modules.audio_mix_engine.application.contracts import AudioMixer, MixAttacher
from media_house.modules.audio_visual_alignment.application.contracts import AudioVisualAligner
from media_house.modules.broll_intelligence.application.contracts import BrollAnalyzer
from media_house.modules.caption_engine.application.contracts import CaptionPlanner
from media_house.modules.content_intelligence.application.contracts import ContentAnalyzer
from media_house.modules.continuity_intelligence.application.contracts import ContinuityAnalyzer
from media_house.modules.edit_plan_compiler.application.contracts import TimelineCompiler
from media_house.modules.editing_intelligence.application.contracts import EditPlanner
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import MediaPipeline
from media_house.modules.media_pipeline.application.executors import ModuleExecutors
from media_house.modules.media_pipeline.application.run_pipeline import RunPipeline
from media_house.modules.motion_graphics_engine.application.contracts import MotionGraphicsPlanner
from media_house.modules.music_and_sound_design.application.contracts import (
    SoundDesignPlanner,
    SoundStemRenderer,
)
from media_house.modules.pacing_intelligence.application.contracts import PacingAnalyzer
from media_house.modules.proxy_manager.application.contracts import ProxyManager
from media_house.modules.quality_control.application.contracts import QualityAnalyzer
from media_house.modules.reframing_intelligence.application.contracts import ReframingAnalyzer
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveExecutor,
    ResolveRenderer,
)
from media_house.modules.story_architecture.application.contracts import StoryPlanner
from media_house.modules.thumbnail_design.application.contracts import (
    ReactionCollection,
    ReactionSuggester,
    ThumbnailDesigner,
)
from media_house.modules.timeline_builder.application.contracts import TimelineBuilder
from media_house.modules.transition_engine.application.contracts import TransitionPlanner
from media_house.modules.vfx_engine.application.contracts import VfxPlanner
from media_house.modules.video_improvement.application.contracts import VideoImprover
from media_house.modules.video_intelligence.application.contracts import VideoAnalyzer
from media_house.modules.visual_style_engine.application.contracts import StyleResolver


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
                    reframing_analyzer=c.resolve(ReframingAnalyzer),
                    edit_planner=c.resolve(EditPlanner),
                    relationships=c.resolve(RelationshipDetection),
                    style_resolver=c.resolve(StyleResolver),
                    graphics_planner=c.resolve(MotionGraphicsPlanner),
                    caption_planner=c.resolve(CaptionPlanner),
                    transition_planner=c.resolve(TransitionPlanner),
                    vfx_planner=c.resolve(VfxPlanner),
                    sound_planner=c.resolve(SoundDesignPlanner),
                    stem_renderer=c.resolve(SoundStemRenderer),
                    timeline_compiler=c.resolve(TimelineCompiler),
                    timeline_builder=c.resolve(TimelineBuilder),
                    resolve_executor=c.resolve(ResolveExecutor),
                    resolve_renderer=c.resolve(ResolveRenderer),
                    audio_mixer=c.resolve(AudioMixer),
                    mix_attacher=c.resolve(MixAttacher),
                    quality_analyzer=c.resolve(QualityAnalyzer),
                    settings=settings,
                    thumbnail_designer=c.resolve(ThumbnailDesigner),
                    reaction_suggester=c.resolve(ReactionSuggester),
                    media_analyzer=c.resolve(MediaAnalyzer),
                    reactions=c.resolve(ReactionCollection),
                ),
            ),
        )
        container.register_factory(MediaPipeline, lambda c: c.resolve(RunPipeline))
