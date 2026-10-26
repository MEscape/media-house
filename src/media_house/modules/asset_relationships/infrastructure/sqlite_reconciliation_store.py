"""SQLite adapter for the reconciliation store: sweep state, per-claim evaluation, purge and audit.

It shares the database file and connection helper of the relationship repository. Every claim is
evaluated in its own ``BEGIN IMMEDIATE`` transaction, so what the rules see is the claim as it is
stored at that moment (a decision made a second ago is already there). The cursor moves in a
separate transaction by compare-and-set after the batch's claims were applied, so a crash leaves
the cursor behind (a batch is re-evaluated, which is idempotent) and never ahead.
"""

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from media_house.modules.asset_relationships.domain.reconciliation import (
    ClaimRef,
    ClaimVerdict,
    PurgeReason,
    ScanState,
)
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.values import RelationshipStatus
from media_house.modules.asset_relationships.infrastructure.sqlite_database import (
    SqliteRelationshipDatabase,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    load_claim,
    save_claim,
)
from media_house.shared.errors import InvariantViolation

_CURSOR = "claim_cursor"
_GENERATION = "sweep_generation"
_STARTED = "sweep_started_at"
_COMPLETED = "last_sweep_completed_at"
_FAILURES = "consecutive_failures"
_NEXT_ATTEMPT = "next_attempt_after"
_LAST_RUN = "last_run"
_DECISIONS = frozenset({RelationshipStatus.CONFIRMED, RelationshipStatus.REJECTED})


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _moment(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _read(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM scan_state")}


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO scan_state (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _drop(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM scan_state WHERE key = ?", (key,))


def _last_decision(claim: AssetRelationship) -> datetime | None:
    decided = [h.at for h in claim.history if h.status_to in _DECISIONS]
    return decided[-1] if decided else None


class SqliteReconciliationStore:
    """Structurally implements ``ReconciliationStore``; owns ``scan_state`` and ``purge_log``."""

    def __init__(self, database_path: Path) -> None:
        self._db = SqliteRelationshipDatabase(database_path)

    def claims_after(self, cursor: str | None, limit: int) -> Sequence[ClaimRef]:
        def work(conn: sqlite3.Connection) -> list[ClaimRef]:
            rows = conn.execute(
                "SELECT relationship_id, asset_a, asset_b, basis, "
                "endpoint_missing_since IS NOT NULL AS marked FROM relationships "
                "WHERE relationship_id > ? ORDER BY relationship_id LIMIT ?",
                (cursor or "", limit),
            ).fetchall()
            return [
                ClaimRef(
                    r["relationship_id"],
                    r["asset_a"],
                    r["asset_b"],
                    bool(r["marked"]),
                    r["basis"],
                )
                for r in rows
            ]

        return self._db.read(work)

    def reconcile_claim(
        self,
        relationship_id: str,
        decide: Callable[[AssetRelationship], ClaimVerdict],
        *,
        now: datetime,
    ) -> ClaimVerdict | None:
        def work(conn: sqlite3.Connection) -> ClaimVerdict | None:
            claim = load_claim(conn, relationship_id)
            if claim is None:
                return None
            verdict = decide(claim)
            if verdict.purge is not None:
                self._purge(conn, claim, verdict.purge, now)
            elif verdict.changed:
                save_claim(conn, claim)
            return verdict

        return self._db.write(work)

    @staticmethod
    def _purge(
        conn: sqlite3.Connection, claim: AssetRelationship, reason: PurgeReason, now: datetime
    ) -> None:
        if claim.endpoint_missing_since is None:
            raise InvariantViolation("A claim is purged only after its endpoint was marked missing")
        decided_at = _last_decision(claim)
        conn.execute(
            "INSERT INTO purge_log (relationship_id, type, asset_a, asset_b, final_status, "
            "decided_at, endpoint_missing_since, purged_at, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                claim.id.value,
                claim.type.value,
                claim.asset_a,
                claim.asset_b,
                claim.status.value,
                None if decided_at is None else _stamp(decided_at),
                _stamp(claim.endpoint_missing_since),
                _stamp(now),
                reason.value,
            ),
        )
        # evidence and history go with the claim (ON DELETE CASCADE); the log row is what remains
        conn.execute("DELETE FROM relationships WHERE relationship_id = ?", (claim.id.value,))

    def load_state(self) -> ScanState:
        def work(conn: sqlite3.Connection) -> ScanState:
            values: Mapping[str, str] = _read(conn)
            return ScanState(
                claim_cursor=values.get(_CURSOR),
                sweep_generation=int(values.get(_GENERATION, "0")),
                sweep_started_at=_moment(values.get(_STARTED)),
                last_sweep_completed_at=_moment(values.get(_COMPLETED)),
                consecutive_failures=int(values.get(_FAILURES, "0")),
                next_attempt_after=_moment(values.get(_NEXT_ATTEMPT)),
                last_run=values.get(_LAST_RUN),
            )

        return self._db.read(work)

    def advance_cursor(self, expected: str | None, new: str, *, now: datetime) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            if _read(conn).get(_CURSOR) != expected:
                return False  # another run moved it first
            _set(conn, _CURSOR, new)
            if expected is None:
                _set(conn, _STARTED, _stamp(now))
            return True

        return self._db.write(work)

    def complete_sweep(self, expected: str | None, *, now: datetime) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            values = _read(conn)
            if values.get(_CURSOR) != expected:
                return False
            _drop(conn, _CURSOR)
            _set(conn, _GENERATION, str(int(values.get(_GENERATION, "0")) + 1))
            _set(conn, _COMPLETED, _stamp(now))
            return True

        return self._db.write(work)

    def record_run(
        self,
        *,
        last_run: str | None,
        consecutive_failures: int,
        next_attempt_after: datetime | None,
        now: datetime,
    ) -> None:
        def work(conn: sqlite3.Connection) -> None:
            if last_run is not None:
                _set(conn, _LAST_RUN, last_run)
            _set(conn, _FAILURES, str(consecutive_failures))
            if next_attempt_after is None:
                _drop(conn, _NEXT_ATTEMPT)
            else:
                _set(conn, _NEXT_ATTEMPT, _stamp(next_attempt_after))

        self._db.write(work)
