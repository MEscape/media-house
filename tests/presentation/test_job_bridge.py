import threading

import pytest
from pytestqt.qtbot import QtBot

from media_house.presentation.models.job_list_model import IS_ACTIVE_ROLE, JobListModel
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.shared.concurrency import JobContext, JobProgress
from media_house.shared.errors import ErrorCategory
from media_house.shared.errors.handler import ErrorReport

pytestmark = pytest.mark.presentation
MAIN = threading.main_thread()


def test_dispatcher_runs_work_posted_from_another_thread_on_the_ui_thread(
    qtbot: QtBot,
    dispatcher: UiDispatcher,
) -> None:
    ran_on: list[threading.Thread] = []
    thread = threading.Thread(
        target=lambda: dispatcher.post(lambda: ran_on.append(threading.current_thread()))
    )
    thread.start()
    thread.join()
    qtbot.waitUntil(lambda: bool(ran_on), timeout=3000)
    assert ran_on == [MAIN]


def test_work_runs_off_the_ui_thread_and_results_come_back_on_it(
    qtbot: QtBot,
    runner: UiJobRunner,
) -> None:
    worker: list[threading.Thread] = []
    delivered: list[tuple[int, threading.Thread]] = []
    finished: list[bool] = []

    def work(_ctx: JobContext) -> int:
        worker.append(threading.current_thread())
        return 42

    runner.submit(
        "answer",
        work,
        on_success=lambda value: delivered.append((value, threading.current_thread())),
        on_finished=lambda: finished.append(True),
    )
    qtbot.waitUntil(lambda: bool(finished), timeout=3000)
    assert delivered == [(42, MAIN)]
    assert worker[0] is not MAIN


def test_failures_reach_the_default_error_presenter_and_on_finished_still_runs(
    qtbot: QtBot,
    runner: UiJobRunner,
    unhandled_errors: list[ErrorReport],
) -> None:
    finished: list[bool] = []

    def boom(_ctx: JobContext) -> None:
        raise RuntimeError("kaput /secret/path")

    runner.submit("boom", boom, on_finished=lambda: finished.append(True))
    qtbot.waitUntil(lambda: bool(finished), timeout=3000)
    (report,) = unhandled_errors
    assert report.category is ErrorCategory.UNEXPECTED
    assert "secret" not in report.user_message


def test_explicit_error_handler_replaces_the_default(
    qtbot: QtBot,
    runner: UiJobRunner,
    unhandled_errors: list[ErrorReport],
) -> None:
    own: list[ErrorReport] = []

    def boom(_ctx: JobContext) -> None:
        raise RuntimeError("x")

    runner.submit("boom", boom, on_error=own.append)
    qtbot.waitUntil(lambda: bool(own), timeout=3000)
    assert unhandled_errors == []


def test_cancellation_and_progress(qtbot: QtBot, runner: UiJobRunner) -> None:
    progress: list[JobProgress] = []
    cancelled: list[bool] = []

    def loop(ctx: JobContext) -> None:
        ctx.progress.report(1, 10, "started")
        while True:
            ctx.raise_if_cancelled()
            ctx.cancellation.wait(0.01)

    handle = runner.submit(
        "loop", loop, on_progress=progress.append, on_cancelled=lambda: cancelled.append(True)
    )
    qtbot.waitUntil(lambda: bool(progress), timeout=3000)
    runner.cancel(handle.id)
    qtbot.waitUntil(lambda: bool(cancelled), timeout=3000)
    assert progress[0] == JobProgress(1, 10, "started")


def test_job_list_model_tracks_job_lifecycle(qtbot: QtBot, runner: UiJobRunner) -> None:
    model = JobListModel(runner)
    done: list[bool] = []
    runner.submit("quick", lambda _c: None, on_finished=lambda: done.append(True))
    assert model.rowCount() == 1  # visible immediately after submit
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    index = model.index(0)
    assert "quick: succeeded" in index.data()
    assert index.data(IS_ACTIVE_ROLE) is False


def test_submitting_from_a_worker_thread_is_rejected_loudly(runner: UiJobRunner) -> None:
    errors: list[BaseException] = []

    def misuse() -> None:
        try:
            runner.submit("bad", lambda _c: None)
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=misuse)
    thread.start()
    thread.join()
    assert len(errors) == 1
    assert "UI thread" in str(errors[0])
