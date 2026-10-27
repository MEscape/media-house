"""The reconciliation store: one contract run against the in-memory fake AND the SQLite adapter.

SQLite-only tests follow: the audit table, atomic purge, the migration of a version-1 database,
constraints, corruption, the compare-and-set cursor under threads, and the query plan.
"""

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.detection_store import Mark, MarkOutcome
from media_house.modules.asset_relationships.domain.errors import CorruptClaim
from media_house.modules.asset_relationships.domain.reconciliation import (
    ClaimVerdict,
    PurgeReason,
    ReconcilePolicy,
    ReconciliationStore,
    reconcile_claim,
)
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.repository import (
    RelationshipCriteria,
    RelationshipRepository,
)
from media_house.modules.asset_relationships.domain.values import (
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.asset_relationships.infrastructure.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_detection_store import (
    SqliteDetectionStore,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_reconciliation_store import (
    SqliteReconciliationStore,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)
from media_house.shared.errors import InvariantViolation, PersistenceError
from tests.support.relationship_fakes import (
    T0,
    InMemoryReconciliationStore,
    InMemoryRelationshipRepository,
    tag_match,
)

pytestmark = pytest.mark.integration

TAKE = RelationshipType.ALTERNATE_TAKE_OF
LATER = T0 + timedelta(hours=1)
POLICY = ReconcilePolicy(timedelta(days=7), timedelta(days=365))


@dataclass
class Stores:
    repository: RelationshipRepository
    store: ReconciliationStore
    path: Path | None


def make_claim(
    first: str = "a", second: str = "b", status: RelationshipStatus = RelationshipStatus.PROPOSED
) -> AssetRelationship:
    claim = AssetRelationship.create(
        ClaimInput.of(
            TAKE,
            first,
            second,
            origin=RelationshipOrigin.DETECTOR,
            basis=Basis("take-detector", "1"),
            evidence=(tag_match(),),
        ),
        now=T0,
    )
    if status is RelationshipStatus.CONFIRMED:
        claim.confirm(now=T0, note="same scene")
    elif status is RelationshipStatus.REJECTED:
        claim.reject(now=T0)
    return claim


def put(stores: Stores, claim: AssetRelationship) -> None:
    stores.repository.update(claim.id, lambda _existing: claim)


def noop(claim: AssetRelationship) -> ClaimVerdict:
    return ClaimVerdict()


def mark_missing(claim: AssetRelationship) -> ClaimVerdict:
    return reconcile_claim(
        claim, absent={claim.asset_a}, basis_current=None, now=LATER, policy=POLICY
    )


@pytest.fixture(params=["memory", "sqlite"])
def stores(request: pytest.FixtureRequest, tmp_path: Path) -> Stores:
    if request.param == "memory":
        repository = InMemoryRelationshipRepository()
        return Stores(repository, InMemoryReconciliationStore(repository), None)
    path = tmp_path / "asset_relationships.db"
    return Stores(SqliteRelationshipRepository(path), SqliteReconciliationStore(path), path)


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    return tmp_path / "asset_relationships.db"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TestClaimsAfter:
    def test_walks_every_claim_once_in_id_order(self, stores: Stores) -> None:
        for number in range(12):
            put(stores, make_claim(f"a{number}", f"b{number}"))
        expected = sorted(make_claim(f"a{n}", f"b{n}").id.value for n in range(12))

        seen: list[str] = []
        cursor: str | None = None
        while True:
            batch = stores.store.claims_after(cursor, 5)
            if not batch:
                break
            seen += [ref.relationship_id for ref in batch]
            cursor = batch[-1].relationship_id

        assert seen == expected

    def test_a_reference_carries_the_endpoints_and_nothing_hydrated(self, stores: Stores) -> None:
        claim = make_claim("a", "b")
        put(stores, claim)

        (ref,) = stores.store.claims_after(None, 10)

        assert (ref.relationship_id, ref.asset_a, ref.asset_b) == (claim.id.value, "a", "b")

    def test_a_reference_hints_whether_the_claim_is_marked_and_what_its_basis_is(
        self, stores: Stores
    ) -> None:
        claim = make_claim("a", "b")
        put(stores, claim)

        (before,) = stores.store.claims_after(None, 10)
        stores.store.reconcile_claim(claim.id.value, mark_missing, now=LATER)
        (after,) = stores.store.claims_after(None, 10)

        assert (before.marked, after.marked) == (False, True)
        assert before.basis == after.basis == claim.basis.canonical()

    def test_an_empty_store_and_the_end_of_the_walk_give_nothing(self, stores: Stores) -> None:
        assert stores.store.claims_after(None, 5) == []
        put(stores, make_claim())
        last = stores.store.claims_after(None, 5)[-1].relationship_id
        assert stores.store.claims_after(last, 5) == []

    def test_claims_added_during_the_walk_are_seen_only_if_they_sort_ahead(
        self, stores: Stores
    ) -> None:
        for number in range(10):
            put(stores, make_claim(f"a{number}", f"b{number}"))
        first = stores.store.claims_after(None, 5)
        cursor = first[-1].relationship_id
        ahead = behind = None
        for number in range(100, 400):
            candidate = make_claim(f"a{number}", f"b{number}")
            if candidate.id.value > cursor and ahead is None:
                ahead = candidate
            if candidate.id.value < cursor and behind is None:
                behind = candidate
            if ahead and behind:
                break
        assert ahead is not None and behind is not None
        put(stores, ahead)
        put(stores, behind)

        rest = [ref.relationship_id for ref in stores.store.claims_after(cursor, 100)]

        assert ahead.id.value in rest
        assert behind.id.value not in rest
        assert len(rest) == len(set(rest))

    def test_deleting_claims_during_the_walk_does_not_disturb_the_cursor(
        self, stores: Stores
    ) -> None:
        for number in range(10):
            put(stores, make_claim(f"a{number}", f"b{number}"))
        everything = [r.relationship_id for r in stores.store.claims_after(None, 100)]
        cursor = everything[4]
        victim = everything[2]  # already passed
        stores.store.reconcile_claim(
            victim,
            lambda c: reconcile_claim(
                c, absent={"x"}, basis_current=None, now=LATER, policy=POLICY
            ),
            now=LATER,
        )

        assert [r.relationship_id for r in stores.store.claims_after(cursor, 100)] == everything[5:]


class TestReconcileClaim:
    def test_the_rules_see_the_claim_as_stored_and_their_change_is_saved(
        self, stores: Stores
    ) -> None:
        claim = make_claim("a", "b")
        put(stores, claim)

        verdict = stores.store.reconcile_claim(claim.id.value, mark_missing, now=LATER)

        assert verdict == ClaimVerdict(marked_missing=True)
        stored = stores.repository.get(claim.id)
        assert stored is not None
        assert stored.endpoint_missing_since == LATER
        assert stored.updated_at == LATER
        assert stored.status is RelationshipStatus.PROPOSED

    def test_an_unchanged_claim_is_not_rewritten(self, stores: Stores) -> None:
        claim = make_claim()
        put(stores, claim)

        verdict = stores.store.reconcile_claim(claim.id.value, noop, now=LATER)

        assert verdict == ClaimVerdict()
        stored = stores.repository.get(claim.id)
        assert stored is not None
        assert stored.updated_at == T0

    def test_a_claim_that_is_gone_answers_none(self, stores: Stores) -> None:
        assert stores.store.reconcile_claim("nothing", noop, now=LATER) is None

    def test_a_decision_in_the_store_is_what_the_rules_see(self, stores: Stores) -> None:
        claim = make_claim()
        put(stores, claim)
        decided = stores.repository.get(claim.id)
        assert decided is not None
        decided.confirm(now=LATER)
        stores.repository.update(claim.id, lambda _existing: decided)
        seen: list[RelationshipStatus] = []

        def look(current: AssetRelationship) -> ClaimVerdict:
            seen.append(current.status)
            return ClaimVerdict()

        stores.store.reconcile_claim(claim.id.value, look, now=LATER)

        assert seen == [RelationshipStatus.CONFIRMED]

    def test_an_error_in_the_rules_writes_nothing(self, stores: Stores) -> None:
        claim = make_claim()
        put(stores, claim)

        def broken(current: AssetRelationship) -> ClaimVerdict:
            current.mark_endpoint_missing(now=LATER)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            stores.store.reconcile_claim(claim.id.value, broken, now=LATER)

        stored = stores.repository.get(claim.id)
        assert stored is not None
        assert stored.endpoint_missing_since is None

    def test_a_purge_deletes_the_claim_and_only_then(self, stores: Stores) -> None:
        claim = make_claim()
        claim.mark_endpoint_missing(now=T0)
        put(stores, claim)
        due = T0 + timedelta(days=8)

        verdict = stores.store.reconcile_claim(
            claim.id.value,
            lambda c: reconcile_claim(c, absent={"a"}, basis_current=None, now=due, policy=POLICY),
            now=due,
        )

        assert verdict is not None
        assert verdict.purge is PurgeReason.UNDECIDED_ORPHAN
        assert stores.repository.get(claim.id) is None
        assert stores.repository.find(RelationshipCriteria("a")) == []

    def test_a_purge_of_a_claim_never_marked_missing_is_refused(self, stores: Stores) -> None:
        claim = make_claim()
        put(stores, claim)

        with pytest.raises(InvariantViolation):
            stores.store.reconcile_claim(
                claim.id.value,
                lambda _c: ClaimVerdict(purge=PurgeReason.UNDECIDED_ORPHAN),
                now=LATER,
            )

        assert stores.repository.get(claim.id) is not None

    def test_the_marker_round_trips_through_the_repository(self, stores: Stores) -> None:
        claim = make_claim()
        put(stores, claim)
        stores.store.reconcile_claim(claim.id.value, mark_missing, now=LATER)

        found = stores.repository.find(RelationshipCriteria("a"))

        assert [c.endpoint_missing_since for c in found] == [LATER]


class TestScanState:
    def test_a_new_store_has_an_empty_state(self, stores: Stores) -> None:
        state = stores.store.load_state()

        assert (state.claim_cursor, state.sweep_generation, state.consecutive_failures) == (
            None,
            0,
            0,
        )
        assert (state.last_sweep_completed_at, state.next_attempt_after, state.last_run) == (
            None,
            None,
            None,
        )

    def test_the_cursor_moves_only_from_where_it_was_expected(self, stores: Stores) -> None:
        assert stores.store.advance_cursor(None, "c1", now=T0) is True
        assert stores.store.advance_cursor(None, "c2", now=T0) is False  # stale expectation
        assert stores.store.advance_cursor("c1", "c2", now=T0) is True

        state = stores.store.load_state()

        assert state.claim_cursor == "c2"
        assert state.sweep_started_at == T0

    def test_the_start_of_a_sweep_is_recorded_once(self, stores: Stores) -> None:
        stores.store.advance_cursor(None, "c1", now=T0)
        stores.store.advance_cursor("c1", "c2", now=LATER)

        assert stores.store.load_state().sweep_started_at == T0

    def test_completing_a_sweep_clears_the_cursor_and_counts_the_generation(
        self, stores: Stores
    ) -> None:
        stores.store.advance_cursor(None, "c1", now=T0)

        assert stores.store.complete_sweep("wrong", now=LATER) is False
        assert stores.store.complete_sweep("c1", now=LATER) is True

        state = stores.store.load_state()
        assert (state.claim_cursor, state.sweep_generation) == (None, 1)
        assert state.last_sweep_completed_at == LATER

    def test_an_empty_sweep_can_complete_from_no_cursor(self, stores: Stores) -> None:
        assert stores.store.complete_sweep(None, now=LATER) is True
        assert stores.store.load_state().sweep_generation == 1

    def test_a_run_is_recorded_with_its_failure_count_and_next_attempt(
        self, stores: Stores
    ) -> None:
        stores.store.record_run(
            last_run='{"x": 1}', consecutive_failures=2, next_attempt_after=LATER, now=T0
        )

        state = stores.store.load_state()
        assert (state.last_run, state.consecutive_failures, state.next_attempt_after) == (
            '{"x": 1}',
            2,
            LATER,
        )

        stores.store.record_run(
            last_run=None, consecutive_failures=0, next_attempt_after=None, now=T0
        )
        state = stores.store.load_state()
        assert (state.last_run, state.consecutive_failures, state.next_attempt_after) == (
            '{"x": 1}',
            0,
            None,
        )

    def test_racing_cursor_moves_have_exactly_one_winner(self, stores: Stores) -> None:
        barrier = threading.Barrier(8)
        wins: list[bool] = []
        errors: list[BaseException] = []

        def worker(number: int) -> None:
            try:
                barrier.wait(timeout=10)
                wins.append(stores.store.advance_cursor(None, f"c{number}", now=T0))
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert sorted(wins) == [False] * 7 + [True]


class TestSqliteAudit:
    def purge(self, path: Path, claim: AssetRelationship, *, at: timedelta) -> None:
        due = T0 + at
        SqliteReconciliationStore(path).reconcile_claim(
            claim.id.value,
            lambda c: reconcile_claim(c, absent={"a"}, basis_current=None, now=due, policy=POLICY),
            now=due,
        )

    def test_a_purge_writes_one_audit_row_without_evidence_and_removes_the_claim_atomically(
        self, sqlite_path: Path
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        claim = make_claim("a", "b", RelationshipStatus.CONFIRMED)
        claim.mark_endpoint_missing(now=T0)
        repository.update(claim.id, lambda _existing: claim)

        self.purge(sqlite_path, claim, at=timedelta(days=366))

        with closing(connect(sqlite_path)) as conn:
            (row,) = conn.execute("SELECT * FROM purge_log").fetchall()
            assert dict(row) == {
                "seq": 1,
                "relationship_id": claim.id.value,
                "type": "alternate_take_of",
                "asset_a": "a",
                "asset_b": "b",
                "final_status": "confirmed",
                "decided_at": T0.isoformat(timespec="microseconds"),
                "endpoint_missing_since": T0.isoformat(timespec="microseconds"),
                "purged_at": (T0 + timedelta(days=366)).isoformat(timespec="microseconds"),
                "reason": "retention_expired",
            }
            assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0] == 0

    def test_an_undecided_purge_has_no_decision_time(self, sqlite_path: Path) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        claim = make_claim("a", "b")
        claim.mark_endpoint_missing(now=T0)
        repository.update(claim.id, lambda _existing: claim)

        self.purge(sqlite_path, claim, at=timedelta(days=8))

        with closing(connect(sqlite_path)) as conn:
            (row,) = conn.execute("SELECT decided_at, reason, final_status FROM purge_log")
            assert tuple(row) == (None, "undecided_orphan", "proposed")

    def test_the_audit_log_keeps_growing_and_is_never_trimmed_by_the_store(
        self, sqlite_path: Path
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        for number in range(3):
            claim = make_claim("a", f"b{number}")
            claim.mark_endpoint_missing(now=T0)
            repository.update(claim.id, lambda _existing, c=claim: c)  # type: ignore[misc]
            self.purge(sqlite_path, claim, at=timedelta(days=8))

        with closing(connect(sqlite_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM purge_log").fetchone()[0] == 3

    @pytest.mark.parametrize(
        ("column", "value"),
        [("reason", "because"), ("final_status", "bogus")],
    )
    def test_the_audit_table_rejects_invented_values(
        self, sqlite_path: Path, column: str, value: str
    ) -> None:
        SqliteReconciliationStore(sqlite_path).load_state()
        values = {
            "relationship_id": "r",
            "type": "alternate_take_of",
            "asset_a": "a",
            "asset_b": "b",
            "final_status": "proposed",
            "decided_at": None,
            "endpoint_missing_since": "2026-01-01T00:00:00.000000+00:00",
            "purged_at": "2026-01-01T00:00:00.000000+00:00",
            "reason": "undecided_orphan",
        } | {column: value}

        columns = ", ".join(values)
        marks = ", ".join("?" * len(values))
        sql = f"INSERT INTO purge_log ({columns}) VALUES ({marks})"  # noqa: S608
        with closing(connect(sqlite_path)) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, tuple(values.values()))


class TestSqliteSchemaAndMigration:
    def test_a_version_1_database_is_migrated_and_keeps_every_claim(
        self, sqlite_path: Path
    ) -> None:
        with closing(connect(sqlite_path)) as conn:
            for statement in MIGRATIONS[0].statements:
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 1")
            conn.execute(
                "INSERT INTO relationships (relationship_id, type, symmetric, asset_a, asset_b, "
                "status, grade, origin, basis, created_at, updated_at) VALUES "
                "('r1', 'alternate_take_of', 1, 'a', 'b', 'confirmed', 'indicated', 'user', "
                '\'{"detector":"manual","inputs":{},"version":"1"}\', '
                "'2026-01-01T12:00:00.000000+00:00', '2026-01-02T12:00:00.000000+00:00')"
            )
            conn.execute(
                "INSERT INTO relationship_evidence (relationship_id, kind, provider, "
                "provider_version, detail, observed_at) VALUES "
                "('r1', 'user_assertion', 'user', '1', '{}', '2026-01-01T12:00:00.000000+00:00')"
            )
            conn.execute(
                "INSERT INTO decision_history (relationship_id, status_from, status_to, at, note, "
                "evidence_snapshot) VALUES "
                "('r1', NULL, 'confirmed', '2026-01-01T12:00:00.000000+00:00', 'made by hand', "
                "'[\"user_assertion|user|1\"]')"
            )
            conn.commit()

        SqliteReconciliationStore(sqlite_path).load_state()  # opening migrates

        with closing(connect(sqlite_path)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION == 3
            row = conn.execute("SELECT * FROM relationships").fetchone()
            assert (row["relationship_id"], row["status"], row["endpoint_missing_since"]) == (
                "r1",
                "confirmed",
                None,
            )
            assert conn.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0] == 1
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            indexes = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
        assert {"scan_state", "purge_log"} <= tables
        assert {"ix_relationships_missing", "ix_purge_log_assets"} <= indexes

    def test_a_version_2_database_gains_the_detection_tables_and_loses_nothing(
        self, sqlite_path: Path
    ) -> None:
        with closing(connect(sqlite_path)) as conn:
            for migration in MIGRATIONS[:2]:
                for statement in migration.statements:
                    conn.execute(statement)
            conn.execute("PRAGMA user_version = 2")
            conn.execute(
                "INSERT INTO relationships (relationship_id, type, symmetric, asset_a, asset_b, "
                "status, grade, origin, basis, created_at, updated_at) VALUES "
                "('r2', 'alternate_take_of', 1, 'a', 'b', 'rejected', 'indicated', 'detector', "
                '\'{"detector":"production_tag_takes","inputs":{},"version":"1"}\', '
                "'2026-01-01T12:00:00.000000+00:00', '2026-01-02T12:00:00.000000+00:00')"
            )
            conn.execute("INSERT INTO scan_state (key, value) VALUES ('claim_cursor', 'r1')")
            conn.execute(
                "INSERT INTO purge_log (relationship_id, type, asset_a, asset_b, final_status, "
                "endpoint_missing_since, purged_at, reason) VALUES ('gone', 'alternate_take_of', "
                "'x', 'y', 'confirmed', '2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00', "
                "'retention_expired')"
            )
            conn.commit()

        store = SqliteDetectionStore(sqlite_path)  # opening migrates
        store.put_mark(
            Mark("a", "wall_clock_overlap", "1", "i:f", MarkOutcome.NONE, 0),
            analyzed_at="2026-03-01T00:00:00+00:00",
        )

        with closing(connect(sqlite_path)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
            kept = conn.execute("SELECT status FROM relationships").fetchall()
            assert [r["status"] for r in kept] == ["rejected"]  # a decision survives
            assert conn.execute("SELECT value FROM scan_state").fetchone()[0] == "r1"
            assert conn.execute("SELECT COUNT(*) FROM purge_log").fetchone()[0] == 1
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {
            "analysis_marks",
            "observed_facts",
            "ix_observed_span",
            "ix_observed_group",
        } <= tables
        assert store.get_mark("a", "wall_clock_overlap") is not None

    def test_a_fresh_database_has_the_detection_tables_and_they_can_be_emptied(
        self, sqlite_path: Path
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        claim = make_claim()
        repository.update(claim.id, lambda _existing: claim)
        store = SqliteDetectionStore(sqlite_path)
        store.put_mark(
            Mark("a", "wall_clock_overlap", "1", "i:f", MarkOutcome.FOUND, 1),
            analyzed_at="2026-03-01T00:00:00+00:00",
        )

        with closing(connect(sqlite_path)) as conn:
            conn.execute("DELETE FROM analysis_marks")
            conn.execute("DELETE FROM observed_facts")
            conn.commit()

        assert store.get_mark("a", "wall_clock_overlap") is None
        assert SqliteRelationshipRepository(sqlite_path).get(claim.id) is not None

    def test_a_migrated_claim_is_readable_and_healthy(self, sqlite_path: Path) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        claim = make_claim()
        repository.update(claim.id, lambda _existing: claim)
        with closing(connect(sqlite_path)) as conn:
            conn.execute("UPDATE relationships SET endpoint_missing_since = NULL")
            conn.commit()

        loaded = SqliteRelationshipRepository(sqlite_path).get(claim.id)

        assert loaded is not None
        assert loaded.endpoint_missing_since is None

    def test_a_database_from_a_newer_version_is_still_refused(self, sqlite_path: Path) -> None:
        SqliteReconciliationStore(sqlite_path).load_state()
        with closing(connect(sqlite_path)) as conn:
            conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")

        with pytest.raises(PersistenceError, match="newer version"):
            SqliteReconciliationStore(sqlite_path).load_state()

    def test_the_migration_applies_once_when_processes_start_together(
        self, sqlite_path: Path
    ) -> None:
        errors: list[BaseException] = []
        barrier = threading.Barrier(6)

        def worker() -> None:
            try:
                barrier.wait(timeout=10)
                SqliteReconciliationStore(sqlite_path).load_state()
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []

    def test_the_sweep_walks_the_primary_key_index_without_sorting(self, sqlite_path: Path) -> None:
        SqliteReconciliationStore(sqlite_path).load_state()

        with closing(connect(sqlite_path)) as conn:
            plan = " ".join(
                r[3]
                for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT relationship_id, asset_a, asset_b "
                    "FROM relationships "
                    "WHERE relationship_id > ? ORDER BY relationship_id LIMIT 10",
                    ("",),
                )
            )
        assert "INDEX" in plan
        assert "TEMP B-TREE" not in plan


class TestSqliteCorruption:
    def test_an_unreadable_claim_is_a_corrupt_claim_not_a_library_fault(
        self, sqlite_path: Path
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        claim = make_claim()
        repository.update(claim.id, lambda _existing: claim)
        with closing(connect(sqlite_path)) as conn:
            conn.execute("UPDATE relationships SET basis = 'not json'")
            conn.commit()
        store = SqliteReconciliationStore(sqlite_path)

        with pytest.raises(CorruptClaim):
            store.reconcile_claim(claim.id.value, noop, now=LATER)

        assert [r.relationship_id for r in store.claims_after(None, 10)] == [claim.id.value]

    def test_a_corrupt_claim_is_still_a_persistence_error_for_other_callers(
        self, sqlite_path: Path
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        claim = make_claim()
        repository.update(claim.id, lambda _existing: claim)
        with closing(connect(sqlite_path)) as conn:
            conn.execute("UPDATE relationships SET basis = 'not json'")
            conn.commit()

        with pytest.raises(PersistenceError):
            repository.get(claim.id)
