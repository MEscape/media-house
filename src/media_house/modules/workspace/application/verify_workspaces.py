"""Use case: verify all workspaces in the background (demonstrates the job pattern).

The use case is plain application code: it takes a ``JobContext`` to report
progress and honour cancellation, and knows nothing about threads or Qt.
"""

from media_house.core.application.ports import Clock
from media_house.modules.workspace.application.dto import VerificationSummary
from media_house.modules.workspace.application.events import WorkspaceVerificationCompleted
from media_house.modules.workspace.domain.repository import WorkspaceRepository
from media_house.shared.concurrency import JobContext
from media_house.shared.events import EventPublisher


class VerifyWorkspaces:
    def __init__(
        self,
        repository: WorkspaceRepository,
        clock: Clock,
        events: EventPublisher,
        *,
        step_delay_seconds: float = 0.0,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._events = events
        # Demonstration pacing only, so progress and cancellation are visible in the UI.
        self._step_delay = step_delay_seconds

    def execute(self, context: JobContext) -> VerificationSummary:
        workspaces = self._repository.list_all()
        total = len(workspaces)
        now = self._clock.now()
        problems: list[str] = []
        for index, workspace in enumerate(workspaces, start=1):
            context.raise_if_cancelled()
            problems.extend(workspace.problems(now=now))
            context.progress.report(index, total, f"Verified {workspace.name}")
            if self._step_delay and context.cancellation.wait(self._step_delay):
                context.raise_if_cancelled()
        self._events.publish(
            WorkspaceVerificationCompleted(
                occurred_at=self._clock.now(),
                checked=total,
                problem_count=len(problems),
            ),
        )
        return VerificationSummary(checked=total, problems=tuple(problems))
