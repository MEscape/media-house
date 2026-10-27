"""The scheduler: concurrency where tasks are independent, waiting where they are not."""

import random
import threading
import time
from collections.abc import Mapping

import pytest

from media_house.modules.media_pipeline.application.runner import GraphRunner
from media_house.modules.media_pipeline.application.tasks import (
    TaskExecutor,
    TaskJob,
    TaskRecord,
    TaskResult,
)
from media_house.modules.media_pipeline.domain.graph import PipelineGraph, TaskSpec
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskKind, TaskStatus
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import ExternalSystemError, InvariantViolation

K = TaskKind
OK = TaskResult(TaskStatus.SUCCEEDED)


def ctx(token: CancellationToken | None = None) -> JobContext:
    base = JobContext.detached()
    return JobContext(base.job_id, token or base.cancellation, base.progress)


def full_table(fn: TaskExecutor | None = None) -> dict[TaskKind, TaskExecutor]:
    return {k: fn or (lambda _job: OK) for k in TaskKind}


def graph_for(asset: str = "a", requested: set[TaskKind] | None = None) -> PipelineGraph:
    plan = plan_asset(
        asset, has_video=True, has_audio=True, requested=requested or {K.ALIGNMENT, K.INSPECT}
    )
    return PipelineGraph(plan.specs)


def test_independent_tasks_overlap_and_dependents_wait() -> None:
    audio_and_video_running = threading.Barrier(2, timeout=5)
    events: list[tuple[str, TaskKind]] = []
    lock = threading.Lock()

    def execute(job: TaskJob) -> TaskResult:
        kind = job.id.kind
        with lock:
            events.append(("start", kind))
        if kind in (K.AUDIO_ANALYSIS, K.VIDEO_ANALYSIS):
            audio_and_video_running.wait()  # only passes if BOTH run at the same time
        with lock:
            events.append(("end", kind))
        return OK

    run = GraphRunner(full_table(execute), {Lane.GPU: 1, Lane.CPU: 1}).run(graph_for(), ctx())

    assert {r.result.status for r in run.records.values()} == {TaskStatus.SUCCEEDED}
    order = list(events)
    align_start = order.index(("start", K.ALIGNMENT))
    assert order.index(("end", K.AUDIO_ANALYSIS)) < align_start
    assert order.index(("end", K.VIDEO_ANALYSIS)) < align_start
    assert run.peak_total >= 2


def test_a_lane_never_runs_more_tasks_than_it_has_slots() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def execute(_job: TaskJob) -> TaskResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return OK

    specs = [TaskSpec(TaskId(f"a{i}", K.AUDIO_ANALYSIS), Lane.GPU) for i in range(5)]
    run = GraphRunner(full_table(execute), {Lane.GPU: 2}).run(PipelineGraph(specs), ctx())

    assert peak == 2
    assert run.peak_running[Lane.GPU] == 2
    assert all(r.result.status is TaskStatus.SUCCEEDED for r in run.records.values())
    assert max(r.queued_seconds for r in run.records.values()) > 0  # somebody waited for a slot


def test_failed_required_task_skips_dependents_but_not_unrelated_work() -> None:
    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.VIDEO_ANALYSIS:
            return TaskResult(TaskStatus.FAILED, "decoder crashed")
        return OK

    run = GraphRunner(full_table(execute)).run(graph_for(), ctx())
    status = {t.kind: r.result for t, r in run.records.items()}

    assert status[K.VIDEO_ANALYSIS].status is TaskStatus.FAILED
    assert status[K.ALIGNMENT].status is TaskStatus.SKIPPED_DEPENDENCY
    assert "decoder crashed" in status[K.ALIGNMENT].detail
    assert status[K.AUDIO_ANALYSIS].status is TaskStatus.SUCCEEDED
    assert status[K.INSPECT].status is TaskStatus.SUCCEEDED


def test_a_failed_optional_upstream_does_not_block_the_task_that_only_wants_it() -> None:
    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.INSPECT:
            return TaskResult(TaskStatus.FAILED, "corrupt container")
        return OK

    run = GraphRunner(full_table(execute)).run(
        graph_for(requested={K.VIDEO_ANALYSIS, K.INSPECT}), ctx()
    )
    status = {t.kind: r.result.status for t, r in run.records.items()}

    assert status[K.INSPECT] is TaskStatus.FAILED
    assert status[K.VIDEO_ANALYSIS] is TaskStatus.SUCCEEDED  # ran without the inspection


def test_wanted_task_is_waited_for_even_when_slow() -> None:
    finished_inspect = threading.Event()

    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.INSPECT:
            time.sleep(0.15)
            finished_inspect.set()
        elif job.id.kind is K.VIDEO_ANALYSIS:
            assert finished_inspect.is_set(), "started before the inspection it wants finished"
        return OK

    run = GraphRunner(full_table(execute)).run(
        graph_for(requested={K.VIDEO_ANALYSIS, K.INSPECT}), ctx()
    )

    assert all(r.result.status is TaskStatus.SUCCEEDED for r in run.records.values())


def test_partial_and_reused_results_satisfy_a_need() -> None:
    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.AUDIO_ANALYSIS:
            return TaskResult(TaskStatus.REUSED)
        if job.id.kind is K.VIDEO_ANALYSIS:
            return TaskResult(TaskStatus.PARTIAL, "one analyzer missing")
        return OK

    run = GraphRunner(full_table(execute)).run(graph_for(requested={K.ALIGNMENT}), ctx())

    assert run.records[TaskId("a", K.ALIGNMENT)].result.status is TaskStatus.SUCCEEDED


def test_payload_reaches_the_dependent_typed_and_unchanged() -> None:
    seen: dict[str, object] = {}

    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.AUDIO_ANALYSIS:
            return TaskResult(TaskStatus.SUCCEEDED, payload=["timeline"])
        if job.id.kind is K.VIDEO_ANALYSIS:
            return TaskResult(TaskStatus.SUCCEEDED, payload="not a list")
        if job.id.kind is K.ALIGNMENT:
            seen["audio"] = job.payload_as(TaskId("a", K.AUDIO_ANALYSIS), list)
            seen["wrong_type"] = job.payload_as(TaskId("a", K.VIDEO_ANALYSIS), list)
        return OK

    GraphRunner(full_table(execute)).run(graph_for(requested={K.ALIGNMENT}), ctx())

    assert seen == {"audio": ["timeline"], "wrong_type": None}


def test_exceptions_become_failed_tasks_and_the_run_continues() -> None:
    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.AUDIO_ANALYSIS:
            raise ExternalSystemError("ffmpeg exited 1", user_message="FFmpeg failed.")
        if job.id.kind is K.VIDEO_ANALYSIS:
            raise RuntimeError("boom")
        return OK

    run = GraphRunner(full_table(execute)).run(graph_for(requested={K.ALIGNMENT}), ctx())
    by_kind: Mapping[TaskKind, TaskResult] = {t.kind: r.result for t, r in run.records.items()}

    assert by_kind[K.AUDIO_ANALYSIS].status is TaskStatus.FAILED
    assert "FFmpeg failed." in by_kind[K.AUDIO_ANALYSIS].detail
    assert by_kind[K.VIDEO_ANALYSIS].detail == "RuntimeError: boom"
    assert by_kind[K.ALIGNMENT].status is TaskStatus.SKIPPED_DEPENDENCY


def test_cancellation_stops_new_starts_and_marks_the_rest_cancelled() -> None:
    token = CancellationToken()

    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.INSPECT:
            token.cancel()
        return OK

    run = GraphRunner(full_table(execute)).run(
        graph_for(requested={K.VIDEO_ANALYSIS, K.INSPECT}), ctx(token)
    )
    status = {t.kind: r.result.status for t, r in run.records.items()}

    assert status[K.INSPECT] is TaskStatus.SUCCEEDED
    assert status[K.VIDEO_ANALYSIS] is TaskStatus.CANCELLED


def test_missing_executor_and_empty_lane_are_configuration_errors() -> None:
    table = full_table()
    del table[K.ALIGNMENT]
    with pytest.raises(InvariantViolation, match="No executor"):
        GraphRunner(table).run(graph_for(), ctx())
    with pytest.raises(InvariantViolation, match="at least one slot"):
        GraphRunner(full_table(), {Lane.GPU: 0})


# --- pre-flight cache checks ---------------------------------------------------------------------
def two_gpu_tasks() -> PipelineGraph:
    return PipelineGraph(
        [
            TaskSpec(TaskId("slow", K.AUDIO_ANALYSIS), Lane.GPU),
            TaskSpec(TaskId("cached", K.AUDIO_ANALYSIS), Lane.GPU),
        ]
    )


def test_a_stored_result_does_not_wait_for_a_busy_lane() -> None:
    cached_done = threading.Event()
    waited_for_cached: list[bool] = []

    def execute(job: TaskJob) -> TaskResult:
        if job.id.asset_id == "slow":
            waited_for_cached.append(cached_done.wait(timeout=5))
        return OK

    def peek(job: TaskJob) -> TaskResult | None:
        if job.id.asset_id == "cached":
            return TaskResult(TaskStatus.REUSED, "stored")
        return None

    class Watcher:
        def task_started(self, task_id: TaskId, lane: Lane) -> None: ...

        def task_progress(self, task_id: TaskId, progress: object) -> None: ...

        def task_finished(self, record: TaskRecord) -> None:
            if record.id.asset_id == "cached":
                cached_done.set()

    run = GraphRunner(full_table(execute), {Lane.GPU: 1}, peeks={K.AUDIO_ANALYSIS: peek}).run(
        two_gpu_tasks(),
        ctx(),
        Watcher(),
    )

    cached = run.records[TaskId("cached", K.AUDIO_ANALYSIS)]
    assert cached.preflight_hit
    assert cached.result.status is TaskStatus.REUSED
    assert waited_for_cached == [True]  # the slow task was still running when the hit finished
    assert run.peak_running[Lane.GPU] == 1  # the hit never took a slot


def test_a_missing_or_broken_check_only_means_ask_the_executor() -> None:
    def execute(_job: TaskJob) -> TaskResult:
        return OK

    def broken(job: TaskJob) -> TaskResult | None:
        if job.id.asset_id == "cached":
            raise RuntimeError("catalog offline")
        return None

    run = GraphRunner(full_table(execute), peeks={K.AUDIO_ANALYSIS: broken}).run(
        two_gpu_tasks(), ctx()
    )

    for record in run.records.values():
        assert record.result.status is TaskStatus.SUCCEEDED
        assert not record.preflight_hit


def test_pre_flight_checks_are_bounded() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def peek(_job: TaskJob) -> TaskResult | None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return None

    specs = [TaskSpec(TaskId(f"a{i}", K.THUMBNAIL), Lane.LIGHT) for i in range(8)]
    GraphRunner(full_table(), {Lane.LIGHT: 8}, peeks={K.THUMBNAIL: peek}, lookup_slots=2).run(
        PipelineGraph(specs), ctx()
    )

    assert peak == 2


def test_a_hit_still_feeds_its_payload_to_dependents() -> None:
    seen: list[object] = []

    def peek(_job: TaskJob) -> TaskResult | None:
        return TaskResult(TaskStatus.REUSED, payload="stored-inspection")

    def execute(job: TaskJob) -> TaskResult:
        if job.id.kind is K.VIDEO_ANALYSIS:
            seen.append(job.payload_as(TaskId("a", K.INSPECT), str))
        return OK

    GraphRunner(full_table(execute), peeks={K.INSPECT: peek}).run(
        graph_for(requested={K.VIDEO_ANALYSIS}), ctx()
    )

    assert seen == ["stored-inspection"]


# --- liveness ------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(25))
def test_random_graphs_always_finish_with_consistent_outcomes(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - seeded test data, not security
    kinds = list(TaskKind)
    ids = [TaskId(f"a{i}", rng.choice(kinds)) for i in range(12)]
    ids = list(dict.fromkeys(ids))
    specs = []
    for index, tid in enumerate(ids):
        earlier = ids[:index]
        needs = frozenset(rng.sample(earlier, k=min(len(earlier), rng.randint(0, 2))))
        wants = frozenset(rng.sample(earlier, k=min(len(earlier), rng.randint(0, 2)))) - needs
        specs.append(TaskSpec(tid, rng.choice(list(Lane)), needs, wants))
    outcomes = {tid: rng.choice(list(TaskStatus)[:5]) for tid in ids}  # succeeded..failed

    def execute(job: TaskJob) -> TaskResult:
        return TaskResult(outcomes[job.id])

    peeks = {k: (lambda job: None) for k in kinds if rng.random() < 0.5}
    run = GraphRunner(
        full_table(execute),
        {Lane.LIGHT: 1, Lane.CPU: 2, Lane.GPU: 1},
        peeks=peeks,
        lookup_slots=1,
    ).run(PipelineGraph(specs), ctx())

    assert set(run.records) == set(ids)  # nothing left pending, nothing invented
    for spec in specs:
        got = run.records[spec.id].result.status
        blocked = any(not run.records[n].result.status.delivered for n in spec.needs)
        if blocked:
            assert got is TaskStatus.SKIPPED_DEPENDENCY
        else:
            assert got is outcomes[spec.id]


def test_cancelling_a_running_task_ends_the_run_promptly() -> None:
    token = CancellationToken()
    started = threading.Event()

    def execute(job: TaskJob) -> TaskResult:
        started.set()
        job.ctx.cancellation.wait(30)  # a well-behaved long task
        job.ctx.raise_if_cancelled()
        return OK

    def cancel_when_started() -> None:
        started.wait(5)
        token.cancel()

    threading.Thread(target=cancel_when_started, daemon=True).start()
    begun = time.perf_counter()
    run = GraphRunner(full_table(execute)).run(graph_for(), ctx(token))

    assert time.perf_counter() - begun < 10
    statuses = {r.result.status for r in run.records.values()}
    assert statuses <= {TaskStatus.CANCELLED, TaskStatus.SUCCEEDED}
    assert TaskStatus.CANCELLED in statuses
    assert not any(
        r.result.status is TaskStatus.SUCCEEDED
        for t, r in run.records.items()
        if t.kind is K.ALIGNMENT
    )


def test_max_running_one_makes_the_run_strictly_sequential() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def execute(_job: TaskJob) -> TaskResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return OK

    specs = [TaskSpec(TaskId(f"a{i}", K.THUMBNAIL), Lane.LIGHT) for i in range(6)]
    run = GraphRunner(full_table(execute), max_running=1).run(PipelineGraph(specs), ctx())

    assert peak == 1 and run.peak_total == 1
    assert all(r.result.status is TaskStatus.SUCCEEDED for r in run.records.values())
    with pytest.raises(InvariantViolation, match="max_running"):
        GraphRunner(full_table(), max_running=0)
