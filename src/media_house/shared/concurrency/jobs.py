"""Background job contracts: lifecycle, progress, cancellation, outcome.

A *job* is a unit of background work: ``work(JobContext) -> T``. The work itself
is ordinary application code that knows nothing about threads or Qt; it only
reports progress and polls for cancellation through the :class:`JobContext`.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from media_house.shared.concurrency.cancellation import CancellationToken
from media_house.shared.errors.handler import ErrorReport


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)


@dataclass(frozen=True, slots=True)
class JobProgress:
    current: int
    total: int | None = None
    message: str = ""


class ProgressReporter(Protocol):
    def report(self, current: int, total: int | None = None, message: str = "") -> None: ...


class _NullProgress:
    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        return None


@dataclass(frozen=True, slots=True)
class JobContext:
    """Everything a unit of work may use to cooperate with its scheduler."""

    job_id: str
    cancellation: CancellationToken
    progress: ProgressReporter

    def raise_if_cancelled(self) -> None:
        self.cancellation.raise_if_cancelled()

    @classmethod
    def detached(cls) -> "JobContext":
        """Context for running work directly (tests, scripts): no scheduler, never cancelled."""
        return cls(job_id="detached", cancellation=CancellationToken(), progress=_NullProgress())


@dataclass(frozen=True, slots=True)
class JobOutcome[T]:
    state: JobState
    value: T | None = None
    error: ErrorReport | None = None


class JobHandle[T]:
    """Thread-safe view of a submitted job. Created by schedulers, held by callers."""

    def __init__(self, job_id: str, name: str, token: CancellationToken) -> None:
        self._id = job_id
        self._name = name
        self._token = token
        self._lock = threading.Lock()
        self._state = JobState.PENDING
        self._outcome: JobOutcome[T] | None = None
        self._done = threading.Event()

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> JobState:
        with self._lock:
            return self._state

    @property
    def outcome(self) -> JobOutcome[T] | None:
        with self._lock:
            return self._outcome

    @property
    def cancellation(self) -> CancellationToken:
        return self._token

    def cancel(self) -> None:
        """Request cooperative cancellation. Idempotent; no-op once finished."""
        self._token.cancel()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until finished. Returns ``False`` on timeout."""
        return self._done.wait(timeout)

    # -- scheduler-facing ------------------------------------------------- #
    def mark_running(self) -> bool:
        with self._lock:
            if self._state is not JobState.PENDING:
                return False
            self._state = JobState.RUNNING
            return True

    def complete(self, outcome: JobOutcome[T]) -> bool:
        """Record the terminal outcome. Only the first call wins; returns whether it did."""
        with self._lock:
            if self._state.is_terminal:
                return False
            self._state = outcome.state
            self._outcome = outcome
        self._done.set()
        return True


@dataclass(frozen=True, slots=True)
class JobCallbacks[T]:
    """Observer hooks. Invoked on the scheduler's worker thread, never on the UI thread."""

    on_started: Callable[[JobHandle[T]], None] | None = None
    on_progress: Callable[[JobHandle[T], JobProgress], None] | None = None
    on_finished: Callable[[JobHandle[T], JobOutcome[T]], None] | None = None


class JobScheduler(Protocol):
    """Port: run work in the background."""

    def submit[T](
        self,
        name: str,
        work: Callable[[JobContext], T],
        *,
        callbacks: JobCallbacks[T] | None = None,
    ) -> JobHandle[T]: ...

    def shutdown(self, *, timeout: float) -> None: ...
