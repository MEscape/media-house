"""Regression tests for races and stale state found reviewing Slice 2 (in-process, deterministic).

The cross-process counterparts are in ``test_cross_process.py``.
"""

import time
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.proxy_manager.application.contracts import Condition
from media_house.modules.proxy_manager.domain.attempt import AttemptState, FailureKind
from media_house.modules.proxy_manager.domain.errors import AttemptInFlight, LeaseLost
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.shared.errors import Err, Ok
from tests.integration.proxy_manager.conftest import Env
from tests.support.proxy_fakes import source_facts
from tests.support.proxy_store_helpers import (
    NOW,
    artifact,
    begin_as,
    later,
    other_owner,
    publishing,
)

pytestmark = pytest.mark.integration

FACTS = source_facts()


def exists(env: Env, asset_id: str) -> bool:
    return isinstance(env.library.get(asset_id), Ok)


# ------------------------------------------------- stale records must not wedge a request
def test_a_proxy_asset_deleted_behind_the_modules_back_is_replaced_not_a_database_error(
    env: Env,
) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source)
    assert isinstance(env.library.delete(first.proxy_asset_id), Ok)  # asset gone, record remains

    again = env.ok(use_case, source)

    assert not again.reused and again.proxy_asset_id != first.proxy_asset_id
    assert len(encoder.calls) == 2 and env.catalog.condition(source, "analysis") is Condition.READY
    assert env.store.get_artifact(first.proxy_asset_id) is None  # the stale record was replaced


def test_a_stale_rollback_record_is_replaced_the_same_way(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    rollback = env.ok(use_case, source, max_long_edge=480)
    env.ok(use_case, source, max_long_edge=320)
    assert isinstance(env.library.delete(rollback.proxy_asset_id), Ok)

    env.ok(use_case, source, max_long_edge=480)  # the rollback spec is asked for again

    slot = env.store.get_slot(source, "analysis")
    assert slot is not None and slot.previous_asset_id != rollback.proxy_asset_id
    assert env.store.get_artifact(rollback.proxy_asset_id) is None


def test_a_removal_that_stopped_after_marking_does_not_wedge_the_next_request(env: Env) -> None:
    """Crash between the phases of a prune/remove, with nobody calling prune afterwards: the
    request that meets the half-removed artifact finishes the removal itself and carries on."""
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source)
    assert env.store.claim_slot(source, "analysis")  # phase 1 only: slot dissolved, marked

    again = env.ok(use_case, source)

    assert not again.reused and again.proxy_asset_id != first.proxy_asset_id
    assert not exists(env, first.proxy_asset_id) and len(encoder.calls) == 2
    assert env.store.pruning_artifacts() == []


def test_a_half_removed_artifact_that_cannot_be_deleted_is_reported_as_being_removed(
    env: Env,
) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source)
    assert isinstance(env.library.ensure_thumbnail(first.proxy_asset_id), Ok)  # derived from it
    env.store.claim_slot(source, "analysis")

    again = env.ensure(use_case, source)

    # the library refuses to delete it (something derives from it): it is live again and reused
    assert isinstance(again, Ok) and again.value.proxy_asset_id == first.proxy_asset_id
    assert len(encoder.calls) == 1


def test_adopting_an_artifact_that_is_being_removed_is_still_refused_by_the_store(
    env: Env,
) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source, max_long_edge=480)
    env.ok(use_case, source, max_long_edge=320)
    env.ok(use_case, source, max_long_edge=240)
    env.store.claim_unreferenced()  # marks `first` (nobody serves or keeps it) as being removed

    result = env.ensure(use_case, source, max_long_edge=480)

    # the request finishes the removal and generates afresh instead of failing for good
    assert isinstance(result, Ok) and result.value.proxy_asset_id != first.proxy_asset_id
    assert not exists(env, first.proxy_asset_id) and len(encoder.calls) == 4


# --------------------------------------------- maintenance must not be blocked by dead owners
def test_prune_is_not_blocked_forever_by_the_attempt_of_a_dead_process(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    oldest = env.ok(use_case, source, max_long_edge=480).proxy_asset_id
    env.ok(use_case, source, max_long_edge=320)
    env.ok(use_case, source, max_long_edge=240)
    env.foreign_attempt(source, "d" * 64)  # a live-looking attempt on the same slot

    respected = env.maintenance.prune(dry_run=False)
    kept_while_alive = exists(env, oldest)
    env.clock.advance(seconds=121)  # its owner never showed a sign of life again
    after = env.maintenance.prune(dry_run=False)

    assert respected.pruned == () and kept_while_alive  # a live owner is respected
    assert after.pruned == (oldest,)


def test_remove_is_not_blocked_forever_by_the_attempt_of_a_dead_process(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    served = env.ok(use_case, source).proxy_asset_id
    env.foreign_attempt(source, "d" * 64)

    refused = env.maintenance.remove(source, "analysis")
    env.clock.advance(seconds=121)
    removed = env.maintenance.remove(source, "analysis")

    assert isinstance(refused, Err) and isinstance(refused.error, AttemptInFlight)
    assert isinstance(removed, Ok) and removed.value.removed_asset_ids == (served,)


# ------------------------------------------------------ a retired attempt must stop cleanly
def test_an_attempt_retired_by_a_takeover_cannot_publish_or_move_on(
    tmp_path: Path,
) -> None:
    db = tmp_path / "proxy.db"
    mine = SqliteProxyStore(db, owner_token="me")
    stale = begin_as(other_owner(db), 1, owner="other")
    stale = publishing(other_owner(db), stale)
    begin_as(mine, 2, at=121)  # the takeover retires `stale` (silent for longer than the lease)

    with pytest.raises(LeaseLost):
        other_owner(db).publish(stale, artifact("X1", stale), now=later(122))
    with pytest.raises(LeaseLost):
        other_owner(db).save_attempt(stale.advance(AttemptState.READY, now=later(122)))
    retired = mine.get_attempt(stale.id)
    assert retired is not None and retired.state is AttemptState.INTERRUPTED
    assert retired.failure is FailureKind.TRANSIENT
    assert mine.get_artifact("X1") is None and mine.get_slot("s", "edit") is None  # nothing leaked


def test_touching_a_retired_attempt_reports_that_it_no_longer_belongs_to_the_caller(
    tmp_path: Path,
) -> None:
    db = tmp_path / "proxy.db"
    owner = other_owner(db)
    stale = begin_as(owner, 1, owner="other")
    begin_as(SqliteProxyStore(db, owner_token="me"), 2, at=121)

    assert owner.touch_attempt(stale.id, now=later(122)) is False  # told: stop, you were retired
    live = begin_as(owner, 3, owner="other", fp="9" * 64)
    assert owner.touch_attempt(live.id, now=NOW) is True


# ------------------------------------------------ the heartbeat must not depend on progress
def test_a_long_phase_without_progress_still_proves_the_attempt_is_alive(env: Env) -> None:
    source = env.clip()
    seen: dict[str, Any] = {}

    def stall(call: int) -> None:
        env.clock.advance(seconds=30)  # the clock moves; only a heartbeat can record it
        time.sleep(0.5)  # no progress callback, no state change: a long validation or copy
        seen["beat"] = env.rows("SELECT heartbeat_at FROM attempts")[0][0]
        seen["expected"] = env.clock.now().isoformat()

    use_case, _, _ = env.scripted(FACTS, on_encode=stall, heartbeat_seconds=0.05)

    env.ok(use_case, source)

    assert seen["beat"] == seen["expected"]


def test_an_attempt_retired_while_working_stops_before_registering_anything(env: Env) -> None:
    source = env.clip()
    db = env.paths.data_dir / "proxy_manager.db"

    def retired_by_another_process(call: int) -> None:
        attempt_id = str(env.rows("SELECT id FROM attempts")[0][0])
        assert other_owner(db).expire_lease(attempt_id, now=later(10**6), lease_seconds=1.0)

    use_case, encoder, _ = env.scripted(
        FACTS, on_encode=retired_by_another_process, heartbeat_seconds=0.05
    )

    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, LeaseLost)
    assert env.library.list_derived(source) == []  # nothing was registered on its behalf
    assert env.catalog.find(source, "analysis") is None
    assert len(encoder.calls) == 1 and env.attempts() == [("interrupted", "transient")]
    assert env.scratch_dirs() == []
