"""The render queue: requests wait here in priority order and run one at a time.

It is a plain in-process structure driven by whoever calls ``run_next`` (a job, a script, a test):
it starts no thread of its own. A request is planned when it is queued, so its stable ``render_id``
is known at once and an identical request already waiting (or already rendered) is not queued
twice. A queued render can be held (paused) and released without any backend support; pausing a
render that is already running needs the backend to support it.
"""

import threading
from dataclasses import dataclass

from media_house.modules.render_manager.application.render import (
    PlannedRender,
    RenderFailure,
    RenderOutcome,
    RenderRequest,
    RenderService,
    RenderStatus,
)
from media_house.modules.render_manager.domain.errors import RenderManagerError, UnknownRender
from media_house.modules.render_manager.domain.values import JobState
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result


@dataclass(slots=True)
class _Entry:
    planned: PlannedRender
    sequence: int
    state: JobState = JobState.QUEUED


class RenderQueue:
    """Implements ``RenderQueueing``."""

    def __init__(self, service: RenderService) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._sequence = 0

    def enqueue(self, request: RenderRequest) -> Result[RenderStatus, RenderFailure]:
        planned = self._service.plan(request)
        if isinstance(planned, Err):
            return planned
        render_id = planned.value.render_id
        with self._lock:
            held = self._entries.get(render_id)
            if held is not None and held.state in (JobState.QUEUED, JobState.PAUSED):
                return Ok(RenderStatus(render_id, held.state, 0.0, "already queued"))
            self._sequence += 1
            self._entries[render_id] = _Entry(planned.value, self._sequence)
        return Ok(RenderStatus(render_id, JobState.QUEUED, 0.0))

    def pending(self) -> list[RenderStatus]:
        """What waits, in the order it will run."""
        with self._lock:
            order = sorted(self._waiting(), key=_order)
        return [RenderStatus(e.planned.render_id, e.state, 0.0) for e in order]

    def run_next(self, ctx: JobContext) -> Result[RenderOutcome, RenderFailure] | None:
        """Run the highest-priority queued render (oldest first on a tie); ``None``: none waits."""
        with self._lock:
            ready = [e for e in self._waiting() if e.state is JobState.QUEUED]
            if not ready:
                return None
            entry = min(ready, key=_order)
            entry.state = JobState.RUNNING
        try:
            outcome = self._service.execute(entry.planned, ctx)
        finally:
            with self._lock:
                self._entries.pop(entry.planned.render_id, None)
        return outcome

    def hold(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        return self._set(render_id, JobState.QUEUED, JobState.PAUSED)

    def release(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        return self._set(render_id, JobState.PAUSED, JobState.QUEUED)

    def withdraw(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        """Take a render out of the queue (it never started)."""
        with self._lock:
            entry = self._entries.get(render_id)
            if entry is None or entry.state is JobState.RUNNING:
                return Err(UnknownRender(render_id))
            del self._entries[render_id]
        return Ok(RenderStatus(render_id, JobState.CANCELLED, 0.0, "withdrawn before it started"))

    def _set(
        self, render_id: str, was: JobState, now: JobState
    ) -> Result[RenderStatus, RenderManagerError]:
        with self._lock:
            entry = self._entries.get(render_id)
            if entry is None or entry.state is not was:
                return Err(UnknownRender(render_id))
            entry.state = now
        return Ok(RenderStatus(render_id, now, 0.0))

    def _waiting(self) -> list[_Entry]:
        return [e for e in self._entries.values() if e.state is not JobState.RUNNING]


def _order(entry: _Entry) -> tuple[int, int]:
    return (-entry.planned.request.priority, entry.sequence)
