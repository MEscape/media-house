"""Module registration: the Control Center owns no data and registers only its UI.

Every service it uses is another module's public contract, resolved lazily when a page is first
opened (after the desktop bootstrap registered ``UiJobRunner`` / ``StatusReporter``).
"""

from media_house.core.modules import Container
from media_house.modules.control_center.application.resolve_status import CheckResolveStatus
from media_house.modules.control_center.application.review_workspace import ReviewWorkspace
from media_house.modules.control_center.application.sound_library import SoundLibraryService
from media_house.modules.control_center.application.thumbnail_studio import ThumbnailStudio
from media_house.modules.control_center.presentation.contributor import ControlCenterUiContributor
from media_house.modules.control_center.presentation.viewmodels.brief_to_cut_viewmodel import (
    BriefToCutViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.resolve_viewmodel import (
    ResolveViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.review_viewmodel import (
    ReviewViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.sound_library_viewmodel import (
    SoundLibraryViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.style_review_viewmodel import (
    StyleReviewViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.thumbnail_viewmodel import (
    ThumbnailViewModel,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.music_and_sound_design.application.contracts import (
    SoundLibraryCatalog,
    SoundLibraryImporter,
)
from media_house.modules.production_project.application.contracts import ProductionWorkflow
from media_house.modules.render_manager.application.contracts import RenderProfiles
from media_house.modules.resolve_adapter.application.contracts import ResolveInspector
from media_house.modules.resolve_export.application.contracts import ExportApprovalService
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSessions,
)
from media_house.modules.revision_engine.application.contracts import (
    RevisionCatalog,
    RevisionDecider,
)
from media_house.modules.style_memory.application.contracts import StyleReview
from media_house.modules.thumbnail_design.application.contracts import (
    ReactionCollection,
    ReactionSuggester,
    ThumbnailDesigner,
    ThumbnailExporter,
)
from media_house.presentation.extension import UiContributor
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter


class ControlCenterModule:
    name: str = "control_center"

    def register(self, container: Container) -> None:
        container.add_to_collection(
            UiContributor,
            lambda c: ControlCenterUiContributor(
                resolve=lambda: ResolveViewModel(
                    CheckResolveStatus(c.resolve(ResolveInspector)),
                    c.resolve(UiJobRunner),
                    c.resolve(StatusReporter),
                ),
                sounds=lambda: SoundLibraryViewModel(
                    SoundLibraryService(
                        c.resolve(SoundLibraryCatalog), c.resolve(SoundLibraryImporter)
                    ),
                    c.resolve(UiJobRunner),
                    c.resolve(StatusReporter),
                ),
                review=lambda: ReviewViewModel(
                    _review_workspace(c), c.resolve(UiJobRunner), c.resolve(StatusReporter)
                ),
                brief_to_cut=lambda: BriefToCutViewModel(
                    c.resolve(ProductionWorkflow),
                    c.resolve(UiJobRunner),
                    c.resolve(StatusReporter),
                ),
                style_review=lambda: StyleReviewViewModel(
                    c.resolve(StyleReview), c.resolve(UiJobRunner)
                ),
                thumbnail=lambda: ThumbnailViewModel(
                    ThumbnailStudio(
                        library=c.resolve(MediaLibrary),
                        designer=c.resolve(ThumbnailDesigner),
                        suggester=c.resolve(ReactionSuggester),
                        reactions=c.resolve(ReactionCollection),
                        exporter=c.resolve(ThumbnailExporter),
                    ),
                    c.resolve(UiJobRunner),
                    c.resolve(StatusReporter),
                ),
            ),
        )


def _review_workspace(c: Container) -> ReviewWorkspace:
    return ReviewWorkspace(
        library=c.resolve(MediaLibrary),
        reviews=c.resolve(ReviewCatalog),
        sessions=c.resolve(ReviewSessions),
        revisions=c.resolve(RevisionCatalog),
        decider=c.resolve(RevisionDecider),
        approvals=c.resolve(ExportApprovalService),
        profiles=c.resolve(RenderProfiles),
    )
