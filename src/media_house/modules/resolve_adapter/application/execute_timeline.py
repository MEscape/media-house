"""Send a built timeline to DaVinci Resolve and verify what is there.

``ExecuteTimeline`` is the use case; ``execution.py`` holds the phases. Before Resolve is touched
the timeline and its files are checked (an unreadable timeline, a missing or changed file under a
placed clip). Once Resolve is being driven, a failure is not an exception to the caller: it is the
report, with the state it ended in, what was done, and what was not.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.application.ports import Clock
from media_house.core.domain import json_codec
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.resolve_adapter.application.execution import (
    Execution,
    project_name_for,
)
from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.application.reporting import make_report
from media_house.modules.resolve_adapter.application.sources import SourceFile, SourceFiles
from media_house.modules.resolve_adapter.application.store import ResolveStore
from media_house.modules.resolve_adapter.application.views import to_facts
from media_house.modules.resolve_adapter.domain.binding import SyncState
from media_house.modules.resolve_adapter.domain.capabilities import detect
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.environment import ConnectionStatus
from media_house.modules.resolve_adapter.domain.errors import (
    ResolveAdapterError,
    ResolveConnectionLost,
    ResolveUnavailable,
    TimelineNotExecutable,
)
from media_house.modules.resolve_adapter.domain.facts import TimelineFacts
from media_house.modules.resolve_adapter.domain.report import ExecutionReport, SyncRecord
from media_house.modules.resolve_adapter.domain.values import (
    SUPPORTED_BUILT_SCHEMAS,
    ExecutionState,
)
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
    InvalidBuiltDocument,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)

type ExecuteError = MediaError | ResolveAdapterError | InvalidBuiltDocument


@dataclass(frozen=True, slots=True)
class ExecuteTimelineCommand:
    #: The timeline in memory (from the pipeline) ...
    timeline: BuiltTimeline | None = None
    #: ... or the anchor whose newest stored build is read.
    anchor_asset_id: str | None = None
    timeline_id: str | None = None
    config: ResolveAdapterConfig = field(default_factory=ResolveAdapterConfig)
    #: Name of the timeline in Resolve; empty = from the recording's name.
    name: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    report: ExecutionReport
    report_asset: MediaAssetDto
    binding: SyncState | None
    binding_asset: MediaAssetDto | None


class ExecuteTimeline:
    def __init__(
        self,
        backend: ResolveBackend,
        library: MediaLibrary,
        catalog: BuiltTimelineCatalog,
        sources: SourceFiles,
        store: ResolveStore,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._backend = backend
        self._library = library
        self._catalog = catalog
        self._sources = sources
        self._store = store
        self._paths = paths
        self._clock = clock

    def execute(
        self, command: ExecuteTimelineCommand, ctx: JobContext
    ) -> Result[ExecutionResult, ExecuteError]:
        loaded = self._load(command)
        if isinstance(loaded, Err):
            return loaded
        timeline, anchor = loaded.value
        if timeline.schema_version not in SUPPORTED_BUILT_SCHEMAS:
            return Err(
                TimelineNotExecutable(
                    [f"the built timeline's schema {timeline.schema_version} is not readable"]
                )
            )
        facts = to_facts(timeline, name=command.name or f"{anchor.display_name} · edit")
        config = command.config
        files = self._sources.resolve(
            facts.media, proxy_profile=config.proxy_profile, with_proxies=config.link_proxies
        )
        problems = _media_problems(facts, files) if config.require_media else []
        if problems:
            return Err(TimelineNotExecutable(problems))
        previous = self._store.latest_binding(anchor.id, facts.timeline_id)
        if isinstance(previous, Err):
            return previous
        status = self._backend.connect()
        if not status.connected:
            return Err(ResolveUnavailable(status.state.value, status.detail))

        started = self._clock.now()
        ledger = Ledger()
        ledger.enter(ExecutionState.INITIALIZING)
        run = Execution(
            backend=self._backend,
            config=config,
            facts=facts,
            files=files,
            previous=previous.value[0] if previous.value else None,
            ledger=ledger,
            export_dir=self._paths.data_dir / "resolve" / anchor.id,
            on_checkpoint=lambda state: self._checkpoint(anchor, state),
        )
        execution_id = new_id()
        state_after: SyncState | None = None
        cancelled: OperationCancelledError | None = None
        try:
            with self._backend.exclusive():
                state_after = run.run(execution_id, ctx)
        except OperationCancelledError as exc:
            ledger.enter(ExecutionState.CANCELLED)
            cancelled = exc
        except ResolveConnectionLost as exc:
            ledger.fail(str(exc))
            ledger.enter(ExecutionState.INTERRUPTED)
        except ResolveAdapterError as exc:
            ledger.fail(str(exc))
            ledger.enter(ExecutionState.FAILED)
        report = self._report(run, status, execution_id, started, config)
        stored = self._store.save_report(anchor, report)
        if isinstance(stored, Err):
            return stored
        binding_asset: MediaAssetDto | None = None
        if state_after is not None:
            saved = self._store.save_binding(anchor, state_after)
            if isinstance(saved, Err):
                return saved
            binding_asset = saved.value
        if cancelled is not None:
            raise cancelled
        _log.info(
            "Timeline sent to Resolve",
            outcome=report.outcome.value,
            operations=len(report.operations),
            project=report.project_name,
        )
        return Ok(ExecutionResult(report, stored.value, state_after, binding_asset))

    def _checkpoint(self, anchor: MediaAssetDto, state: SyncState) -> None:
        saved = self._store.save_binding(anchor, state)
        if isinstance(saved, Err):
            raise ResolveAdapterError(
                f"the link to the Resolve project could not be kept: {saved.error}"
            )

    def _report(
        self,
        run: Execution,
        status: ConnectionStatus,
        execution_id: str,
        started: datetime,
        config: ResolveAdapterConfig,
    ) -> ExecutionReport:
        sync = run.sync_record() if run.expected is not None else SyncRecord("not_reached")
        matrix = run.matrix or detect(status.environment, frozenset(run.ledger.observed))
        return make_report(
            ledger=run.ledger,
            status=status,
            execution_id=execution_id,
            started=started,
            finished=self._clock.now(),
            anchor_asset_id=run.facts.anchor_asset_id,
            timeline_id=run.facts.timeline_id,
            project_id=run.facts.project_id,
            build_version=run.facts.build_version,
            build_hash=run.facts.build_hash,
            schema=run.facts.schema_version,
            policy=config.policy,
            config_hash=json_codec.digest(config),
            project_name=run.project_name or project_name_for(config, run.facts),
            project_resolve_id=run.project_resolve_id,
            database=run.database,
            timeline_name=run.timeline_name,
            timeline_resolve_id=run.timeline_resolve_id,
            capabilities=matrix.items,
            media=[run.media_records[k] for k in sorted(run.media_records)],
            sync=sync,
        )

    def _load(
        self, command: ExecuteTimelineCommand
    ) -> Result[tuple[BuiltTimeline, MediaAssetDto], ExecuteError]:
        timeline = command.timeline
        anchor_id = command.anchor_asset_id or (timeline.anchor_asset_id if timeline else None)
        if anchor_id is None:
            return Err(TimelineNotExecutable(["no timeline and no anchor recording were given"]))
        anchor = self._library.get(anchor_id)
        if isinstance(anchor, Err):
            return anchor
        if timeline is None:
            latest = self._catalog.latest(anchor_id, timeline_id=command.timeline_id)
            if isinstance(latest, Err):
                return latest
            if latest.value is None:
                return Err(TimelineNotExecutable(["no built timeline is stored for the recording"]))
            timeline = latest.value[0]
        return Ok((timeline, anchor.value))


def _media_problems(facts: TimelineFacts, files: Mapping[str, SourceFile]) -> list[str]:
    """Files a placed clip plays that are gone or changed: Resolve would show offline media."""
    problems: set[str] = set()
    names = {m.asset_id: m.name for m in facts.media}
    for clip in facts.clips:
        if clip.asset_id is None or not clip.resolved:
            continue
        found = files.get(clip.asset_id)
        label = names.get(clip.asset_id, clip.asset_id)
        if found is None or not found.exists:
            problems.add(f"{label} is no longer available")
        elif found.changed:
            problems.add(f"{label} changed since the timeline was built")
    return sorted(problems)
