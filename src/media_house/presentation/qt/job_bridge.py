"""Bridge between the Qt-free :class:`JobScheduler` and the UI thread.

Contract: ``submit`` is called on the UI thread; every callback and signal is
delivered on the UI thread. Worker threads never touch widgets.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.shared.concurrency import (
    JobCallbacks,
    JobContext,
    JobHandle,
    JobOutcome,
    JobProgress,
    JobScheduler,
    JobState,
)
from media_house.shared.errors.handler import ErrorReport


class UiJobRunner(QObject):
    job_submitted = Signal(object)  # JobHandle
    job_progress = Signal(object, object)  # JobHandle, JobProgress
    job_finished = Signal(object, object)  # JobHandle, JobOutcome

    def __init__(
        self,
        scheduler: JobScheduler,
        dispatcher: UiDispatcher,
        on_unhandled_error: Callable[[ErrorReport], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._scheduler = scheduler
        self._dispatcher = dispatcher
        self._on_unhandled_error = on_unhandled_error
        self._handles: dict[str, JobHandle[object]] = {}

    def submit[T](
        self,
        name: str,
        work: Callable[[JobContext], T],
        *,
        on_success: Callable[[T], None] | None = None,
        on_error: Callable[[ErrorReport], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        on_progress: Callable[[JobProgress], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> JobHandle[T]:
        """Run ``work`` in the background.

        Failures without an ``on_error`` are shown through the default error presenter.
        ``on_finished`` always runs last, whatever the outcome - use it to reset state.
        """
        if QThread.currentThread() != self.thread():
            # Mutating _handles and emitting signals from a worker would be a silent race.
            raise RuntimeError("UiJobRunner.submit() must be called on the UI thread")
        post = self._dispatcher.post

        def finished(handle: JobHandle[T], outcome: JobOutcome[T]) -> None:
            post(
                lambda: self._finish(
                    handle, outcome, on_success, on_error, on_cancelled, on_finished
                ),
            )

        def progress(handle: JobHandle[T], value: JobProgress) -> None:
            post(lambda: self._progress(handle, value, on_progress))

        handle = self._scheduler.submit(
            name,
            work,
            callbacks=JobCallbacks(on_progress=progress, on_finished=finished),
        )
        self._handles[handle.id] = handle  # type: ignore[assignment]
        self.job_submitted.emit(handle)
        return handle

    def cancel(self, job_id: str) -> None:
        handle = self._handles.get(job_id)
        if handle is not None:
            handle.cancel()

    # -- UI-thread side ---------------------------------------------------- #
    def _progress[T](
        self,
        handle: JobHandle[T],
        value: JobProgress,
        callback: Callable[[JobProgress], None] | None,
    ) -> None:
        self.job_progress.emit(handle, value)
        if callback is not None:
            callback(value)

    def _finish[T](
        self,
        handle: JobHandle[T],
        outcome: JobOutcome[T],
        on_success: Callable[[T], None] | None,
        on_error: Callable[[ErrorReport], None] | None,
        on_cancelled: Callable[[], None] | None,
        on_finished: Callable[[], None] | None,
    ) -> None:
        self._handles.pop(handle.id, None)
        self.job_finished.emit(handle, outcome)
        try:
            match outcome.state:
                case JobState.SUCCEEDED if on_success is not None:
                    on_success(outcome.value)  # type: ignore[arg-type]
                case JobState.FAILED if outcome.error is not None:
                    (on_error or self._on_unhandled_error)(outcome.error)
                case JobState.CANCELLED if on_cancelled is not None:
                    on_cancelled()
                case _:
                    pass
        finally:
            if on_finished is not None:
                on_finished()
