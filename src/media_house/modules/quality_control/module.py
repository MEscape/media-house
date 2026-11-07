"""Module registration: how quality control plugs into the application.

No storage of its own: reports are documents of the recording's anchor asset in the Media Library,
evidence frames are derived assets of the preview. Measuring a file is the shared ``MediaAnalyzer``;
the live Resolve look goes through ``resolve_adapter``.
"""

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.core.modules import Container
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.continuity_intelligence.application.contracts import ContinuityCatalog
from media_house.modules.edit_plan_compiler.application.contracts import TimelineCatalog
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.quality_control.application.analyze_quality import AnalyzeQuality
from media_house.modules.quality_control.application.contracts import (
    QualityAnalyzer,
    QualityCatalog,
)
from media_house.modules.quality_control.application.ports import LiveTimelineProbe
from media_house.modules.quality_control.application.store import QualityStore
from media_house.modules.quality_control.infrastructure.resolve_live import ResolveLiveProbe
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveCatalog,
    ResolveSynchronizer,
)
from media_house.modules.review_orchestrator.application.contracts import ReviewSessions
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class QualityControlModule:
    name: str = "quality_control"

    def __init__(self, live: LiveTimelineProbe | None = None) -> None:
        self._live = live

    def register(self, container: Container) -> None:
        if self._live is not None:
            container.register_instance(LiveTimelineProbe, self._live)
        else:
            container.register_factory(
                LiveTimelineProbe, lambda c: ResolveLiveProbe(c.resolve(ResolveSynchronizer))
            )
        container.register_factory(
            QualityStore, lambda c: QualityStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            AnalyzeQuality,
            lambda c: AnalyzeQuality(
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(TimelineCatalog),
                c.resolve(MixCatalog),
                c.resolve(ContinuityCatalog),
                c.resolve(StoryCatalog),
                c.resolve(ResolveCatalog),
                c.resolve(MediaInspector),
                c.resolve(MediaAnalyzer),
                c.resolve(LiveTimelineProbe),
                c.resolve(ReviewSessions),
                c.resolve(QualityStore),
                c.resolve(EventPublisher),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(QualityAnalyzer, lambda c: c.resolve(AnalyzeQuality))
        container.register_factory(QualityCatalog, lambda c: c.resolve(QualityStore))
