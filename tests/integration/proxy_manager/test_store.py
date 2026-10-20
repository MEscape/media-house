"""The proxy store: fencing, reservation, in-flight uniqueness and the constraints the file itself
enforces (a buggy writer is refused by the database, not only by the domain code)."""

import sqlite3
import threading
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from media_house.modules.proxy_manager.application.ports import PublishOutcome
from media_house.modules.proxy_manager.domain.attempt import Attempt, AttemptState, FailureKind
from media_house.modules.proxy_manager.domain.errors import AttemptInFlight, InsufficientSpace
from media_house.modules.proxy_manager.domain.records import ArtifactState
from media_house.modules.proxy_manager.domain.values import Evidence, Verdict
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.shared.errors import InvariantViolation, PersistenceError
from tests.support.proxy_store_helpers import (
    FP,
    LEASE,
    NOW,
    activate,
    artifact,
    attempt,
    begin,
    publishing,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "proxy.db"


@pytest.fixture
def store(db: Path) -> SqliteProxyStore:
    return SqliteProxyStore(db, owner_token="me")


# ----------------------------------------------------------------------------- attempts
def test_attempts_get_increasing_sequence_numbers(store: SqliteProxyStore) -> None:
    first, second = begin(store, 1, fp="1" * 64), begin(store, 2, fp="2" * 64)

    assert 0 < first.seq < second.seq
    assert store.get_attempt("a1") == first


def test_an_identical_generation_in_flight_is_refused_until_it_ends(
    store: SqliteProxyStore,
) -> None:
    first = begin(store, 1)

    with pytest.raises(AttemptInFlight) as refused:
        begin(store, 2)
    assert refused.value.attempt_id == "a1"

    store.save_attempt(first.fail(FailureKind.TRANSIENT, "x", now=NOW))
    assert begin(store, 3).seq > first.seq  # the failed attempt no longer blocks


def test_a_different_spec_or_profile_may_run_beside_it(store: SqliteProxyStore) -> None:
    begin(store, 1)
    begin(store, 2, fp="b" * 64)
    begin(store, 3, profile="analysis")


def test_reservations_add_up_per_volume_and_are_released_when_the_attempt_ends(
    store: SqliteProxyStore,
) -> None:
    def reserve(n: int, size: int, volume: str = "v") -> Attempt:
        return store.begin_attempt(
            attempt(n, fp=str(n) * 64, reserved_bytes=size, reserved_volume=volume),
            free_bytes=1000, margin_bytes=100, now=NOW, lease_seconds=LEASE,
        )  # fmt: skip

    first = reserve(1, 500)
    with pytest.raises(InsufficientSpace):
        reserve(2, 500)  # 1000 free - 500 reserved - 100 margin = 400 left
    reserve(3, 400)  # fits exactly
    with pytest.raises(InsufficientSpace):
        reserve(4, 1)  # nothing left on this volume
    reserve(6, 800, volume="other")  # another volume has its own budget
    store.save_attempt(first.fail(FailureKind.CANCELLED, "x", now=NOW))
    reserve(5, 500)  # released by the terminal state, no explicit release call to forget


def test_a_refused_reservation_writes_nothing(store: SqliteProxyStore) -> None:
    with pytest.raises(InsufficientSpace):
        store.begin_attempt(
            attempt(1, reserved_bytes=2000, reserved_volume="v"),
            free_bytes=1000,
            margin_bytes=0,
            now=NOW,
            lease_seconds=LEASE,
        )

    assert store.get_attempt("a1") is None


# ------------------------------------------------------------------------------ fencing
def test_the_first_activation_creates_the_slot(store: SqliteProxyStore) -> None:
    done, outcome = activate(store, 1, "X1", FP)

    slot = store.get_slot("s", "edit")
    assert outcome is PublishOutcome.ACTIVATED and done.state is AttemptState.READY
    assert slot is not None and slot.active_asset_id == "X1" and slot.previous_asset_id is None
    assert slot.activated_seq == done.seq


def test_a_replacement_keeps_the_old_usable_proxy_as_the_rollback_target(
    store: SqliteProxyStore,
) -> None:
    activate(store, 1, "X1", "1" * 64)
    activate(store, 2, "X2", "2" * 64)

    slot = store.get_slot("s", "edit")
    assert slot is not None and (slot.active_asset_id, slot.previous_asset_id) == ("X2", "X1")


def test_an_older_request_finishing_after_a_newer_one_is_superseded(
    store: SqliteProxyStore,
) -> None:
    older = publishing(store, begin(store, 1, fp="1" * 64))
    newer = publishing(store, begin(store, 2, fp="2" * 64))

    _, newer_outcome = store.publish(newer, artifact("X2", newer, fp="2" * 64), now=NOW)
    old_done, older_outcome = store.publish(older, artifact("X1", older, fp="1" * 64), now=NOW)

    slot = store.get_slot("s", "edit")
    assert newer_outcome is PublishOutcome.ACTIVATED
    assert older_outcome is PublishOutcome.SUPERSEDED and old_done.state is AttemptState.SUPERSEDED
    assert slot is not None and slot.active_asset_id == "X2" and slot.previous_asset_id is None
    assert store.get_artifact("X1") is not None  # the work is kept, just not served


def test_requests_finishing_in_submission_order_both_activate(store: SqliteProxyStore) -> None:
    older = publishing(store, begin(store, 1, fp="1" * 64))
    newer = publishing(store, begin(store, 2, fp="2" * 64))

    _, first = store.publish(older, artifact("X1", older, fp="1" * 64), now=NOW)
    _, second = store.publish(newer, artifact("X2", newer, fp="2" * 64), now=NOW)

    slot = store.get_slot("s", "edit")
    assert (first, second) == (PublishOutcome.ACTIVATED, PublishOutcome.ACTIVATED)
    assert slot is not None and (slot.active_asset_id, slot.previous_asset_id) == ("X2", "X1")


def test_a_failed_newer_request_does_not_block_the_older_one(store: SqliteProxyStore) -> None:
    older = publishing(store, begin(store, 1, fp="1" * 64))
    newer = begin(store, 2, fp="2" * 64)
    store.save_attempt(newer.fail(FailureKind.PERMANENT, "validation failed", now=NOW))

    _, outcome = store.publish(older, artifact("X1", older, fp="1" * 64), now=NOW)

    slot = store.get_slot("s", "edit")
    assert outcome is PublishOutcome.ACTIVATED
    assert slot is not None and slot.active_asset_id == "X1"


def test_a_cancelled_newer_request_does_not_block_the_older_one(store: SqliteProxyStore) -> None:
    older = publishing(store, begin(store, 1, fp="1" * 64))
    newer = begin(store, 2, fp="2" * 64)
    store.save_attempt(newer.fail(FailureKind.CANCELLED, "cancelled", now=NOW))

    _, outcome = store.publish(older, artifact("X1", older, fp="1" * 64), now=NOW)

    assert outcome is PublishOutcome.ACTIVATED


def test_the_previous_proxy_is_kept_only_while_it_is_usable(store: SqliteProxyStore) -> None:
    activate(store, 1, "X1", "1" * 64)
    store.update_verdict("X1", Verdict.INVALID, Evidence.PACKETS, 1, now=NOW)

    activate(store, 2, "X2", "2" * 64)

    slot = store.get_slot("s", "edit")
    assert slot is not None and slot.previous_asset_id is None


def test_activating_the_previous_proxy_again_swaps_the_roles(store: SqliteProxyStore) -> None:
    activate(store, 1, "X1", "1" * 64)
    activate(store, 2, "X2", "2" * 64)

    started = publishing(store, begin(store, 3, fp="1" * 64))
    store.publish(started, artifact("X1", started, fp="1" * 64), now=NOW)

    slot = store.get_slot("s", "edit")
    assert slot is not None and (slot.active_asset_id, slot.previous_asset_id) == ("X1", "X2")


def test_replacing_an_unusable_artifact_moves_the_record_and_the_slot_together(
    store: SqliteProxyStore,
) -> None:
    activate(store, 1, "X1", FP)
    store.update_verdict("X1", Verdict.INVALID, Evidence.PACKETS, 1, now=NOW)

    started = publishing(store, begin(store, 2, fp=FP, replaces_asset_id="X1"))
    _, outcome = store.publish(started, artifact("Y1", started), now=NOW)

    slot = store.get_slot("s", "edit")
    assert outcome is PublishOutcome.ACTIVATED
    assert store.get_artifact("X1") is None and store.find_artifact("s", FP) is not None
    assert slot is not None and slot.active_asset_id == "Y1" and slot.previous_asset_id is None


def test_an_artifact_being_removed_cannot_be_activated(db: Path, store: SqliteProxyStore) -> None:
    first = publishing(store, begin(store, 1))
    store.publish(first, artifact("X1", first), now=NOW)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE artifacts SET state = ?", (ArtifactState.PRUNING.value,))

    again = publishing(store, begin(store, 2))
    done, outcome = store.publish(again, artifact("X1", again), now=NOW)

    assert outcome is PublishOutcome.PRUNING and done.state is AttemptState.PUBLISHING


def test_only_a_publishing_attempt_can_publish(store: SqliteProxyStore) -> None:
    started = begin(store, 1)

    with pytest.raises(InvariantViolation):
        store.publish(started, artifact("X1", started), now=NOW)


def test_racing_activations_leave_exactly_the_newest_request_active(
    store: SqliteProxyStore,
) -> None:
    runs = [publishing(store, begin(store, n, fp=str(n) * 64)) for n in range(1, 9)]
    barrier = threading.Barrier(len(runs))
    outcomes: list[PublishOutcome] = []

    def go(run: Attempt) -> None:
        barrier.wait()
        outcomes.append(
            store.publish(run, artifact(f"X{run.seq}", run, fp=run.spec_fingerprint), now=NOW)[1]
        )

    threads = [threading.Thread(target=go, args=(r,)) for r in reversed(runs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    slot = store.get_slot("s", "edit")
    assert slot is not None and slot.activated_seq == max(r.seq for r in runs)
    assert outcomes.count(PublishOutcome.ACTIVATED) >= 1


# ---------------------------------------------------------------------------- recovery
def test_attempts_of_an_owner_past_its_lease_are_interrupted_and_the_others_untouched(
    db: Path, store: SqliteProxyStore
) -> None:
    mine = begin(store, 1, fp="1" * 64)
    other = SqliteProxyStore(db, owner_token="dead")
    other.begin_attempt(
        Attempt(id="z", source_asset_id="s", profile="edit", profile_version=1,
                spec_fingerprint="2" * 64, owner_token="dead", created_at=NOW),
        free_bytes=10**12, margin_bytes=0, now=NOW, lease_seconds=LEASE,
    )  # fmt: skip

    assert (
        store.interrupt_foreign_attempts(now=NOW + timedelta(seconds=60), lease_seconds=LEASE) == []
    )
    ended = store.interrupt_foreign_attempts(now=NOW + timedelta(seconds=121), lease_seconds=LEASE)

    assert [a.id for a in ended] == ["z"]
    dead = store.get_attempt("z")
    assert dead is not None and dead.state is AttemptState.INTERRUPTED
    assert dead.failure is FailureKind.TRANSIENT
    assert store.get_attempt(mine.id) == mine
    assert (
        store.interrupt_foreign_attempts(now=NOW + timedelta(seconds=300), lease_seconds=LEASE)
        == []
    )  # idempotent


def test_a_finished_attempt_that_still_names_a_scratch_directory_is_listed_until_cleared(
    store: SqliteProxyStore,
) -> None:
    live = begin(store, 1, scratch_name="proxy-a1")
    assert store.attempts_with_scratch() == []  # a live attempt's directory is in use

    store.save_attempt(live.fail(FailureKind.TRANSIENT, "x", now=NOW))
    assert [a.scratch_name for a in store.attempts_with_scratch()] == ["proxy-a1"]

    store.clear_scratch("a1")
    assert store.attempts_with_scratch() == []


def test_proof_of_ownership_exists_only_for_derivations_this_module_attempted(
    store: SqliteProxyStore,
) -> None:
    begin(store, 1)

    assert store.has_attempt_for("s", FP)
    assert not store.has_attempt_for("s", "b" * 64)
    assert not store.has_attempt_for("other", FP)


# ------------------------------------------------------------------------ the file itself
def test_the_database_refuses_an_illegal_state_move_even_from_another_writer(
    db: Path, store: SqliteProxyStore
) -> None:
    begin(store, 1)

    with closing(sqlite3.connect(db)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE attempts SET state = 'ready' WHERE id = 'a1'")


def test_a_referenced_artifact_cannot_be_deleted_and_a_slot_cannot_dangle(
    db: Path, store: SqliteProxyStore
) -> None:
    activate(store, 1, "X1", FP)

    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM artifacts WHERE asset_id = 'X1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE slots SET active_asset_id = 'ghost'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE slots SET previous_asset_id = active_asset_id")


def test_a_database_from_a_newer_version_is_refused(db: Path, store: SqliteProxyStore) -> None:
    begin(store, 1)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA user_version = 99")

    with pytest.raises(PersistenceError, match="newer version"):
        SqliteProxyStore(db, owner_token="x").get_attempt("a1")


def test_an_unreadable_database_is_a_typed_persistence_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database" * 100)

    with pytest.raises(PersistenceError):
        SqliteProxyStore(broken, owner_token="x").get_slot("s", "edit")
