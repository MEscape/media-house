"""Module registration: how the Proxy Manager plugs into the application.

It owns one small database (``proxy_manager.db``: which proxy each slot serves, and the history of
attempts). Proxy files are Media Library assets derived from their sources; nothing else stores
media. FFmpeg runs through the shared ``ProcessRunner`` port.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.contracts import (
    ProxyCatalog,
    ProxyMaintenance,
    ProxyManager,
)
from media_house.modules.proxy_manager.application.ensure_proxy import EnsureProxy
from media_house.modules.proxy_manager.application.lookup import ProxyLookup
from media_house.modules.proxy_manager.application.maintain_proxies import MaintainProxies
from media_house.modules.proxy_manager.application.ports import (
    DiskSpace,
    MediaProbe,
    ProxyEncoder,
    ProxyStore,
    Scratch,
)
from media_house.modules.proxy_manager.infrastructure.ffmpeg_encoder import FfmpegEncoder
from media_house.modules.proxy_manager.infrastructure.ffmpeg_tool import ProxyFfmpeg
from media_house.modules.proxy_manager.infrastructure.ffprobe_facts import FfprobeFacts
from media_house.modules.proxy_manager.infrastructure.local_files import LocalScratch, StatDiskSpace
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.shared.filesystem import AppPaths
from media_house.shared.types import new_id


class ProxyManagerModule:
    name: str = "proxy_manager"

    def register(self, container: Container) -> None:
        container.register_factory(ProxyFfmpeg, lambda c: ProxyFfmpeg(c.resolve(ProcessRunner)))
        container.register_factory(MediaProbe, lambda c: FfprobeFacts(c.resolve(ProxyFfmpeg)))
        container.register_factory(ProxyEncoder, lambda c: FfmpegEncoder(c.resolve(ProxyFfmpeg)))
        container.register_factory(DiskSpace, lambda _c: StatDiskSpace())
        container.register_factory(Scratch, lambda c: LocalScratch(c.resolve(AppPaths)))
        # one store per application run: its owner token tells live attempts from dead ones
        container.register_factory(
            ProxyStore,
            lambda c: SqliteProxyStore(
                c.resolve(AppPaths).data_dir / "proxy_manager.db", owner_token=new_id()
            ),
        )
        container.register_factory(
            EnsureProxy,
            lambda c: EnsureProxy(
                c.resolve(MediaLibrary),
                c.resolve(InspectionCatalog),
                c.resolve(MediaProbe),
                c.resolve(ProxyEncoder),
                c.resolve(ProxyStore),
                c.resolve(DiskSpace),
                c.resolve(Scratch),
                c.resolve(Clock),
            ),
        )
        container.register_factory(ProxyManager, lambda c: c.resolve(EnsureProxy))
        container.register_factory(
            ProxyLookup,
            lambda c: ProxyLookup(
                c.resolve(MediaLibrary), c.resolve(ProxyStore), c.resolve(DiskSpace)
            ),
        )
        container.register_factory(ProxyCatalog, lambda c: c.resolve(ProxyLookup))
        container.register_factory(
            MaintainProxies,
            lambda c: MaintainProxies(
                c.resolve(MediaLibrary),
                c.resolve(ProxyStore),
                c.resolve(Scratch),
                c.resolve(Clock),
            ),
        )
        container.register_factory(ProxyMaintenance, lambda c: c.resolve(MaintainProxies))
