"""Module registration: how the final polish plugs into the application.

No storage of its own: reports are documents of the recording's anchor asset in the Media Library,
review frames are derived assets of the preview. It reads quality control, the audio mix, the
revisions and the review through their public contracts; pictures are measured through the shared
``MediaAnalyzer`` port.
"""

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.core.modules import Container
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.final_polish.application.contracts import FinalPolisher, PolishCatalog
from media_house.modules.final_polish.application.polish_final import PolishFinal
from media_house.modules.final_polish.application.store import PolishStore
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.quality_control.application.contracts import (
    QualityAnalyzer,
    QualityCatalog,
)
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSessions,
)
from media_house.modules.revision_engine.application.contracts import RevisionCatalog
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class FinalPolishModule:
    name: str = "final_polish"

    def register(self, container: Container) -> None:
        container.register_factory(
            PolishStore, lambda c: PolishStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            PolishFinal,
            lambda c: PolishFinal(
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(QualityCatalog),
                c.resolve(QualityAnalyzer),
                c.resolve(MixCatalog),
                c.resolve(RevisionCatalog),
                c.resolve(ReviewSessions),
                c.resolve(ReviewCatalog),
                c.resolve(MediaAnalyzer),
                c.resolve(PolishStore),
                c.resolve(EventPublisher),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(FinalPolisher, lambda c: c.resolve(PolishFinal))
        container.register_factory(PolishCatalog, lambda c: c.resolve(PolishStore))
