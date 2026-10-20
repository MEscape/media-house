"""An identical request that is already running is waited for and its result shared; a request
whose owner stopped responding is taken over; one whose owner is alive is respected."""

from typing import Any

import pytest

from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.contracts import (
    Condition,
    SharedAttemptFailed,
    WaitTimedOut,
)
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, ProcessFailedError
from tests.integration.proxy_manager.conftest import Env
from tests.support.proxy_fakes import Gate, WaitSignal, in_thread, source_facts

pytestmark = pytest.mark.integration

FACTS = source_facts()
FAST: dict[str, Any] = {"poll_seconds": 0.02}


def waiter_context(signal: WaitSignal, token: CancellationToken | None = None) -> JobContext:
    return JobContext("waiter", token or CancellationToken(), signal)


def test_an_identical_request_waits_for_the_running_one_and_shares_its_result(env: Env) -> None:
    source = env.clip()
    gate = Gate(1)
    use_case, encoder, _ = env.scripted(FACTS, on_encode=gate, **FAST)
    leader, led = in_thread(lambda: env.ensure(use_case, source))
    assert gate.inside.wait(30)
    signal = WaitSignal("Waiting for an identical request")
    waiter, waited = in_thread(
        lambda: use_case.execute(EnsureProxyCommand(source, "analysis"), waiter_context(signal))
    )
    assert signal.reached.wait(30)

    gate.release.set()
    leader.join(30)
    waiter.join(30)

    first, second = led["result"], waited["result"]
    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert second.value.proxy_asset_id == first.value.proxy_asset_id
    assert first.value.reused is False and second.value.reused is True  # answered from storage
    assert len(encoder.calls) == 1
    assert env.attempts() == [("ready", None)]  # the waiter never became an attempt


def test_a_waiter_reports_the_failure_of_the_request_it_waited_for_and_does_not_encode_again(
    env: Env,
) -> None:
    source = env.clip()
    gate = Gate(1)

    def fail_after_gate(call: int) -> None:
        gate(call)
        raise ProcessFailedError("ffmpeg", 1, "encoder crashed")

    use_case, encoder, _ = env.scripted(FACTS, on_encode=fail_after_gate, **FAST)
    leader, led = in_thread(lambda: env.ensure(use_case, source))
    assert gate.inside.wait(30)
    signal = WaitSignal("Waiting for an identical request")
    waiter, waited = in_thread(
        lambda: use_case.execute(EnsureProxyCommand(source, "analysis"), waiter_context(signal))
    )
    assert signal.reached.wait(30)

    gate.release.set()
    leader.join(30)
    waiter.join(30)

    assert isinstance(led["error"], ProcessFailedError)
    result = waited["result"]
    assert isinstance(result, Err) and isinstance(result.error, SharedAttemptFailed)
    assert "failed" in str(result.error)
    assert len(encoder.calls) == 1 and env.attempts() == [("failed", "transient")]


def test_cancelling_a_waiter_cancels_only_the_waiter(env: Env) -> None:
    source = env.clip()
    gate = Gate(1)
    use_case, _, _ = env.scripted(FACTS, on_encode=gate, **FAST)
    leader, led = in_thread(lambda: env.ensure(use_case, source))
    assert gate.inside.wait(30)
    signal, token = WaitSignal("Waiting for an identical request"), CancellationToken()
    waiter, waited = in_thread(
        lambda: use_case.execute(
            EnsureProxyCommand(source, "analysis"), waiter_context(signal, token)
        )
    )
    assert signal.reached.wait(30)

    token.cancel()
    waiter.join(30)
    gate.release.set()
    leader.join(30)

    assert isinstance(waited["error"], OperationCancelledError)
    assert isinstance(led["result"], Ok)  # the running request was not touched
    assert env.attempts() == [("ready", None)]


def test_a_waiter_gives_up_after_its_own_timeout_and_leaves_the_running_request_alone(
    env: Env,
) -> None:
    source = env.clip()
    gate = Gate(1)
    use_case, _, _ = env.scripted(FACTS, on_encode=gate, **FAST)
    leader, led = in_thread(lambda: env.ensure(use_case, source))
    assert gate.inside.wait(30)
    impatient, _, _ = env.scripted(FACTS, wait_timeout_seconds=0.15, **FAST)

    result = env.ensure(impatient, source)

    assert isinstance(result, Err) and isinstance(result.error, WaitTimedOut)
    gate.release.set()
    leader.join(30)
    assert isinstance(led["result"], Ok) and env.attempts() == [("ready", None)]


def test_a_waiter_whose_request_was_superseded_reuses_the_stored_result(env: Env) -> None:
    source = env.clip()
    gate = Gate(1)
    use_case, encoder, _ = env.scripted(FACTS, on_encode=gate, **FAST)
    older, older_box = in_thread(lambda: env.ensure(use_case, source, max_long_edge=480))
    assert gate.inside.wait(30)
    newer = env.ok(use_case, source, max_long_edge=320)  # submitted later, finishes first
    signal = WaitSignal("Waiting for an identical request")
    waiter, waited = in_thread(
        lambda: use_case.execute(
            EnsureProxyCommand(source, "analysis", {"max_long_edge": 480}), waiter_context(signal)
        )
    )
    assert signal.reached.wait(30)

    gate.release.set()  # the older request now finishes and is superseded
    older.join(30)
    waiter.join(30)

    assert isinstance(older_box["result"], Err)
    result = waited["result"]
    assert isinstance(result, Ok) and result.value.reused is True
    assert result.value.proxy_asset_id != newer.proxy_asset_id
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == result.value.proxy_asset_id
    assert len(encoder.calls) == 2  # nothing was encoded for the waiter


# ------------------------------------------------------------------------------------ lease
def test_an_attempt_of_another_live_process_is_respected(env: Env) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS, wait_timeout_seconds=0.2, **FAST)
    spec_fingerprint = env.fingerprint(source, FACTS)
    env.foreign_attempt(source, spec_fingerprint)

    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, WaitTimedOut)
    assert encoder.calls == []
    assert env.attempts() == [("queued", None)]  # still the other process's, untouched


def test_an_attempt_whose_owner_stopped_responding_is_taken_over(env: Env) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS, **FAST)
    env.foreign_attempt(source, env.fingerprint(source, FACTS))
    env.clock.advance(seconds=121)  # longer than the lease with no sign of life

    dto = env.ok(use_case, source)

    assert dto.condition is Condition.READY and len(encoder.calls) == 1
    rows = env.rows("SELECT id, state, failure, error_message FROM attempts ORDER BY seq")
    assert rows[0][:3] == ("other-attempt", "interrupted", "transient")
    assert "lease expired" in rows[0][3] and rows[1][1] == "ready"


def test_an_attempt_that_keeps_proving_it_is_alive_is_not_taken_over(env: Env) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS, wait_timeout_seconds=0.2, **FAST)
    foreign = env.foreign_attempt(source, env.fingerprint(source, FACTS))
    env.clock.advance(seconds=100)
    owner = SqliteProxyStore(env.paths.data_dir / "proxy_manager.db", owner_token="other")
    assert owner.touch_attempt(foreign.id, now=env.clock.now())  # ITS heartbeat at t+100
    assert not env.store.touch_attempt(foreign.id, now=env.clock.now())  # nobody else's counts
    env.clock.advance(seconds=100)  # 200 s after it began, but only 100 s after its heartbeat

    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, WaitTimedOut)
    assert encoder.calls == []


def test_a_running_attempt_records_its_progress_as_a_heartbeat(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(
        FACTS, on_encode=lambda call: env.clock.advance(seconds=30), heartbeat_seconds=0.0, **FAST
    )
    started = env.clock.now()

    env.ok(use_case, source)

    (beat,) = env.rows("SELECT heartbeat_at FROM attempts")[0]
    assert beat > started.isoformat()  # moved on with the work, not frozen at creation
