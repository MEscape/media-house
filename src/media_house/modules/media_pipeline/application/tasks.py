"""What an executor receives and returns, and what the runner records about every task."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from media_house.modules.media_library.application.contracts import JsonValue
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskStatus
from media_house.shared.concurrency import JobContext, JobProgress


@dataclass(frozen=True, slots=True)
class TaskResult:
    status: TaskStatus
    detail: str = ""
    warnings: tuple[str, ...] = ()
    #: Small JSON-safe facts for the report (asset ids, counts). Never the result itself.
    facts: Mapping[str, JsonValue] = field(default_factory=dict)
    #: The typed in-memory result, handed to downstream tasks so they never re-read it.
    payload: object | None = None


@dataclass(frozen=True, slots=True)
class TaskJob:
    id: TaskId
    #: Results of every upstream task (needs and wants) that has finished, whatever its status.
    upstream: Mapping[TaskId, TaskResult]
    ctx: JobContext

    def payload_as[T](self, task_id: TaskId, expected: type[T]) -> T | None:
        """The typed payload of an upstream task that delivered one, else ``None``."""
        result = self.upstream.get(task_id)
        if result is None or not result.status.delivered:
            return None
        return result.payload if isinstance(result.payload, expected) else None


type TaskExecutor = Callable[[TaskJob], TaskResult]

#: A cheap, side-effect-free "is this exact result already stored?" check run BEFORE a task waits
#: for a lane slot. ``None`` means "unknown, ask the executor", never "does not exist".
type TaskPeek = Callable[[TaskJob], TaskResult | None]


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """The outcome of one task plus when it happened (seconds since the run started)."""

    id: TaskId
    lane: Lane
    result: TaskResult
    #: Time between "all prerequisites finished" and "a lane slot was free".
    queued_seconds: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    #: Time spent in the pre-flight cache check (0 when the task has none).
    lookup_seconds: float = 0.0
    #: The result was served by the pre-flight check; the task never took a lane slot.
    preflight_hit: bool = False

    @property
    def run_seconds(self) -> float:
        if self.started_at is None or self.finished_at is None:
            return 0.0
        return self.finished_at - self.started_at


class RunObserver(Protocol):
    """Receives progress. ``task_progress`` is called from worker threads, the rest from one."""

    def task_started(self, task_id: TaskId, lane: Lane) -> None: ...

    def task_progress(self, task_id: TaskId, progress: JobProgress) -> None: ...

    def task_finished(self, record: TaskRecord) -> None: ...


class NullObserver:
    def task_started(self, task_id: TaskId, lane: Lane) -> None:
        return None

    def task_progress(self, task_id: TaskId, progress: JobProgress) -> None:
        return None

    def task_finished(self, record: TaskRecord) -> None:
        return None


__all__ = [
    "NullObserver",
    "RunObserver",
    "TaskExecutor",
    "TaskJob",
    "TaskPeek",
    "TaskRecord",
    "TaskResult",
]
