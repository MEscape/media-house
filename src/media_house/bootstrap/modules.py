"""The product's module list. Adding a feature module = one import + one line here."""

from collections.abc import Sequence

from media_house.core.modules import ApplicationModule
from media_house.modules.audio_improvement.module import AudioImprovementModule
from media_house.modules.audio_intelligence.module import AudioIntelligenceModule
from media_house.modules.image_adjustment.module import ImageAdjustmentModule
from media_house.modules.media_inspection.module import MediaInspectionModule
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.workspace.module import WorkspaceModule


def installed_modules() -> Sequence[ApplicationModule]:
    return (
        WorkspaceModule(),
        MediaLibraryModule(),
        ImageAdjustmentModule(),
        AudioIntelligenceModule(),
        AudioImprovementModule(),
        MediaInspectionModule(),
    )
