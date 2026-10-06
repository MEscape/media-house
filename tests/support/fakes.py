"""Small deterministic test doubles shared across test suites."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from media_house.modules.workspace.domain.repository import WorkspaceNameTaken
from media_house.modules.workspace.domain.values import WorkspaceId, WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.shared.concurrency import (
    CancellationToken,
    JobCallbacks,
    JobContext,
    JobHandle,
    JobOutcome,
    JobProgress,
    JobState,
)
from media_house.shared.errors import OperationCancelledError
from media_house.shared.errors.handler import ErrorHandler

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


def _copy(workspace: Workspace) -> Workspace:
    """Like a real repository, hand out re-hydrated aggregates without pending events."""
    return Workspace(id=workspace.id, name=workspace.name, created_at=workspace.created_at)


class InMemoryWorkspaceRepository:
    def __init__(self) -> None:
        self._items: dict[str, Workspace] = {}

    def add(self, workspace: Workspace) -> None:
        if any(w.name.key == workspace.name.key for w in self._items.values()):
            raise WorkspaceNameTaken(workspace.name)
        self._items[workspace.id.value] = _copy(workspace)

    def get(self, workspace_id: WorkspaceId) -> Workspace | None:
        stored = self._items.get(workspace_id.value)
        return _copy(stored) if stored else None

    def list_all(self) -> Sequence[Workspace]:
        ordered = sorted(self._items.values(), key=lambda w: (w.created_at, w.id.value))
        return [_copy(w) for w in ordered]

    def name_exists(self, name: WorkspaceName) -> bool:
        return any(w.name.key == name.key for w in self._items.values())


class ImmediateJobScheduler:
    """Runs jobs synchronously on submit. Deterministic stand-in for the thread pool."""

    def __init__(self) -> None:
        self._errors = ErrorHandler()

    def submit[T](
        self,
        name: str,
        work: Callable[[JobContext], T],
        *,
        callbacks: JobCallbacks[T] | None = None,
    ) -> JobHandle[T]:
        handle: JobHandle[T] = JobHandle(f"job-{name}", name, CancellationToken())
        handle.mark_running()

        class _Progress:
            def report(self, current: int, total: int | None = None, message: str = "") -> None:
                if callbacks and callbacks.on_progress:
                    callbacks.on_progress(handle, JobProgress(current, total, message))

        context = JobContext(handle.id, handle.cancellation, _Progress())
        try:
            outcome: JobOutcome[T] = JobOutcome(JobState.SUCCEEDED, value=work(context))
        except OperationCancelledError:
            outcome = JobOutcome(JobState.CANCELLED)
        except Exception as exc:  # noqa: BLE001 - mirrors the real scheduler's job boundary
            outcome = JobOutcome(JobState.FAILED, error=self._errors.handle(exc))
        handle.complete(outcome)
        if callbacks and callbacks.on_finished:
            callbacks.on_finished(handle, outcome)
        return handle

    def shutdown(self, *, timeout: float) -> None:
        return None
