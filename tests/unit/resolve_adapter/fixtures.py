"""Representative inputs for the Resolve adapter: the REAL built timeline of the builder's
fixtures (a two-clip talking head at 25 fps with cover footage, a graphic, captions, a dissolve,
music and a sound effect), carried out in a stand-in for Resolve's documented object tree.
"""

from dataclasses import replace
from pathlib import Path

from media_house.modules.resolve_adapter.application.execution import Execution
from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.application.sources import SourceFile
from media_house.modules.resolve_adapter.application.views import to_facts
from media_house.modules.resolve_adapter.domain.binding import SyncState
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.facts import TimelineFacts
from media_house.modules.resolve_adapter.infrastructure.install import (
    ProcessProbe,
    ResolveInstall,
)
from media_house.modules.resolve_adapter.infrastructure.scripting import (
    ScriptingResolveBackend,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline
from media_house.shared.concurrency import JobContext
from tests.support.resolve_fakes import FakeResolve, FakeRunner
from tests.unit.timeline_builder import fixtures as bf

NAME = "Test edit"


def backend(
    tmp: Path, fake: FakeResolve | None = None, *, running: bool | None = True
) -> tuple[ScriptingResolveBackend, FakeResolve]:
    """The real scripting adapter over the stand-in (a library file, a running process)."""
    fake = fake or FakeResolve()
    lib = tmp / "fusionscript.dll"
    lib.write_bytes(b"\x00")
    install = ResolveInstall(tmp, lib, "Resolve.exe", "win32")
    probe = ProcessProbe(FakeRunner(running=running), install)
    return ScriptingResolveBackend(install, probe, lambda _install: fake), fake


def files_for(tmp: Path, facts: TimelineFacts) -> dict[str, SourceFile]:
    """Every file the timeline plays exists; the ones with a proxy have one."""
    folder = tmp / "media"
    folder.mkdir(exist_ok=True)
    found: dict[str, SourceFile] = {}
    for media in facts.media:
        path = folder / f"{media.asset_id}.mov"
        path.write_bytes(b"\x00" * 16)
        proxy = None
        if media.proxy_asset_id:
            proxy = folder / f"{media.proxy_asset_id}.mp4"
            proxy.write_bytes(b"\x00" * 8)
        found[media.asset_id] = SourceFile(media.asset_id, path, True, False, proxy)
    return found


def facts_of(timeline: BuiltTimeline | None = None) -> TimelineFacts:
    return to_facts(timeline or bf.built(), name=NAME)


def execution(
    tmp: Path,
    *,
    facts: TimelineFacts | None = None,
    config: ResolveAdapterConfig | None = None,
    previous: SyncState | None = None,
    fake: FakeResolve | None = None,
) -> tuple[Execution, FakeResolve]:
    facts = facts or facts_of()
    be, fake = backend(tmp, fake)
    be.connect()
    return (
        Execution(
            backend=be,
            config=config or ResolveAdapterConfig(),
            facts=facts,
            files=files_for(tmp, facts),
            previous=previous,
            ledger=Ledger(),
            export_dir=tmp / "exports",
        ),
        fake,
    )


def run(ex: Execution, execution_id: str = "run-1") -> SyncState:
    return ex.run(execution_id, JobContext.detached())


def rerun(
    tmp: Path,
    fake: FakeResolve,
    state: SyncState,
    *,
    facts: TimelineFacts | None = None,
    config: ResolveAdapterConfig | None = None,
    execution_id: str = "run-2",
) -> tuple[Execution, SyncState]:
    """The next execution against the same Resolve, starting from what the last one stored."""
    facts = facts or facts_of()
    be, _ = backend(tmp, fake)
    be.connect()
    ex = Execution(
        backend=be,
        config=config or ResolveAdapterConfig(),
        facts=facts,
        files=files_for(tmp, facts),
        previous=state,
        ledger=Ledger(),
        export_dir=tmp / "exports",
    )
    return ex, run(ex, execution_id)


def with_clip(facts: TimelineFacts, clip_id: str, **changes: object) -> TimelineFacts:
    return replace(
        facts,
        clips=tuple(replace(c, **changes) if c.clip_id == clip_id else c for c in facts.clips),  # type: ignore[arg-type]
    )
