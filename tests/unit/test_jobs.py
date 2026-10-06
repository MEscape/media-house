import threading
from collections.abc import Iterator

import pytest

from media_house.shared.concurrency import (
    JobCallbacks,
    JobContext,
    JobHandle,
    JobOutcome,
    JobProgress,
    JobState,
    ThreadPoolJobScheduler,
)
from media_house.shared.errors import ApplicationError, ErrorCategory
from media_house.shared.errors.handler import ErrorHandler
from media_house.shared.logging import bind_context, current_correlation_id, current_operation_id

WAIT = 5.0


@pytest.fixture
def scheduler() -> Iterator[ThreadPoolJobScheduler]:
    pool = ThreadPoolJobScheduler(max_workers=2, error_handler=ErrorHandler())
    yield pool
    pool.shutdown(timeout=WAIT)


def test_successful_job(scheduler: ThreadPoolJobScheduler) -> None:
    finished: list[JobOutcome[int]] = []
    handle = scheduler.submit(
        "add",
        lambda _ctx: 40 + 2,
        callbacks=JobCallbacks(on_finished=lambda _h, outcome: finished.append(outcome)),
    )
    assert handle.wait(WAIT)
    assert handle.state is JobState.SUCCEEDED
    assert handle.outcome is not None
    assert handle.outcome.value == 42
    assert finished == [handle.outcome]


def test_failing_job_yields_a_safe_report(scheduler: ThreadPoolJobScheduler) -> None:
    def explode(_ctx: JobContext) -> None:
        raise ValueError("/home/alice/secret.mp4 is broken")

    handle = scheduler.submit("explode", explode)
    assert handle.wait(WAIT)
    assert handle.state is JobState.FAILED
    assert handle.outcome is not None
    assert handle.outcome.error is not None
    assert handle.outcome.error.category is ErrorCategory.UNEXPECTED
    assert "alice" not in handle.outcome.error.user_message


def test_cancellation_stops_cooperative_work(scheduler: ThreadPoolJobScheduler) -> None:
    started = threading.Event()

    def loop(ctx: JobContext) -> None:
        started.set()
        while True:
            ctx.raise_if_cancelled()
            ctx.cancellation.wait(0.01)

    handle = scheduler.submit("loop", loop)
    assert started.wait(WAIT)
    handle.cancel()
    assert handle.wait(WAIT)
    assert handle.state is JobState.CANCELLED


def test_progress_and_started_callbacks(scheduler: ThreadPoolJobScheduler) -> None:
    progress: list[JobProgress] = []
    started: list[str] = []

    def work(ctx: JobContext) -> None:
        ctx.progress.report(1, 2, "half")
        ctx.progress.report(2, 2, "done")

    handle = scheduler.submit(
        "p",
        work,
        callbacks=JobCallbacks(
            on_started=lambda h: started.append(h.name),
            on_progress=lambda _h, p: progress.append(p),
        ),
    )
    assert handle.wait(WAIT)
    assert started == ["p"]
    assert progress == [JobProgress(1, 2, "half"), JobProgress(2, 2, "done")]


def test_correlation_context_follows_work_onto_worker_thread(
    scheduler: ThreadPoolJobScheduler,
) -> None:
    def probe(_ctx: JobContext) -> tuple[str | None, str | None, str]:
        return current_correlation_id(), current_operation_id(), threading.current_thread().name

    with bind_context(correlation_id="user-action-1"):
        handle: JobHandle[tuple[str | None, str | None, str]] = scheduler.submit("probe", probe)
    assert handle.wait(WAIT)
    assert handle.outcome is not None
    correlation, operation, thread_name = handle.outcome.value or (None, None, "")
    assert correlation == "user-action-1"
    assert operation == handle.id
    assert thread_name != threading.current_thread().name


def test_a_crashing_callback_does_not_break_the_job(scheduler: ThreadPoolJobScheduler) -> None:
    def bad(*_: object) -> None:
        raise RuntimeError("observer bug")

    handle = scheduler.submit("ok", lambda _c: 1, callbacks=JobCallbacks(on_finished=bad))
    assert handle.wait(WAIT)
    assert handle.state is JobState.SUCCEEDED


def test_shutdown_cancels_running_and_pending_jobs() -> None:
    pool = ThreadPoolJobScheduler(max_workers=1, error_handler=ErrorHandler())
    running = threading.Event()
    finished: list[JobState] = []

    def blocker(ctx: JobContext) -> None:
        running.set()
        while True:
            ctx.raise_if_cancelled()
            ctx.cancellation.wait(0.01)

    callbacks: JobCallbacks[None] = JobCallbacks(
        on_finished=lambda _h, outcome: finished.append(outcome.state),
    )
    first = pool.submit("first", blocker, callbacks=callbacks)
    pending = pool.submit("pending", lambda _c: None, callbacks=callbacks)
    assert running.wait(WAIT)

    pool.shutdown(timeout=WAIT)

    assert first.state is JobState.CANCELLED
    assert pending.state is JobState.CANCELLED
    assert finished.count(JobState.CANCELLED) == 2  # every job reported exactly once
    pool.shutdown(timeout=WAIT)  # idempotent


def test_submitting_after_shutdown_is_rejected() -> None:
    pool = ThreadPoolJobScheduler(max_workers=1, error_handler=ErrorHandler())
    pool.shutdown(timeout=WAIT)
    with pytest.raises(ApplicationError):
        pool.submit("late", lambda _c: None)


def test_detached_context_allows_running_work_directly() -> None:
    context = JobContext.detached()
    context.progress.report(1)
    context.raise_if_cancelled()
    context.cancellation.cancel()
    assert context.cancellation.is_cancelled


def test_handle_completes_only_once() -> None:
    from media_house.shared.concurrency import CancellationToken

    handle: JobHandle[int] = JobHandle("1", "x", CancellationToken())
    assert handle.complete(JobOutcome(JobState.SUCCEEDED, value=1))
    assert not handle.complete(JobOutcome(JobState.FAILED))
    assert handle.state is JobState.SUCCEEDED
