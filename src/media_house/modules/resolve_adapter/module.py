"""Module registration: how the Resolve adapter plugs into the application.

No storage of its own: the link to a Resolve project and every report are JSON documents of the
recording's anchor asset in the Media Library; rendered deliverables are derived assets of it.
Resolve is reached through one port (``ResolveBackend``); the default adapter drives Resolve's
scripting API from the documented install location. Nothing here starts Resolve, and nothing in
the application runs unless a caller asks.
"""

import sys

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.contracts import ProxyCatalog
from media_house.modules.resolve_adapter.application.audio_layout import ResolveAudioLayouts
from media_house.modules.resolve_adapter.application.contracts import (
    AudioLayouts,
    ResolveCatalog,
    ResolveExecutor,
    ResolveInspector,
    ResolveRenderer,
    ResolveSynchronizer,
)
from media_house.modules.resolve_adapter.application.execute_timeline import ExecuteTimeline
from media_house.modules.resolve_adapter.application.inspect_resolve import InspectResolve
from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.application.render_timeline import RenderTimeline
from media_house.modules.resolve_adapter.application.sources import SourceFiles
from media_house.modules.resolve_adapter.application.store import ResolveStore
from media_house.modules.resolve_adapter.application.sync_from_resolve import SyncFromResolve
from media_house.modules.resolve_adapter.infrastructure.install import (
    ProcessProbe,
    ResolveInstall,
    ResolveLoader,
    load_resolve,
)
from media_house.modules.resolve_adapter.infrastructure.scripting import ScriptingResolveBackend
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimelineCatalog,
    TimelineEditor,
)
from media_house.shared.filesystem import AppPaths


class ResolveAdapterModule:
    name: str = "resolve_adapter"

    def __init__(
        self,
        install: ResolveInstall | None = None,
        loader: ResolveLoader | None = None,
        backend: ResolveBackend | None = None,
    ) -> None:
        self._install = install
        self._loader = loader
        self._backend = backend

    def register(self, container: Container) -> None:
        if self._backend is not None:
            container.register_instance(ResolveBackend, self._backend)
        else:
            container.register_factory(ResolveBackend, self._build_backend)
        container.register_factory(
            SourceFiles, lambda c: SourceFiles(c.resolve(MediaLibrary), c.resolve(ProxyCatalog))
        )
        container.register_factory(
            ResolveStore, lambda c: ResolveStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            ExecuteTimeline,
            lambda c: ExecuteTimeline(
                c.resolve(ResolveBackend),
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(SourceFiles),
                c.resolve(ResolveStore),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            SyncFromResolve,
            lambda c: SyncFromResolve(
                c.resolve(ResolveBackend),
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(TimelineEditor),
                c.resolve(ResolveStore),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            RenderTimeline,
            lambda c: RenderTimeline(
                c.resolve(ResolveBackend),
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(MediaInspector),
                c.resolve(ResolveStore),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            InspectResolve, lambda c: InspectResolve(c.resolve(ResolveBackend))
        )
        container.register_factory(
            ResolveAudioLayouts,
            lambda c: ResolveAudioLayouts(c.resolve(BuiltTimelineCatalog), c.resolve(ResolveStore)),
        )
        container.register_factory(ResolveExecutor, lambda c: c.resolve(ExecuteTimeline))
        container.register_factory(ResolveSynchronizer, lambda c: c.resolve(SyncFromResolve))
        container.register_factory(ResolveRenderer, lambda c: c.resolve(RenderTimeline))
        container.register_factory(ResolveInspector, lambda c: c.resolve(InspectResolve))
        container.register_factory(ResolveCatalog, lambda c: c.resolve(ResolveStore))
        container.register_factory(AudioLayouts, lambda c: c.resolve(ResolveAudioLayouts))

    def _build_backend(self, c: Container) -> ResolveBackend:
        install = self._install or ResolveInstall.defaults(sys.platform)
        probe = ProcessProbe(c.resolve(ProcessRunner), install)
        return ScriptingResolveBackend(install, probe, self._loader or load_resolve)
