"""Module registration: how the render manager plugs into the application.

No storage of its own: render reports are documents of the recording's anchor asset in the Media
Library, rendered files are derived assets. The default backend renders through
``resolve_adapter``'s contract; section previews are cut with FFmpeg through ``ProcessRunner``.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.render_manager.application.catalog import RenderHistory
from media_house.modules.render_manager.application.contracts import (
    RenderCatalog,
    RenderManager,
    RenderProfiles,
    RenderQueueing,
)
from media_house.modules.render_manager.application.ports import (
    DiskProbe,
    RenderBackend,
    SectionCutter,
)
from media_house.modules.render_manager.application.profiles import RenderProfileBook
from media_house.modules.render_manager.application.queue import RenderQueue
from media_house.modules.render_manager.application.render import RenderService
from media_house.modules.render_manager.application.store import RenderStore
from media_house.modules.render_manager.infrastructure.disk import LocalDiskProbe
from media_house.modules.render_manager.infrastructure.ffmpeg_cutter import FfmpegSectionCutter
from media_house.modules.render_manager.infrastructure.resolve_backend import ResolveRenderBackend
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveInspector,
    ResolveRenderer,
)
from media_house.modules.review_orchestrator.application.contracts import ReviewSessions
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class RenderManagerModule:
    name: str = "render_manager"

    def __init__(self, backend: RenderBackend | None = None) -> None:
        self._backend = backend

    def register(self, container: Container) -> None:
        if self._backend is not None:
            container.register_instance(RenderBackend, self._backend)
        else:
            container.register_factory(
                RenderBackend,
                lambda c: ResolveRenderBackend(
                    c.resolve(ResolveRenderer), c.resolve(ResolveInspector)
                ),
            )
        container.register_factory(
            SectionCutter, lambda c: FfmpegSectionCutter(c.resolve(ProcessRunner))
        )
        container.register_factory(DiskProbe, lambda c: LocalDiskProbe())
        container.register_factory(
            RenderStore, lambda c: RenderStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            RenderService,
            lambda c: RenderService(
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(RenderBackend),
                c.resolve(SectionCutter),
                c.resolve(DiskProbe),
                c.resolve(MediaInspector),
                c.resolve(ReviewSessions),
                c.resolve(RenderStore),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(EventPublisher),
            ),
        )
        container.register_factory(RenderManager, lambda c: c.resolve(RenderService))
        container.register_factory(RenderQueue, lambda c: RenderQueue(c.resolve(RenderService)))
        container.register_factory(RenderQueueing, lambda c: c.resolve(RenderQueue))
        container.register_factory(
            RenderProfileBook, lambda c: RenderProfileBook(c.resolve(BuiltTimelineCatalog))
        )
        container.register_factory(RenderProfiles, lambda c: c.resolve(RenderProfileBook))
        container.register_factory(RenderHistory, lambda c: RenderHistory(c.resolve(RenderStore)))
        container.register_factory(RenderCatalog, lambda c: c.resolve(RenderHistory))
