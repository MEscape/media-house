"""Store rules added with lease takeover, removing and pruning, and the upgrade of old data."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.proxy_manager.application.ports import PublishOutcome
from media_house.modules.proxy_manager.domain.attempt import Attempt, AttemptState, FailureKind
from media_house.modules.proxy_manager.domain.errors import AttemptInFlight, InsufficientSpace
from media_house.modules.proxy_manager.domain.records import ArtifactState
from media_house.modules.proxy_manager.infrastructure.migrations import MIGRATIONS
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from tests.support.proxy_store_helpers import (
    FP,
    LEASE,
    NOW,
    activate,
    artifact,
    attempt,
    begin,
    begin_as,
    later,
    other_owner,
    publishing,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "proxy.db"


@pytest.fixture
def store(db: Path) -> SqliteProxyStore:
    return SqliteProxyStore(db, owner_token="me")


# ----------------------------------------------------------------------------------- lease
def test_an_identical_attempt_of_a_live_owner_is_respected_and_of_a_lapsed_one_replaced(
    db: Path, store: SqliteProxyStore
) -> None:
    begin_as(other_owner(db), 1, owner="other")

    with pytest.raises(AttemptInFlight):
        begin_as(store, 2, at=60)  # 60 s of silence is within the 120 s lease
    taken = begin_as(store, 3, at=121)

    dead = store.get_attempt("a1")
    assert dead is not None and dead.state is AttemptState.INTERRUPTED
    assert dead.failure is FailureKind.TRANSIENT and "lease expired" in (dead.error_message or "")
    assert taken.seq > dead.seq


def test_an_attempt_of_this_owner_is_never_taken_over(store: SqliteProxyStore) -> None:
    begin_as(store, 1)

    with pytest.raises(AttemptInFlight):
        begin_as(store, 2, at=10_000)  # its thread is alive, or it ended the attempt itself


def test_the_disk_reserved_by_a_lapsed_owner_is_free_again(
    db: Path, store: SqliteProxyStore
) -> None:
    def reserve(who: SqliteProxyStore, n: int, at: float, owner: str) -> Attempt:
        started = replace(
            attempt(n, fp=str(n) * 64, reserved_bytes=500, reserved_volume="v"),
            owner_token=owner,
            heartbeat_at=later(at),
        )
        return who.begin_attempt(
            started, free_bytes=1000, margin_bytes=100, now=later(at), lease_seconds=LEASE
        )

    reserve(other_owner(db), 1, 0, "other")
    with pytest.raises(InsufficientSpace):
        reserve(store, 2, 60, "me")  # 500 of the 900 usable bytes are still spoken for
    reserve(store, 3, 121, "me")  # the owner stopped responding: its reservation no longer counts


def test_expiring_a_lease_only_touches_a_lapsed_live_attempt_of_another_owner(
    db: Path, store: SqliteProxyStore
) -> None:
    mine = begin_as(store, 1, fp="1" * 64)
    theirs = begin_as(other_owner(db), 2, owner="other", fp="2" * 64)
    done = begin_as(other_owner(db), 3, owner="other", fp="3" * 64)
    other_owner(db).save_attempt(done.fail(FailureKind.PERMANENT, "x", now=NOW))

    assert not store.expire_lease(mine.id, now=later(1000), lease_seconds=LEASE)  # its own
    assert not store.expire_lease(done.id, now=later(1000), lease_seconds=LEASE)  # already over
    assert not store.expire_lease(theirs.id, now=later(60), lease_seconds=LEASE)  # still alive
    assert store.expire_lease(theirs.id, now=later(121), lease_seconds=LEASE)
    assert not store.expire_lease(theirs.id, now=later(300), lease_seconds=LEASE)  # idempotent


def test_a_heartbeat_restarts_the_lease(db: Path, store: SqliteProxyStore) -> None:
    theirs = begin_as(other_owner(db), 1, owner="other")

    other_owner(db).touch_attempt(theirs.id, now=later(100))

    assert not store.expire_lease(theirs.id, now=later(150), lease_seconds=LEASE)
    assert store.expire_lease(theirs.id, now=later(221), lease_seconds=LEASE)
    ended = store.get_attempt(theirs.id)
    assert ended is not None and ended.state is AttemptState.INTERRUPTED
    store.touch_attempt(theirs.id, now=later(300))  # a heartbeat after the end changes nothing
    assert store.get_attempt(theirs.id) == ended


# ------------------------------------------------------------------- removing and pruning
def three_artifacts(store: SqliteProxyStore) -> None:
    for n in (1, 2, 3):
        activate(store, n, f"X{n}", str(n) * 64)


def test_only_artifacts_nothing_serves_or_keeps_are_unreferenced(store: SqliteProxyStore) -> None:
    three_artifacts(store)

    assert [r.asset_id for r in store.unreferenced_artifacts()] == ["X1"]  # X3 served, X2 kept


def test_an_artifact_whose_slot_has_an_attempt_in_flight_is_not_unreferenced(
    store: SqliteProxyStore,
) -> None:
    three_artifacts(store)
    begin(store, 4, fp="4" * 64)

    assert store.unreferenced_artifacts() == []


def test_claiming_marks_artifacts_so_nothing_can_adopt_them_and_is_not_repeated(
    store: SqliteProxyStore,
) -> None:
    three_artifacts(store)

    claimed = store.claim_unreferenced()

    assert [(r.asset_id, r.state) for r in claimed] == [("X1", ArtifactState.PRUNING)]
    assert store.claim_unreferenced() == []
    assert [r.asset_id for r in store.pruning_artifacts()] == ["X1"]
    started = publishing(store, begin(store, 5, fp="1" * 64))
    _, outcome = store.publish(started, artifact("X1", started, fp="1" * 64), now=NOW)
    assert outcome is PublishOutcome.PRUNING  # an adoption racing the delete loses


def test_a_claim_is_finished_or_abandoned(store: SqliteProxyStore) -> None:
    three_artifacts(store)
    store.claim_unreferenced()

    store.abandon_prune("X1")
    assert store.get_artifact("X1") is not None and store.pruning_artifacts() == []
    store.claim_unreferenced()
    store.finish_prune("X1")
    assert store.get_artifact("X1") is None
    store.finish_prune("X3")  # a served artifact is not pruning: nothing happens to it
    assert store.get_artifact("X3") is not None


def test_dissolving_a_slot_claims_both_its_artifacts(store: SqliteProxyStore) -> None:
    three_artifacts(store)

    claimed = store.claim_slot("s", "edit")

    assert sorted(r.asset_id for r in claimed) == ["X2", "X3"]
    assert all(r.state is ArtifactState.PRUNING for r in claimed)
    assert store.get_slot("s", "edit") is None
    assert store.claim_slot("s", "edit") == []  # nothing left to claim


def test_a_slot_with_an_attempt_in_flight_cannot_be_dissolved(store: SqliteProxyStore) -> None:
    three_artifacts(store)
    begin(store, 4, fp="4" * 64)

    with pytest.raises(AttemptInFlight):
        store.claim_slot("s", "edit")
    assert store.get_slot("s", "edit") is not None


def test_a_record_is_dropped_only_when_nothing_references_it(store: SqliteProxyStore) -> None:
    three_artifacts(store)

    assert not store.drop_record("X3") and not store.drop_record("X2")  # served and kept
    assert store.drop_record("X1")
    assert store.get_artifact("X1") is None


def test_purging_a_source_forgets_its_slots_and_artifacts_but_not_its_history(
    store: SqliteProxyStore,
) -> None:
    three_artifacts(store)
    elsewhere = publishing(store, begin(store, 5, fp="8" * 64, source="elsewhere"))
    store.publish(elsewhere, artifact("Z1", elsewhere, fp="8" * 64), now=NOW)

    purged = store.purge_source("s")

    assert purged == 3 and store.get_slot("s", "edit") is None
    assert store.get_artifact("X3") is None and store.get_artifact("Z1") is not None
    assert set(store.known_sources()) == {"s", "elsewhere"}  # attempts remain as history


# --------------------------------------------------------------------------- migration
def test_a_slice_1_database_is_upgraded_in_place_and_its_rows_keep_working(db: Path) -> None:
    with closing(sqlite3.connect(db)) as conn, conn:
        for statement in MIGRATIONS[0].statements:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO attempts (id, source_asset_id, profile, profile_version, "
            "spec_fingerprint, state, owner_token, created_at) "
            "VALUES ('old', 's', 'edit', 1, ?, 'generating', 'gone', ?)",
            (FP, NOW.isoformat()),
        )

    upgraded = SqliteProxyStore(db, owner_token="me")
    old = upgraded.get_attempt("old")
    taken = begin_as(upgraded, 2, at=121)  # no heartbeat was ever written: created_at is used

    assert old is not None and old.heartbeat_at is None and old.state is AttemptState.GENERATING
    replaced = upgraded.get_attempt("old")
    assert replaced is not None and replaced.state is AttemptState.INTERRUPTED
    assert taken.seq > old.seq
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
