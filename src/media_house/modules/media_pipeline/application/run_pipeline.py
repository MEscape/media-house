"""The pipeline use case: put inputs in the library, plan every asset, run one graph, report.

The graph spans all assets, so independent work on different assets overlaps as far as the lane
capacities allow. Results live where their owners put them (the Media Library); this use case
stores nothing of its own, which is why a rerun is a set of cache hits and not a new computation.
"""

import mimetypes
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaLibrary, MediaType
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.report import (
    AssetReport,
    InputFailure,
    PipelineReport,
    TaskReport,
)
from media_house.modules.media_pipeline.application.runner import (
    DEFAULT_CAPACITY,
    GraphRunner,
    RunRecord,
)
from media_house.modules.media_pipeline.application.singleflight import SingleFlight
from media_house.modules.media_pipeline.application.tasks import (
    RunObserver,
    TaskExecutor,
    TaskPeek,
    TaskRecord,
)
from media_house.modules.media_pipeline.domain.graph import PipelineGraph, TaskSpec
from media_house.modules.media_pipeline.domain.plan import AssetPlan, plan_asset
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskKind, TaskStatus
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: What a run does when it is not told otherwise: everything that only READS or ANALYSES. The
#: heavy derivatives (proxy, improvement) are opt-in because they write new files. So is the
#: edit plan (``edit_plan``): it is cheap, but it is a DECISION that only means something with a
#: chosen profile and target length, so a default run does not store a plan nobody asked for.
DEFAULT_STAGES: frozenset[TaskKind] = frozenset(
    {
        TaskKind.INSPECT,
        TaskKind.THUMBNAIL,
        TaskKind.AUDIO_ANALYSIS,
        TaskKind.VIDEO_ANALYSIS,
        TaskKind.CONTENT_ANALYSIS,
        TaskKind.ALIGNMENT,
        TaskKind.STORY,
    }
)


@dataclass(frozen=True, slots=True)
class RunPipelineCommand:
    #: Files to import (identical content is stored once) ...
    paths: Sequence[Path] = ()
    #: ... and/or assets already in the library.
    asset_ids: Sequence[str] = ()
    stages: frozenset[TaskKind] = DEFAULT_STAGES
    #: Let content analysis wait for video analysis to use it as context. Off by default: it would
    #: hold the (audio-only) content analysis back behind a long video decode.
    content_uses_video: bool = False
    #: Slots per lane; missing lanes keep the defaults.
    capacity: Mapping[Lane, int] = field(default_factory=dict)
    #: Plan and report the graph without executing anything.
    dry_run: bool = False
    #: Most tasks running at once across all lanes; ``None`` = only the lane capacities limit.
    max_running: int | None = None
    #: Per-stage choices (profiles, CPU-only); ``None`` keeps every owner's defaults.
    settings: StageSettings = field(default_factory=StageSettings)


@dataclass(frozen=True, slots=True)
class _Source:
    asset_id: str
    name: str
    media_type: MediaType
    origin: str


class ExecutorSet(Protocol):
    """What the runner needs for one run: the executors and the optional pre-flight checks."""

    def table(self) -> Mapping[TaskKind, TaskExecutor]: ...

    def peeks(self) -> Mapping[TaskKind, TaskPeek]: ...


type ExecutorFactory = Callable[[StageSettings], ExecutorSet]


def _cache_label(record: TaskRecord) -> str:
    if record.preflight_hit:
        return "preflight_hit"
    return {TaskStatus.REUSED: "owner_hit", TaskStatus.SUCCEEDED: "miss"}.get(
        record.result.status, ""
    )


class RunPipeline:
    def __init__(self, library: MediaLibrary, executors: ExecutorFactory) -> None:
        self._library = library
        self._executors = executors
        self._flight = SingleFlight()

    def execute(
        self,
        command: RunPipelineCommand,
        ctx: JobContext,
        observer: RunObserver | None = None,
    ) -> Result[PipelineReport, ValidationError]:
        if not command.paths and not command.asset_ids:
            return Err(
                ValidationError(
                    "Nothing to process",
                    user_message="Give at least one media file or library asset.",
                )
            )
        sources, failures = self._resolve(command, ctx)
        plans = {
            s.asset_id: plan_asset(
                s.asset_id,
                has_video=s.media_type is MediaType.VIDEO,
                has_audio=s.media_type in (MediaType.VIDEO, MediaType.AUDIO),
                requested=command.stages,
                content_uses_video=command.content_uses_video,
            )
            for s in sources
            if s.media_type in (MediaType.VIDEO, MediaType.AUDIO)
        }
        graph = PipelineGraph(spec for plan in plans.values() for spec in plan.specs)
        capacity = {**DEFAULT_CAPACITY, **command.capacity}
        levels = tuple(tuple(str(t) for t in level) for level in graph.levels())

        run: RunRecord | None = None
        if not command.dry_run and len(graph):
            executors = self._executors(command.settings)
            run = GraphRunner(
                executors.table(),
                capacity,
                peeks=executors.peeks(),
                max_running=command.max_running,
                flight=self._flight,
                scope=command.settings,
            ).run(graph, ctx, observer)
        _log.info(
            "Pipeline finished",
            assets=len(sources),
            tasks=len(graph),
            dry_run=command.dry_run,
            wall_seconds=round(run.wall_seconds, 2) if run else 0.0,
        )
        return Ok(
            PipelineReport(
                assets=tuple(
                    self._asset_report(s, plans.get(s.asset_id), graph, run) for s in sources
                ),
                failures=failures,
                dry_run=command.dry_run,
                cancelled=ctx.cancellation.is_cancelled,
                wall_seconds=run.wall_seconds if run else 0.0,
                levels=levels,
                lane_capacity=capacity,
                peak_running=run.peak_running if run else {},
                peak_total=run.peak_total if run else 0,
            )
        )

    # --- inputs ---------------------------------------------------------------------------
    def _resolve(
        self, command: RunPipelineCommand, ctx: JobContext
    ) -> tuple[list[_Source], tuple[InputFailure, ...]]:
        sources: dict[str, _Source] = {}
        failures: list[InputFailure] = []
        for index, path in enumerate(command.paths):
            if ctx.cancellation.is_cancelled:
                break
            if command.dry_run:
                # A dry run must not change the library, so nothing is imported: the plan is
                # made from what the file claims to be, and says so (origin ``would_import``).
                kind = (mimetypes.guess_type(path.name)[0] or "").split("/")[0]
                if kind not in (MediaType.VIDEO.value, MediaType.AUDIO.value):
                    failures.append(InputFailure(str(path), "not recognised as audio or video"))
                    continue
                name = f"new-{index}:{path.name}"
                sources[name] = _Source(name, path.name, MediaType(kind), "would_import")
                continue
            match self._library.import_file(path):
                case Ok(imported):
                    asset = imported.asset
                    sources.setdefault(
                        asset.id,
                        _Source(
                            asset.id,
                            asset.display_name,
                            asset.media_type,
                            "imported" if imported.created else "existing",
                        ),
                    )
                case Err(error):
                    failures.append(InputFailure(str(path), error.user_message))
        for asset_id in command.asset_ids:
            match self._library.get(asset_id):
                case Ok(asset):
                    sources.setdefault(
                        asset.id, _Source(asset.id, asset.display_name, asset.media_type, "given")
                    )
                case Err(error):
                    failures.append(InputFailure(asset_id, error.user_message))
        return list(sources.values()), tuple(failures)

    # --- report ---------------------------------------------------------------------------
    def _asset_report(
        self,
        source: _Source,
        plan: AssetPlan | None,
        graph: PipelineGraph,
        run: RunRecord | None,
    ) -> AssetReport:
        if plan is None:
            return AssetReport(
                source.asset_id,
                source.name,
                source.media_type.value,
                source.origin,
                notes=(f"{source.media_type.value} assets are not processed by this pipeline",),
            )
        tasks: list[TaskReport] = []
        for kind in TaskKind:
            tid = TaskId(source.asset_id, kind)
            if tid in graph:
                tasks.append(self._planned(graph[tid], plan, run))
            elif kind in plan.not_applicable:
                tasks.append(
                    TaskReport(
                        kind,
                        TaskStatus.SKIPPED_UNAVAILABLE,
                        detail="the media has no stream this stage works on",
                    )
                )
            else:
                tasks.append(TaskReport(kind, TaskStatus.SKIPPED_DISABLED, detail="not requested"))
        return AssetReport(
            source.asset_id, source.name, source.media_type.value, source.origin, tuple(tasks)
        )

    @staticmethod
    def _planned(spec: TaskSpec, plan: AssetPlan, run: RunRecord | None) -> TaskReport:
        needs = tuple(sorted(t.kind for t in spec.needs))
        wants = tuple(sorted(t.kind for t in spec.wants))
        implied = spec.id.kind in plan.implied
        if run is None:
            return TaskReport(
                spec.id.kind,
                None,
                spec.lane,
                needs=needs,
                wants=wants,
                implied=implied,
            )
        record = run.records[spec.id]
        return TaskReport(
            spec.id.kind,
            record.result.status,
            spec.lane,
            detail=record.result.detail,
            warnings=record.result.warnings,
            facts=record.result.facts,
            needs=needs,
            wants=wants,
            implied=implied,
            queued_seconds=record.queued_seconds,
            run_seconds=record.run_seconds,
            lookup_seconds=record.lookup_seconds,
            cache=_cache_label(record),
            started_at=record.started_at,
            finished_at=record.finished_at,
        )


__all__ = ["DEFAULT_STAGES", "RunPipeline", "RunPipelineCommand"]
