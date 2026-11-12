"""The product's module list. Adding a feature module = one import + one line here."""

from collections.abc import Sequence

from media_house.bootstrap.variant_runner import VariantRunnerModule
from media_house.core.modules import ApplicationModule
from media_house.modules.asset_relationships.module import AssetRelationshipsModule
from media_house.modules.audio_improvement.module import AudioImprovementModule
from media_house.modules.audio_intelligence.module import AudioIntelligenceModule
from media_house.modules.audio_mix_engine.module import AudioMixEngineModule
from media_house.modules.audio_visual_alignment.module import AudioVisualAlignmentModule
from media_house.modules.broll_intelligence.module import BrollIntelligenceModule
from media_house.modules.caption_engine.module import CaptionEngineModule
from media_house.modules.content_intelligence.module import ContentIntelligenceModule
from media_house.modules.continuity_intelligence.module import ContinuityIntelligenceModule
from media_house.modules.control_center.module import ControlCenterModule
from media_house.modules.edit_plan_compiler.module import EditPlanCompilerModule
from media_house.modules.editing_intelligence.module import EditingIntelligenceModule
from media_house.modules.export_validation.module import ExportValidationModule
from media_house.modules.final_polish.module import FinalPolishModule
from media_house.modules.media_inspection.module import MediaInspectionModule
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.media_pipeline.module import MediaPipelineModule
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
from media_house.modules.music_and_sound_design.module import MusicAndSoundDesignModule
from media_house.modules.pacing_intelligence.module import PacingIntelligenceModule
from media_house.modules.production_project.module import ProductionProjectModule
from media_house.modules.proxy_manager.module import ProxyManagerModule
from media_house.modules.quality_control.module import QualityControlModule
from media_house.modules.reframing_intelligence.module import ReframingIntelligenceModule
from media_house.modules.render_manager.module import RenderManagerModule
from media_house.modules.resolve_adapter.module import ResolveAdapterModule
from media_house.modules.resolve_export.module import ResolveExportModule
from media_house.modules.review_orchestrator.module import ReviewOrchestratorModule
from media_house.modules.revision_engine.module import RevisionEngineModule
from media_house.modules.story_architecture.module import StoryArchitectureModule
from media_house.modules.style_memory.module import StyleMemoryModule
from media_house.modules.thumbnail_design.module import ThumbnailDesignModule
from media_house.modules.timeline_builder.module import TimelineBuilderModule
from media_house.modules.transition_engine.module import TransitionEngineModule
from media_house.modules.vfx_engine.module import VfxEngineModule
from media_house.modules.video_improvement.module import VideoImprovementModule
from media_house.modules.video_intelligence.module import VideoIntelligenceModule
from media_house.modules.visual_asset_generation.module import VisualAssetGenerationModule
from media_house.modules.visual_style_engine.module import VisualStyleEngineModule
from media_house.modules.workspace.module import WorkspaceModule


def installed_modules() -> Sequence[ApplicationModule]:
    return (
        ControlCenterModule(),
        WorkspaceModule(),
        MediaLibraryModule(),
        VisualAssetGenerationModule(),
        ThumbnailDesignModule(),
        AudioIntelligenceModule(),
        AudioImprovementModule(),
        MediaInspectionModule(),
        VideoImprovementModule(),
        VideoIntelligenceModule(),
        ContentIntelligenceModule(),
        AudioVisualAlignmentModule(),
        ProxyManagerModule(),
        AssetRelationshipsModule(),
        MediaPipelineModule(),
        StoryArchitectureModule(),
        StyleMemoryModule(),
        ProductionProjectModule(),
        VariantRunnerModule(),
        PacingIntelligenceModule(),
        BrollIntelligenceModule(),
        ContinuityIntelligenceModule(),
        ReframingIntelligenceModule(),
        EditingIntelligenceModule(),
        VisualStyleEngineModule(),
        MusicAndSoundDesignModule(),
        VfxEngineModule(),
        MotionGraphicsEngineModule(),
        TransitionEngineModule(),
        CaptionEngineModule(),
        EditPlanCompilerModule(),
        TimelineBuilderModule(),
        ResolveAdapterModule(),
        AudioMixEngineModule(),
        ReviewOrchestratorModule(),
        QualityControlModule(),
        RevisionEngineModule(),
        FinalPolishModule(),
        RenderManagerModule(),
        ExportValidationModule(),
        ResolveExportModule(),
    )
