"""SQLite adapter for the detection store: marks, indexed facts and the walk's cursors.

Shares the database file and connection helper of the relationship repository. Every operation is
one short transaction. Nothing here references a claim: the tables are derived and disposable.
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from media_house.modules.asset_relationships.domain.detection import Fact, Observed
from media_house.modules.asset_relationships.domain.detection_store import Mark, MarkOutcome
from media_house.modules.asset_relationships.infrastructure.sqlite_database import (
    SqliteRelationshipDatabase,
)
from media_house.shared.errors import PersistenceError


def _observed(row: sqlite3.Row) -> Observed:
    try:
        detail = json.loads(row["detail"])
        if not isinstance(detail, dict):
            raise ValueError("detail is not an object")
        return Observed(
            asset_id=row["asset_id"],
            identity=row["identity"],
            inspection_id=row["inspection_id"],
            fact=Fact(
                row["kind"],
                row["group_key"],
                row["start_us"],
                row["end_us"],
                detail,
            ),
        )
    except (ValueError, KeyError) as exc:
        raise PersistenceError(
            "A stored detection fact is unreadable", details={"asset": row["asset_id"]}
        ) from exc


class SqliteDetectionStore:
    """Structurally implements ``DetectionStore``."""

    def __init__(self, database_path: Path) -> None:
        self._db = SqliteRelationshipDatabase(database_path)

    def get_mark(self, asset_id: str, detector: str) -> Mark | None:
        def work(conn: sqlite3.Connection) -> Mark | None:
            row = conn.execute(
                "SELECT * FROM analysis_marks WHERE asset_id = ? AND detector = ?",
                (asset_id, detector),
            ).fetchone()
            if row is None:
                return None
            return Mark(
                row["asset_id"],
                row["detector"],
                row["detector_version"],
                row["input_identity"],
                MarkOutcome(row["outcome"]),
                row["candidates_found"],
            )

        return self._db.read(work)

    def put_mark(self, mark: Mark, *, analyzed_at: str) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO analysis_marks (asset_id, detector, detector_version, input_identity,"
                " outcome, candidates_found, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (asset_id, detector) DO UPDATE SET "
                "detector_version = excluded.detector_version, "
                "input_identity = excluded.input_identity, outcome = excluded.outcome, "
                "candidates_found = excluded.candidates_found, analyzed_at = excluded.analyzed_at",
                (
                    mark.asset_id,
                    mark.detector,
                    mark.detector_version,
                    mark.input_identity,
                    mark.outcome.value,
                    mark.candidates_found,
                    analyzed_at,
                ),
            )

        self._db.write(work)

    def replace_fact(
        self, asset_id: str, detector: str, fact: Fact | None, *, identity: str, inspection_id: str
    ) -> None:
        def work(conn: sqlite3.Connection) -> None:
            if fact is None:
                conn.execute(
                    "DELETE FROM observed_facts WHERE asset_id = ? AND detector = ?",
                    (asset_id, detector),
                )
                return
            conn.execute(
                "INSERT INTO observed_facts (asset_id, detector, kind, group_key, start_us, "
                "end_us, identity, inspection_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (asset_id, detector) DO UPDATE SET kind = excluded.kind, "
                "group_key = excluded.group_key, start_us = excluded.start_us, "
                "end_us = excluded.end_us, identity = excluded.identity, "
                "inspection_id = excluded.inspection_id, detail = excluded.detail",
                (
                    asset_id,
                    detector,
                    fact.kind,
                    fact.group_key,
                    fact.start_us,
                    fact.end_us,
                    identity,
                    inspection_id,
                    json.dumps(dict(fact.detail), sort_keys=True),
                ),
            )

        self._db.write(work)

    def overlapping(
        self, detector: str, start_us: int, end_us: int, *, exclude: str, limit: int
    ) -> Sequence[Observed]:
        def work(conn: sqlite3.Connection) -> list[Observed]:
            rows = conn.execute(
                "SELECT * FROM observed_facts WHERE detector = ? AND asset_id <> ? "
                "AND start_us < ? AND end_us > ? "
                "ORDER BY (MIN(end_us, ?) - MAX(start_us, ?)) DESC, asset_id LIMIT ?",
                (detector, exclude, end_us, start_us, end_us, start_us, limit),
            ).fetchall()
            return [_observed(r) for r in rows]

        return self._db.read(work)

    def in_group(
        self, detector: str, group_key: str, *, exclude: str, limit: int
    ) -> Sequence[Observed]:
        def work(conn: sqlite3.Connection) -> list[Observed]:
            rows = conn.execute(
                "SELECT * FROM observed_facts WHERE detector = ? AND group_key = ? "
                "AND asset_id <> ? ORDER BY asset_id LIMIT ?",
                (detector, group_key, exclude, limit),
            ).fetchall()
            return [_observed(r) for r in rows]

        return self._db.read(work)

    def get_state(self, key: str) -> str | None:
        def work(conn: sqlite3.Connection) -> str | None:
            row = conn.execute("SELECT value FROM scan_state WHERE key = ?", (key,)).fetchone()
            return None if row is None else str(row["value"])

        return self._db.read(work)

    def cas_state(self, key: str, expected: str | None, new: str | None) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            row = conn.execute("SELECT value FROM scan_state WHERE key = ?", (key,)).fetchone()
            if (None if row is None else row["value"]) != expected:
                return False
            if new is None:
                conn.execute("DELETE FROM scan_state WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO scan_state (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, new),
                )
            return True

        return self._db.write(work)
