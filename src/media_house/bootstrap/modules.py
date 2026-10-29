"""The product's module list. Adding a feature module = one import + one line here."""

from collections.abc import Sequence

from media_house.core.modules import ApplicationModule
from media_house.modules.asset_relationships.module import AssetRelationshipsModule
from media_house.modules.audio_improvement.module import AudioImprovementModule
from media_house.modules.audio_intelligence.module import AudioIntelligenceModule
from media_house.modules.audio_visual_alignment.module import AudioVisualAlignmentModule
from media_house.modules.broll_intelligence.module import BrollIntelligenceModule
from media_house.modules.content_intelligence.module import ContentIntelligenceModule
from media_house.modules.editing_intelligence.module import EditingIntelligenceModule
from media_house.modules.image_adjustment.module import ImageAdjustmentModule
from media_house.modules.media_inspection.module import MediaInspectionModule
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.media_pipeline.module import MediaPipelineModule
from media_house.modules.pacing_intelligence.module import PacingIntelligenceModule
from media_house.modules.proxy_manager.module import ProxyManagerModule
from media_house.modules.story_architecture.module import StoryArchitectureModule
from media_house.modules.video_improvement.module import VideoImprovementModule
from media_house.modules.video_intelligence.module import VideoIntelligenceModule
from media_house.modules.workspace.module import WorkspaceModule


def installed_modules() -> Sequence[ApplicationModule]:
    return (
        WorkspaceModule(),
        MediaLibraryModule(),
        ImageAdjustmentModule(),
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
        PacingIntelligenceModule(),
        BrollIntelligenceModule(),
        EditingIntelligenceModule(),
    )
