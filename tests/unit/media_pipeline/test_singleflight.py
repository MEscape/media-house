"""Identical in-flight work is computed once; failures and cancellation never spread."""

import threading

import pytest

from media_house.modules.media_pipeline.application.singleflight import SingleFlight
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.values import TaskStatus
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import OperationCancelledError


def in_threads(count: int, target: object) -> list[threading.Thread]:
    threads = [threading.Thread(target=target) for _ in range(count)]  # type: ignore[arg-type]
    for thread in threads:
        thread.start()
    return threads


def test_concurrent_callers_share_one_computation_and_followers_report_reused() -> None:
    flight = SingleFlight()
    calls: list[int] = []
    release = threading.Event()
    entered = threading.Event()
    results: list[TaskResult] = []

    def work() -> TaskResult:
        calls.append(1)
        entered.set()
        release.wait(5)
        return TaskResult(TaskStatus.SUCCEEDED, payload="x")

    def call() -> None:
        results.append(flight.run("k", work, CancellationToken()))

    first = threading.Thread(target=call)
    first.start()
    entered.wait(5)
    followers = in_threads(3, call)
    release.set()
    for thread in [first, *followers]:
        thread.join(5)

    assert len(calls) == 1
    assert sorted(r.status.value for r in results) == ["reused", "reused", "reused", "succeeded"]
    assert {r.payload for r in results} == {"x"}


def test_different_keys_do_not_wait_for_each_other() -> None:
    flight = SingleFlight()
    inside = threading.Barrier(2, timeout=5)

    def call(key: str) -> None:
        flight.run(
            key, lambda: (inside.wait(), TaskResult(TaskStatus.SUCCEEDED))[1], CancellationToken()
        )

    threads = [threading.Thread(target=call, args=(k,)) for k in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert not inside.broken  # both were inside their work at the same time


def test_a_failed_leader_does_not_fail_the_followers_they_retry() -> None:
    flight = SingleFlight()
    attempts: list[int] = []
    entered = threading.Event()
    release = threading.Event()
    outcomes: list[TaskStatus] = []

    def work() -> TaskResult:
        attempts.append(1)
        if len(attempts) == 1:
            entered.set()
            release.wait(5)
            return TaskResult(TaskStatus.FAILED, "boom")
        return TaskResult(TaskStatus.SUCCEEDED)

    def call() -> None:
        outcomes.append(flight.run("k", work, CancellationToken()).status)

    leader = threading.Thread(target=call)
    leader.start()
    entered.wait(5)
    follower = in_threads(1, call)[0]
    release.set()
    leader.join(5)
    follower.join(5)

    assert sorted(o.value for o in outcomes) == ["failed", "succeeded"]
    assert len(attempts) == 2


def test_a_cancelled_follower_stops_waiting_and_the_leader_is_unaffected() -> None:
    flight = SingleFlight()
    entered = threading.Event()
    release = threading.Event()
    leader_result: list[TaskResult] = []

    def work() -> TaskResult:
        entered.set()
        release.wait(5)
        return TaskResult(TaskStatus.SUCCEEDED)

    leader = threading.Thread(
        target=lambda: leader_result.append(flight.run("k", work, CancellationToken()))
    )
    leader.start()
    entered.wait(5)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelledError):
        flight.run("k", work, token)
    release.set()
    leader.join(5)

    assert leader_result[0].status is TaskStatus.SUCCEEDED


def test_an_exception_in_the_leader_clears_the_flight() -> None:
    flight = SingleFlight()

    def explode() -> TaskResult:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        flight.run("k", explode, CancellationToken())

    assert (
        flight.run("k", lambda: TaskResult(TaskStatus.SUCCEEDED), CancellationToken()).status
        is TaskStatus.SUCCEEDED
    )
