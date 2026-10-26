"""The relationship store: one contract, run against the in-memory fake AND the SQLite adapter.

SQLite-only tests (schema, migration, corruption, indexes) follow the shared contract.
"""

import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.repository import (
    RelationshipCriteria,
    RelationshipRepository,
)
from media_house.modules.asset_relationships.domain.values import (
    Grade,
    OffsetOrigin,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.asset_relationships.infrastructure.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)
from media_house.shared.errors import InvariantViolation, PersistenceError
from tests.support.relationship_fakes import (
    CONTAINER,
    T0,
    WALL,
    InMemoryRelationshipRepository,
    assertion,
    clock_offset,
    interval,
    tag_match,
    wall_clock,
)

pytestmark = pytest.mark.integration

TAKE = RelationshipType.ALTERNATE_TAKE_OF
OVERLAP = RelationshipType.TEMPORALLY_OVERLAPS_WITH
LATER = T0 + timedelta(hours=1)


def take(a: str = "a", b: str = "b", *, same_reel: bool | None = None) -> AssetRelationship:
    claim = ClaimInput.of(
        TAKE,
        a,
        b,
        origin=RelationshipOrigin.DETECTOR,
        basis=Basis("tag-detector", "1", {"inspection:a": "v1"}),
        evidence=(tag_match(same_reel=same_reel), assertion("seen on set")),
    )
    return AssetRelationship.create(claim, now=T0)


def overlap(a: str = "a", b: str = "b") -> AssetRelationship:
    claim = ClaimInput.of(
        OVERLAP,
        a,
        b,
        origin=RelationshipOrigin.USER,
        basis=Basis.manual(),
        evidence=(
            wall_clock("same_device", offset=clock_offset(5, 1, OffsetOrigin.USER_ASSERTED)),
        ),
        first_interval=interval(0, 10, WALL),
        second_interval=interval(5, 15, CONTAINER),
        offset=clock_offset(5, 1, OffsetOrigin.USER_ASSERTED),
    )
    return AssetRelationship.create(claim, now=T0)


def put(repository: RelationshipRepository, relationship: AssetRelationship) -> AssetRelationship:
    stored = repository.update(relationship.id, lambda _existing: relationship)
    assert stored is not None
    return stored


@pytest.fixture(params=["memory", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> RelationshipRepository:
    if request.param == "memory":
        return InMemoryRelationshipRepository()
    return SqliteRelationshipRepository(tmp_path / "asset_relationships.db")


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    return tmp_path / "asset_relationships.db"


@pytest.fixture
def db(sqlite_path: Path) -> Callable[[], sqlite3.Connection]:
    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    return connect


class TestContract:
    def test_a_missing_relationship_is_none(self, repository: RelationshipRepository) -> None:
        assert repository.get(RelationshipId("nothing")) is None

    def test_a_claim_round_trips_completely(self, repository: RelationshipRepository) -> None:
        original = take(same_reel=True)
        original.confirm(now=LATER, note="checked on set")
        put(repository, original)

        loaded = repository.get(original.id)

        assert loaded is not None
        assert loaded.id == original.id
        assert (loaded.type, loaded.asset_a, loaded.asset_b) == (TAKE, "a", "b")
        assert (loaded.status, loaded.grade, loaded.origin) == (
            RelationshipStatus.CONFIRMED,
            Grade.CORROBORATED,
            RelationshipOrigin.DETECTOR,
        )
        assert loaded.basis == original.basis
        assert loaded.evidence == original.evidence
        assert loaded.history == original.history
        assert (loaded.created_at, loaded.updated_at) == (T0, LATER)

    def test_intervals_offset_and_exact_fractions_round_trip(
        self, repository: RelationshipRepository
    ) -> None:
        original = overlap()
        put(repository, original)

        loaded = repository.get(original.id)

        assert loaded is not None
        assert loaded.a_interval == original.a_interval
        assert loaded.b_interval == original.b_interval
        assert loaded.offset == original.offset
        assert loaded.evidence[0].offset == original.evidence[0].offset

    def test_nothing_comes_back_as_a_live_object(self, repository: RelationshipRepository) -> None:
        original = take()
        put(repository, original)

        first = repository.get(original.id)
        assert first is not None
        first.confirm(now=LATER)

        again = repository.get(original.id)
        assert again is not None
        assert again.status is RelationshipStatus.PROPOSED

    def test_returning_none_from_a_change_writes_nothing(
        self, repository: RelationshipRepository
    ) -> None:
        original = take()
        put(repository, original)

        kept = repository.update(original.id, lambda _existing: None)

        assert kept is not None
        assert kept.updated_at == T0
        assert repository.update(RelationshipId("absent"), lambda _existing: None) is None

    def test_an_error_in_a_change_writes_nothing(self, repository: RelationshipRepository) -> None:
        original = take()
        put(repository, original)

        def broken(existing: AssetRelationship | None) -> AssetRelationship | None:
            if existing is not None:
                existing.confirm(now=LATER)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            repository.update(original.id, broken)
        with pytest.raises(RuntimeError):
            repository.update(RelationshipId("absent"), broken)

        stored = repository.get(original.id)
        assert stored is not None
        assert stored.status is RelationshipStatus.PROPOSED
        assert repository.get(RelationshipId("absent")) is None

    def test_a_change_cannot_move_a_relationship_to_another_id(
        self, repository: RelationshipRepository
    ) -> None:
        with pytest.raises(InvariantViolation):
            repository.update(take("a", "b").id, lambda _existing: take("a", "c"))

        assert repository.get(take("a", "c").id) is None

    def test_a_pair_is_one_row_however_often_it_is_written(
        self, repository: RelationshipRepository
    ) -> None:
        for _ in range(3):
            put(repository, take())

        found = repository.find(RelationshipCriteria("a"))

        assert len(found) == 1

    def test_find_is_symmetric_in_the_endpoints(self, repository: RelationshipRepository) -> None:
        put(repository, take("a", "b"))

        assert [r.id for r in repository.find(RelationshipCriteria("a"))] == [take("a", "b").id]
        assert [r.id for r in repository.find(RelationshipCriteria("b"))] == [take("a", "b").id]
        assert repository.find(RelationshipCriteria("c")) == []

    def test_find_filters_every_dimension(self, repository: RelationshipRepository) -> None:
        strong, weak, user = take("a", "b", same_reel=True), take("a", "c"), overlap("a", "d")
        for item in (strong, weak, user):
            put(repository, item)
        weak.reject(now=LATER)
        put(repository, weak)

        def ids(**criteria: object) -> set[RelationshipId]:
            return {r.id for r in repository.find(RelationshipCriteria("a", **criteria))}  # type: ignore[arg-type]

        assert ids() == {strong.id, weak.id, user.id}
        assert ids(types=frozenset({OVERLAP})) == {user.id}
        assert ids(statuses=frozenset({RelationshipStatus.REJECTED})) == {weak.id}
        assert ids(grades=frozenset({Grade.CORROBORATED})) == {strong.id}
        assert ids(origins=frozenset({RelationshipOrigin.USER})) == {user.id}
        assert ids(types=frozenset({TAKE}), statuses=frozenset({RelationshipStatus.PROPOSED})) == {
            strong.id
        }

    def test_find_orders_by_id_and_pages_with_a_keyset(
        self, repository: RelationshipRepository
    ) -> None:
        for other in "bcdef":
            put(repository, take("a", other))
        expected = sorted(take("a", other).id.value for other in "bcdef")

        first = repository.find(RelationshipCriteria("a", limit=2))
        second = repository.find(RelationshipCriteria("a", limit=2, after=first[-1].id.value))
        rest = repository.find(RelationshipCriteria("a", limit=10, after=second[-1].id.value))

        assert [r.id.value for r in [*first, *second, *rest]] == expected
        assert [len(first), len(second), len(rest)] == [2, 2, 1]

    def test_history_is_append_only_across_writes(self, repository: RelationshipRepository) -> None:
        item = take()
        put(repository, item)
        item.confirm(now=LATER)
        put(repository, item)
        put(repository, item)

        loaded = repository.get(item.id)

        assert loaded is not None
        assert [h.status_to for h in loaded.history] == [
            RelationshipStatus.PROPOSED,
            RelationshipStatus.CONFIRMED,
        ]

    def test_superseded_evidence_of_a_decided_claim_is_kept(
        self, repository: RelationshipRepository
    ) -> None:
        item = take()
        item.confirm(now=LATER)
        item.refresh(
            ClaimInput.of(
                TAKE,
                "a",
                "b",
                origin=RelationshipOrigin.DETECTOR,
                basis=Basis("tag-detector", "2"),
                evidence=(tag_match(same_device=True),),
            ),
            now=LATER + timedelta(hours=1),
        )
        put(repository, item)

        loaded = repository.get(item.id)

        assert loaded is not None
        assert [e.superseded for e in loaded.evidence] == [False, False, True]
        assert loaded.outdated_since == LATER + timedelta(hours=1)

    def test_racing_writers_converge_on_one_relationship(
        self, repository: RelationshipRepository
    ) -> None:
        """Eight threads each try to create the same pair: exactly one creates, the rest see it."""
        created: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def change(existing: AssetRelationship | None) -> AssetRelationship | None:
            if existing is not None:
                return None
            created.append(threading.current_thread().name)
            return take()

        def worker() -> None:
            try:
                barrier.wait(timeout=10)
                repository.update(take().id, change)
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread below
                errors.append(exc)

        threads = [threading.Thread(target=worker, name=f"w{i}") for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert len(created) == 1
        assert len(repository.find(RelationshipCriteria("a"))) == 1


class TestSqliteSchema:
    def test_a_new_database_is_migrated_to_the_latest_version(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        SqliteRelationshipRepository(sqlite_path).get(RelationshipId("x"))

        with closing(db()) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert version == LATEST_VERSION == len(MIGRATIONS)
        assert {"relationships", "relationship_evidence", "decision_history"} <= tables

    def test_reopening_does_not_migrate_again_and_keeps_data(self, sqlite_path: Path) -> None:
        item = take()
        put(SqliteRelationshipRepository(sqlite_path), item)

        reopened = SqliteRelationshipRepository(sqlite_path)

        assert reopened.get(item.id) is not None

    def test_a_database_from_a_newer_version_is_refused(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        SqliteRelationshipRepository(sqlite_path).get(RelationshipId("x"))
        with closing(db()) as conn:
            conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")

        with pytest.raises(PersistenceError, match="newer version"):
            SqliteRelationshipRepository(sqlite_path).get(RelationshipId("x"))

    def test_concurrent_first_use_applies_the_migration_once(self, sqlite_path: Path) -> None:
        errors: list[BaseException] = []
        barrier = threading.Barrier(6)

        def worker() -> None:
            try:
                barrier.wait(timeout=10)
                SqliteRelationshipRepository(sqlite_path).get(RelationshipId("x"))
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("status", "bogus"),
            ("grade", "bogus"),
            ("symmetric", 2),
            ("asset_b", "a"),  # same as asset_a
            ("a_domain", "wall_clock"),  # half-set interval
            ("offset_num", 1),  # half-set offset
        ],
    )
    def test_the_database_enforces_the_invariants_itself(
        self,
        sqlite_path: Path,
        db: Callable[[], sqlite3.Connection],
        column: str,
        value: object,
    ) -> None:
        item = take()
        put(SqliteRelationshipRepository(sqlite_path), item)

        with closing(db()) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"UPDATE relationships SET {column} = ? WHERE relationship_id = ?",  # noqa: S608
                (value, item.id.value),
            )

    def test_symmetric_pairs_must_be_stored_in_order(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        item = take()
        put(SqliteRelationshipRepository(sqlite_path), item)

        with closing(db()) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE relationships SET asset_a = 'z' WHERE relationship_id = ?", (item.id.value,)
            )

    def test_one_row_per_type_and_pair(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        item = take()
        put(SqliteRelationshipRepository(sqlite_path), item)

        with closing(db()) as conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(relationships)")]
            others = ", ".join(c for c in columns if c != "relationship_id")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(  # a second row for the same (type, asset_a, asset_b)
                    f"INSERT INTO relationships (relationship_id, {others}) "  # noqa: S608
                    f"SELECT 'other', {others} FROM relationships"
                )

    def test_deleting_a_relationship_removes_its_evidence_and_history(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        item = take()
        put(SqliteRelationshipRepository(sqlite_path), item)

        with closing(db()) as conn:
            conn.execute("DELETE FROM relationships")
            conn.commit()
            evidence = conn.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0]
            history = conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0]
        assert (evidence, history) == (0, 0)

    def test_an_unreadable_row_is_a_persistence_error_naming_it(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        item = take()
        put(repository, item)
        with closing(db()) as conn:
            conn.execute("UPDATE relationships SET basis = 'not json'")
            conn.commit()

        with pytest.raises(PersistenceError, match=item.id.value):
            repository.get(item.id)
        with pytest.raises(PersistenceError):
            repository.find(RelationshipCriteria("a"))

    def test_corrupt_evidence_is_a_persistence_error(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection]
    ) -> None:
        repository = SqliteRelationshipRepository(sqlite_path)
        item = take()
        put(repository, item)
        with closing(db()) as conn:
            conn.execute("UPDATE relationship_evidence SET kind = 'invented'")
            conn.commit()

        with pytest.raises(PersistenceError):
            repository.get(item.id)

    @pytest.mark.parametrize("asset", ["a", "b"])
    def test_lookup_by_either_endpoint_uses_an_index(
        self, sqlite_path: Path, db: Callable[[], sqlite3.Connection], asset: str
    ) -> None:
        put(SqliteRelationshipRepository(sqlite_path), take())

        with closing(db()) as conn:
            plan = " ".join(
                row[3]
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM relationships "
                    "WHERE (asset_a = ? OR asset_b = ?) ORDER BY relationship_id LIMIT 10",
                    (asset, asset),
                )
            )
        assert "ix_relationships_a" in plan
        assert "ix_relationships_b" in plan
        assert "SCAN relationships" not in plan.replace("USING INDEX", "")
