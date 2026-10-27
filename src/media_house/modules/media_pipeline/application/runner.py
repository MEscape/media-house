"""Runs a validated graph: starts each task the moment its prerequisites are done and its lane
has a free slot, never earlier, never later.

Scheduling policy (explainable on purpose):

1. A task is *settled* once every task it needs or wants has a final record.
2. A settled task whose NEEDED upstream did not deliver is skipped, not run.
3. A settled task with a pre-flight check (``TaskPeek``) is checked first, in a small separate
   lookup pool. A hit finishes the task as ``reused`` without it ever taking a lane slot, so a
   stored result never queues behind heavy work. A miss, an error or ``None`` only means "ask the
   executor": a failed check can never fail a task or hide a result.
4. ``max_running`` caps all lanes together (1 = strictly one task at a time).
   Settled tasks start in order of how many tasks wait for them (longest chains first), then in
   plan order, as long as their lane has a free slot. Lanes bound heavy work; they do not
   serialise independent work.
5. With a ``SingleFlight`` (shared by the runs of one process), a task another run is already
   computing is waited for and shared instead of computed again.
6. Cancellation stops new starts, cancels what has not started, and lets running tasks see the
   shared token.
"""

import time
from collections.abc import Callable, Hashable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from media_house.modules.media_pipeline.application.singleflight import SingleFlight
from media_house.modules.media_pipeline.application.tasks import (
    NullObserver,
    RunObserver,
    TaskExecutor,
    TaskJob,
    TaskPeek,
    TaskRecord,
    TaskResult,
)
from media_house.modules.media_pipeline.domain.graph import PipelineGraph, TaskSpec
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskKind, TaskStatus
from media_house.shared.concurrency import JobContext, JobProgress
from media_house.shared.errors import InvariantViolation, MediaHouseError, OperationCancelledError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

DEFAULT_CAPACITY: Mapping[Lane, int] = {Lane.LIGHT: 4, Lane.CPU: 2, Lane.GPU: 1}


@dataclass(frozen=True, slots=True)
class _Looked:
    """What a pre-flight check found (``hit`` is ``None`` for "ask the executor") and when."""

    hit: TaskResult | None
    began: float
    ended: float


@dataclass(frozen=True, slots=True)
class RunRecord:
    records: Mapping[TaskId, TaskRecord]
    wall_seconds: float
    #: The most tasks that ran at the same moment, per lane (evidence of real concurrency).
    peak_running: Mapping[Lane, int] = field(default_factory=dict)
    peak_total: int = 0


class _Progress:
    def __init__(self, task_id: TaskId, observer: RunObserver) -> None:
        self._id = task_id
        self._observer = observer

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        self._observer.task_progress(self._id, JobProgress(current, total, message))


class GraphRunner:
    def __init__(
        self,
        executors: Mapping[TaskKind, TaskExecutor],
        capacity: Mapping[Lane, int] | None = None,
        *,
        peeks: Mapping[TaskKind, TaskPeek] | None = None,
        lookup_slots: int = 2,
        max_running: int | None = None,
        flight: SingleFlight | None = None,
        scope: Hashable = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._executors = executors
        self._capacity = {**DEFAULT_CAPACITY, **(capacity or {})}
        for lane, slots in self._capacity.items():
            if slots < 1:
                raise InvariantViolation(f"Lane {lane.value} needs at least one slot")
        if max_running is not None and max_running < 1:
            raise InvariantViolation("max_running must be at least 1")
        if lookup_slots < 1:
            raise InvariantViolation("The lookup pool needs at least one slot")
        self._peeks = peeks or {}
        self._lookup_slots = lookup_slots
        self._max_running = max_running
        self._flight = flight
        self._scope = scope
        self._clock = clock

    def run(
        self,
        graph: PipelineGraph,
        ctx: JobContext,
        observer: RunObserver | None = None,
    ) -> RunRecord:
        obs = observer or NullObserver()
        for tid in graph.order:
            if tid.kind not in self._executors:
                raise InvariantViolation(f"No executor for {tid.kind.value}")

        t0 = self._clock()
        records: dict[TaskId, TaskRecord] = {}
        position = {tid: i for i, tid in enumerate(graph.order)}
        pending = sorted(graph.order, key=lambda t: (-graph.weight(t), position[t]))
        settled_at: dict[TaskId, float] = {}
        running: dict[Future[TaskRecord], TaskId] = {}
        peeking: dict[Future[_Looked], TaskId] = {}
        peeked: dict[TaskId, float] = {}  # checks that found nothing: how long they took
        in_lane = dict.fromkeys(self._capacity, 0)
        peak = dict.fromkeys(self._capacity, 0)
        peak_total = 0

        def finish(record: TaskRecord) -> None:
            records[record.id] = record
            obs.task_finished(record)

        with (
            ThreadPoolExecutor(
                max_workers=sum(self._capacity.values()), thread_name_prefix="pipeline"
            ) as pool,
            ThreadPoolExecutor(
                max_workers=self._lookup_slots, thread_name_prefix="pipeline-lookup"
            ) as lookup,
        ):
            while pending or running or peeking:
                progressed = False
                checking = set(peeking.values())
                for tid in tuple(pending):
                    spec = graph[tid]
                    if not all(u in records for u in spec.upstream):
                        continue
                    settled_at.setdefault(tid, self._clock() - t0)
                    skip = self._verdict(spec, records, ctx)
                    if skip is not None:
                        pending.remove(tid)
                        now = self._clock() - t0
                        finish(
                            TaskRecord(
                                tid,
                                spec.lane,
                                skip,
                                queued_seconds=now - settled_at[tid],
                                finished_at=now,
                            )
                        )
                        progressed = True
                        continue
                    if tid in checking:
                        continue
                    upstream = {u: records[u].result for u in spec.upstream}
                    if tid.kind in self._peeks and tid not in peeked:
                        if len(peeking) < self._lookup_slots:
                            job = TaskJob(tid, upstream, ctx)
                            peek = self._peeks[tid.kind]
                            peeking[lookup.submit(self._peek, peek, job, ctx, t0)] = tid
                            checking.add(tid)
                            progressed = True
                        continue
                    if in_lane[spec.lane] >= self._capacity[spec.lane]:
                        continue
                    if self._max_running is not None and len(running) >= self._max_running:
                        continue
                    pending.remove(tid)
                    in_lane[spec.lane] += 1
                    peak[spec.lane] = max(peak[spec.lane], in_lane[spec.lane])
                    peak_total = max(peak_total, len(running) + 1)
                    started = self._clock() - t0
                    obs.task_started(tid, spec.lane)
                    future = pool.submit(
                        self._execute,
                        spec,
                        upstream,
                        ctx,
                        obs,
                        t0,
                        settled_at[tid],
                        started,
                        peeked.get(tid, 0.0),
                    )
                    running[future] = tid
                    progressed = True
                if running or peeking:
                    watched: list[Future[Any]] = [*running, *peeking]
                    done, _ = wait(watched, timeout=0.2, return_when=FIRST_COMPLETED)
                    for run_future in [f for f in running if f in done]:
                        tid = running.pop(run_future)
                        in_lane[graph[tid].lane] -= 1
                        finish(run_future.result())
                    for peek_future in [f for f in peeking if f in done]:
                        tid = peeking.pop(peek_future)
                        outcome = peek_future.result()
                        if outcome.hit is None:
                            peeked[tid] = outcome.ended - outcome.began
                            continue
                        pending.remove(tid)
                        finish(
                            TaskRecord(
                                tid,
                                graph[tid].lane,
                                outcome.hit,
                                queued_seconds=max(0.0, outcome.began - settled_at[tid]),
                                started_at=outcome.began,
                                finished_at=outcome.ended,
                                lookup_seconds=outcome.ended - outcome.began,
                                preflight_hit=True,
                            )
                        )
                elif pending and not progressed:
                    raise InvariantViolation(
                        "Scheduler stalled",
                        details={"pending": [str(t) for t in pending]},
                    )

        return RunRecord(
            records=records,
            wall_seconds=self._clock() - t0,
            peak_running=peak,
            peak_total=peak_total,
        )

    def _peek(self, peek: TaskPeek, job: TaskJob, ctx: JobContext, t0: float) -> _Looked:
        began = self._clock() - t0
        hit: TaskResult | None = None
        if not ctx.cancellation.is_cancelled:
            try:
                hit = peek(job)
            except Exception as exc:  # noqa: BLE001 - a failed check only means "ask the executor"
                _log.warning("Cache pre-check failed", task=str(job.id), error=str(exc))
        return _Looked(hit, began, self._clock() - t0)

    @staticmethod
    def _verdict(
        spec: TaskSpec, records: Mapping[TaskId, TaskRecord], ctx: JobContext
    ) -> TaskResult | None:
        """``None`` means run it; otherwise the final result that replaces running it."""
        if ctx.cancellation.is_cancelled:
            return TaskResult(TaskStatus.CANCELLED, "cancelled before it started")
        for need in sorted(spec.needs):
            got = records[need].result
            if not got.status.delivered:
                reason = f"{need.kind.value} {got.status.value}"
                return TaskResult(
                    TaskStatus.SKIPPED_DEPENDENCY,
                    f"{reason}: {got.detail}" if got.detail else reason,
                )
        return None

    def _execute(
        self,
        spec: TaskSpec,
        upstream: Mapping[TaskId, TaskResult],
        ctx: JobContext,
        observer: RunObserver,
        t0: float,
        settled: float,
        started: float,
        lookup_seconds: float,
    ) -> TaskRecord:
        job = TaskJob(
            id=spec.id,
            upstream=upstream,
            ctx=JobContext(str(spec.id), ctx.cancellation, _Progress(spec.id, observer)),
        )
        try:
            execute = self._executors[spec.id.kind]
            if self._flight is None:
                result = execute(job)
            else:
                result = self._flight.run(
                    (self._scope, spec.id), lambda: execute(job), ctx.cancellation
                )
        except OperationCancelledError:
            result = TaskResult(TaskStatus.CANCELLED, "cancelled while running")
        except MediaHouseError as exc:
            _log.warning("Task failed", task=str(spec.id), code=exc.code, error_id=exc.error_id)
            result = TaskResult(TaskStatus.FAILED, f"{exc.code}: {exc.user_message}")
        except Exception as exc:  # noqa: BLE001 - boundary: one task must not end the whole run
            _log.error("Task crashed", exc_info=exc, task=str(spec.id))
            result = TaskResult(TaskStatus.FAILED, f"{type(exc).__name__}: {exc}")
        finished = self._clock() - t0
        return TaskRecord(
            spec.id,
            spec.lane,
            result,
            queued_seconds=max(0.0, started - settled),
            started_at=started,
            finished_at=finished,
            lookup_seconds=lookup_seconds,
        )
