"""Thread-pool implementation of :class:`JobScheduler`.

Why threads: Media-House's background work is IO-bound or delegated to external
processes (which release the GIL). CPU-bound work, when it appears, should be
delegated to a subprocess behind the ``ProcessRunner`` port rather than to
in-process parallelism. See ADR-0006.
"""

import contextvars
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from media_house.shared.concurrency.cancellation import CancellationToken
from media_house.shared.concurrency.jobs import (
    JobCallbacks,
    JobContext,
    JobHandle,
    JobOutcome,
    JobProgress,
    JobState,
)
from media_house.shared.errors import ApplicationError, OperationCancelledError
from media_house.shared.errors.handler import ErrorHandler
from media_house.shared.logging import bind_context, get_logger

_log = get_logger(__name__)


class _ProgressSink[T]:
    def __init__(self, handle: JobHandle[T], callbacks: JobCallbacks[T] | None) -> None:
        self._handle = handle
        self._callbacks = callbacks

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        if self._callbacks and self._callbacks.on_progress:
            _safe(self._callbacks.on_progress, self._handle, JobProgress(current, total, message))


class ThreadPoolJobScheduler:
    def __init__(self, *, max_workers: int, error_handler: ErrorHandler) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._error_handler = error_handler
        self._lock = threading.Lock()
        self._active: dict[str, tuple[JobHandle[Any], JobCallbacks[Any] | None]] = {}
        self._closed = False

    def submit[T](
        self,
        name: str,
        work: Callable[[JobContext], T],
        *,
        callbacks: JobCallbacks[T] | None = None,
    ) -> JobHandle[T]:
        handle: JobHandle[T] = JobHandle(uuid.uuid4().hex[:12], name, CancellationToken())
        with self._lock:
            if self._closed:
                raise ApplicationError(
                    "Scheduler is shut down",
                    user_message="The application is closing; the task was not started.",
                )
            self._active[handle.id] = (handle, callbacks)
        # Copy the caller's context so correlation ids follow the work onto the worker thread.
        context = contextvars.copy_context()
        self._executor.submit(context.run, self._run, handle, work, callbacks)
        return handle

    def shutdown(self, *, timeout: float) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active.values())
        for handle, _ in active:
            handle.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        # Jobs that never started will never run _run(): finalise them here.
        for handle, callbacks in active:
            if handle.state is JobState.PENDING:
                self._finish(handle, callbacks, JobOutcome(JobState.CANCELLED))
        deadline = time.monotonic() + timeout
        for handle, _ in active:
            if not handle.wait(max(0.0, deadline - time.monotonic())):
                _log.warning("Job did not stop before shutdown timeout", job=handle.name)

    # ------------------------------------------------------------------ #
    def _run[T](
        self,
        handle: JobHandle[T],
        work: Callable[[JobContext], T],
        callbacks: JobCallbacks[T] | None,
    ) -> None:
        with bind_context(operation_id=handle.id):
            if handle.cancellation.is_cancelled or not handle.mark_running():
                self._finish(handle, callbacks, JobOutcome(JobState.CANCELLED))
                return
            if callbacks and callbacks.on_started:
                _safe(callbacks.on_started, handle)
            context = JobContext(handle.id, handle.cancellation, _ProgressSink(handle, callbacks))
            outcome: JobOutcome[T]
            try:
                outcome = JobOutcome(JobState.SUCCEEDED, value=work(context))
            except OperationCancelledError:
                outcome = JobOutcome(JobState.CANCELLED)
            except Exception as exc:  # noqa: BLE001 - job boundary: every failure becomes an outcome
                report = self._error_handler.handle(exc, operation=handle.name)
                outcome = JobOutcome(JobState.FAILED, error=report)
            self._finish(handle, callbacks, outcome)

    def _finish[T](
        self,
        handle: JobHandle[T],
        callbacks: JobCallbacks[T] | None,
        outcome: JobOutcome[T],
    ) -> None:
        if handle.complete(outcome):
            _log.debug("Job finished", job=handle.name, state=outcome.state.value)
            if callbacks and callbacks.on_finished:
                _safe(callbacks.on_finished, handle, outcome)
        with self._lock:
            self._active.pop(handle.id, None)


def _safe(callback: Callable[..., None], *args: object) -> None:
    try:
        callback(*args)
    except Exception:  # noqa: BLE001 - observer failures must never break the worker
        _log.exception("Job callback failed")
