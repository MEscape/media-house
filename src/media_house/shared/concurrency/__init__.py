"""Background job primitives and the thread-pool scheduler. Qt-free."""

from media_house.shared.concurrency.cancellation import CancellationToken
from media_house.shared.concurrency.jobs import (
    JobCallbacks,
    JobContext,
    JobHandle,
    JobOutcome,
    JobProgress,
    JobScheduler,
    JobState,
    ProgressReporter,
)
from media_house.shared.concurrency.thread_pool import ThreadPoolJobScheduler

__all__ = [
    "CancellationToken",
    "JobCallbacks",
    "JobContext",
    "JobHandle",
    "JobOutcome",
    "JobProgress",
    "JobScheduler",
    "JobState",
    "ProgressReporter",
    "ThreadPoolJobScheduler",
]
