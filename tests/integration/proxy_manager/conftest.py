"""A real Media Library, real inspection and a real proxy store; FFmpeg is real or scripted."""

import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import (
    InspectionCatalog,
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_inspection.module import MediaInspectionModule
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.contracts import ProxyCatalog
from media_house.modules.proxy_manager.application.dto import ProxyDto
from media_house.modules.proxy_manager.application.ensure_proxy import (
    EnsureProxy,
    ProxyManagerError,
)
from media_house.modules.proxy_manager.application.maintain_proxies import MaintainProxies
from media_house.modules.proxy_manager.application.ports import (
    DiskSpace,
    MediaProbe,
    ProxyEncoder,
    ProxyStore,
    Scratch,
)
from media_house.modules.proxy_manager.domain.attempt import Attempt
from media_house.modules.proxy_manager.domain.profile import resolve_profile
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.spec import resolve_spec
from media_house.modules.proxy_manager.domain.values import PROXY_OPERATION, SPEC_VERSION
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.modules.proxy_manager.module import ProxyManagerModule
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.proxy_fakes import CopyEncoder, FakeDisk, ScriptedProbe, faithful_probe
from tests.support.proxy_media import cfr_with_audio

pytestmark = pytest.mark.integration


@dataclass
class Env:
    container: Container
    library: MediaLibrary
    paths: AppPaths
    store: SqliteProxyStore
    media_dir: Path

    @property
    def catalog(self) -> ProxyCatalog:
        return self.container.resolve(ProxyCatalog)

    @property
    def real(self) -> EnsureProxy:
        """The production wiring: real FFmpeg, real ffprobe."""
        return self.container.resolve(EnsureProxy)

    @property
    def maintenance(self) -> MaintainProxies:
        return MaintainProxies(
            self.library, self.store, self.container.resolve(Scratch), self.clock
        )

    @property
    def clock(self) -> FixedClock:
        clock = self.container.resolve(Clock)
        assert isinstance(clock, FixedClock)
        return clock

    def fingerprint(self, source: str, facts: SourceFacts, profile: str = "analysis") -> str:
        """The library fingerprint a request for ``profile`` would have for these source facts."""
        spec = resolve_spec(resolve_profile(profile), facts)
        result = self.library.fingerprint(source, PROXY_OPERATION, spec.to_config(), SPEC_VERSION)
        assert isinstance(result, Ok)
        return result.value

    def clip(self, name: str = "clip.mp4") -> str:
        return self.import_media(cfr_with_audio(self.media_dir / name))

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        with closing(sqlite3.connect(self.paths.data_dir / "proxy_manager.db")) as conn:
            return [tuple(r) for r in conn.execute(sql)]

    def attempts(self) -> list[tuple[str, str | None]]:
        return [
            (str(a), b) for a, b in self.rows("SELECT state, failure FROM attempts ORDER BY seq")
        ]

    def scratch_dirs(self) -> list[str]:
        root = self.paths.temp_dir
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.name.startswith("proxy-"))

    def foreign_attempt(self, source: str, fingerprint: str, *, owner: str = "other") -> Attempt:
        """A live attempt of ANOTHER process, whose last sign of life is the current clock time."""
        now = self.clock.now()
        other = SqliteProxyStore(self.paths.data_dir / "proxy_manager.db", owner_token=owner)
        return other.begin_attempt(
            Attempt(
                id=f"{owner}-attempt", source_asset_id=source, profile="analysis",
                profile_version=1, spec_fingerprint=fingerprint, owner_token=owner,
                created_at=now, heartbeat_at=now,
            ),
            free_bytes=10**12, margin_bytes=0, now=now, lease_seconds=120.0,
        )  # fmt: skip

    def import_media(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def inspect(self, asset_id: str) -> None:
        inspector = self.container.resolve(MediaInspector)
        assert isinstance(
            inspector.execute(InspectMediaCommand(asset_id), JobContext.detached()), Ok
        )

    def build(
        self,
        encoder: ProxyEncoder,
        probe: MediaProbe,
        *,
        disk: DiskSpace | None = None,
        store: ProxyStore | None = None,
        with_inspections: bool = False,
        clock: Clock | None = None,
        **options: Any,
    ) -> EnsureProxy:
        """The use case over scripted collaborators but the REAL library and store."""
        return EnsureProxy(
            self.library,
            self.container.resolve(InspectionCatalog) if with_inspections else None,
            probe,
            encoder,
            store or self.store,
            disk or FakeDisk(),
            self.container.resolve(Scratch),
            clock or self.container.resolve(Clock),
            **options,
        )

    def scripted(
        self,
        facts: SourceFacts,
        *,
        on_encode: Callable[[int], None] | None = None,
        with_inspections: bool = False,
        **options: Any,
    ) -> tuple[EnsureProxy, CopyEncoder, ScriptedProbe]:
        """Real library and store; the encoder copies a prepared file, the probe is faithful."""
        template = cfr_with_audio(self.media_dir / "template-proxy.mp4", seconds=1)
        encoder = CopyEncoder(template, on_encode)
        probe = faithful_probe(encoder)
        probe.facts = facts
        built = self.build(encoder, probe, with_inspections=with_inspections, **options)
        return built, encoder, probe

    def ensure(
        self,
        use_case: EnsureProxy,
        source_id: str,
        profile: str = "analysis",
        **overrides: object,
    ) -> Result[ProxyDto, ProxyManagerError]:
        return use_case.execute(
            EnsureProxyCommand(source_id, profile, overrides),  # type: ignore[arg-type]
            JobContext.detached(),
        )

    def ok(
        self, use_case: EnsureProxy, source_id: str, profile: str = "analysis", **o: object
    ) -> ProxyDto:
        result = self.ensure(use_case, source_id, profile, **o)
        assert isinstance(result, Ok), result
        return result.value


@pytest.fixture(scope="session")
def ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")


def build_env(root: Path) -> Env:
    container = Container()
    paths = AppPaths.under_root(root / "app")
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, FixedClock())
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, SubprocessRunner())
    MediaLibraryModule().register(container)
    MediaInspectionModule().register(container)
    ProxyManagerModule().register(container)
    media_dir = root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    store = container.resolve(ProxyStore)
    assert isinstance(store, SqliteProxyStore)
    return Env(container, container.resolve(MediaLibrary), paths, store, media_dir)


@pytest.fixture
def env(tmp_path: Path, ffmpeg_available: None) -> Env:
    return build_env(tmp_path)
