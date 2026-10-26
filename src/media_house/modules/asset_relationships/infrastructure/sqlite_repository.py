# ruff: noqa: S608 - SQL text is joined only from fixed fragments and generated '?' markers.
"""SQLite adapter for the relationship store.

Rows are mapped to the domain explicitly. A row that cannot be turned back into a valid
relationship is a ``PersistenceError`` naming the relationship, never a half-built object.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.errors import CorruptClaim
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.intervals import (
    ClockOffset,
    TimeCoordinates,
    TimeInterval,
)
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    DecisionRecord,
)
from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.domain.taxonomy import spec_of
from media_house.modules.asset_relationships.domain.values import (
    ClockDomain,
    EvidenceKind,
    Grade,
    OffsetOrigin,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_database import (
    SqliteRelationshipDatabase,
)
from media_house.shared.errors import DomainError, InvariantViolation

_CHUNK = 500


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"naive timestamp {text!r}")
    return moment


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


# ---------------------------------------------------------------------------- value <-> columns
def _interval_values(interval: TimeInterval | None) -> tuple[object, ...]:
    if interval is None:
        return (None,) * 6
    return (
        interval.coordinates.domain.value,
        interval.coordinates.origin,
        interval.start.numerator,
        interval.start.denominator,
        interval.end.numerator,
        interval.end.denominator,
    )


def _interval(row: sqlite3.Row, side: str) -> TimeInterval | None:
    if row[f"{side}_domain"] is None:
        return None
    return TimeInterval(
        Fraction(row[f"{side}_start_num"], row[f"{side}_start_den"]),
        Fraction(row[f"{side}_end_num"], row[f"{side}_end_den"]),
        TimeCoordinates(ClockDomain(row[f"{side}_domain"]), row[f"{side}_origin"]),
    )


def _offset_values(offset: ClockOffset | None) -> tuple[object, ...]:
    if offset is None:
        return (None,) * 5
    unc = offset.uncertainty
    return (
        offset.seconds.numerator,
        offset.seconds.denominator,
        None if unc is None else unc.numerator,
        None if unc is None else unc.denominator,
        offset.origin.value,
    )


def _offset(row: sqlite3.Row) -> ClockOffset | None:
    if row["offset_num"] is None:
        return None
    unc = (
        None
        if row["offset_unc_num"] is None
        else Fraction(row["offset_unc_num"], row["offset_unc_den"])
    )
    return ClockOffset(
        Fraction(row["offset_num"], row["offset_den"]), unc, OffsetOrigin(row["offset_origin"])
    )


_INSERT = (
    "INSERT INTO relationships (relationship_id, type, symmetric, asset_a, asset_b, status, "
    "grade, origin, basis, contradicted, "
    "a_domain, a_origin, a_start_num, a_start_den, a_end_num, a_end_den, "
    "b_domain, b_origin, b_start_num, b_start_den, b_end_num, b_end_den, "
    "offset_num, offset_den, offset_unc_num, offset_unc_den, offset_origin, "
    "outdated_since, endpoint_missing_since, created_at, updated_at) VALUES ("
    + _placeholders(31)
    + ")"
)
_UPDATE = (
    "UPDATE relationships SET status = ?, grade = ?, origin = ?, basis = ?, contradicted = ?, "
    "a_domain = ?, a_origin = ?, a_start_num = ?, a_start_den = ?, a_end_num = ?, a_end_den = ?, "
    "b_domain = ?, b_origin = ?, b_start_num = ?, b_start_den = ?, b_end_num = ?, b_end_den = ?, "
    "offset_num = ?, offset_den = ?, offset_unc_num = ?, offset_unc_den = ?, offset_origin = ?, "
    "outdated_since = ?, endpoint_missing_since = ?, updated_at = ? WHERE relationship_id = ?"
)


def _row_values(r: AssetRelationship) -> tuple[object, ...]:
    """The mutable columns, in the order ``_UPDATE`` assigns them (without the id)."""
    return (
        r.status.value,
        r.grade.value,
        r.origin.value,
        r.basis.canonical(),
        int(r.contradicted),
        *_interval_values(r.a_interval),
        *_interval_values(r.b_interval),
        *_offset_values(r.offset),
        None if r.outdated_since is None else _stamp(r.outdated_since),
        None if r.endpoint_missing_since is None else _stamp(r.endpoint_missing_since),
        _stamp(r.updated_at),
    )


# ---------------------------------------------------------------------------- write helpers
def _save(conn: sqlite3.Connection, r: AssetRelationship, *, is_new: bool) -> None:
    values = _row_values(r)
    if is_new:
        conn.execute(
            _INSERT,
            (
                r.id.value,
                r.type.value,
                int(spec_of(r.type).symmetric),
                r.asset_a,
                r.asset_b,
                *values[:-1],  # status .. endpoint_missing_since
                _stamp(r.created_at),
                values[-1],  # updated_at
            ),
        )
    else:
        conn.execute(_UPDATE, (*values, r.id.value))
    conn.execute("DELETE FROM relationship_evidence WHERE relationship_id = ?", (r.id.value,))
    for e in r.evidence:
        conn.execute(
            "INSERT INTO relationship_evidence (relationship_id, kind, provider, provider_version, "
            "detail, offset_num, offset_den, offset_unc_num, offset_unc_den, offset_origin, "
            "observed_at, superseded) VALUES (" + _placeholders(12) + ")",
            (
                r.id.value,
                e.kind.value,
                e.provider,
                e.provider_version,
                json.dumps(dict(e.detail), sort_keys=True),
                *_offset_values(e.offset),
                _stamp(e.observed_at),
                int(e.superseded),
            ),
        )
    stored = int(
        conn.execute(
            "SELECT COUNT(*) FROM decision_history WHERE relationship_id = ?", (r.id.value,)
        ).fetchone()[0]
    )
    for h in r.history[stored:]:  # history is append-only
        conn.execute(
            "INSERT INTO decision_history (relationship_id, status_from, status_to, at, note, "
            "evidence_snapshot) VALUES (?, ?, ?, ?, ?, ?)",
            (
                r.id.value,
                None if h.status_from is None else h.status_from.value,
                h.status_to.value,
                _stamp(h.at),
                h.note,
                json.dumps(list(h.evidence_snapshot)),
            ),
        )


# ---------------------------------------------------------------------------- read helpers
def _hydrate(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[AssetRelationship]:
    ids = [row["relationship_id"] for row in rows]
    evidence: dict[str, list[sqlite3.Row]] = {i: [] for i in ids}
    history: dict[str, list[sqlite3.Row]] = {i: [] for i in ids}
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        marks = _placeholders(len(chunk))
        for e in conn.execute(
            f"SELECT * FROM relationship_evidence WHERE relationship_id IN ({marks}) "
            "ORDER BY evidence_id",
            chunk,
        ):
            evidence[e["relationship_id"]].append(e)
        for h in conn.execute(
            f"SELECT * FROM decision_history WHERE relationship_id IN ({marks}) ORDER BY seq",
            chunk,
        ):
            history[h["relationship_id"]].append(h)
    result: list[AssetRelationship] = []
    for row in rows:
        rid = row["relationship_id"]
        try:
            result.append(_to_domain(row, evidence[rid], history[rid]))
        except (ValueError, KeyError, TypeError, DomainError) as exc:
            raise CorruptClaim(
                f"Stored relationship {rid} is unreadable: {exc}",
                details={"relationship_id": rid},
            ) from exc
    return result


def _to_domain(
    row: sqlite3.Row, evidence: Sequence[sqlite3.Row], history: Sequence[sqlite3.Row]
) -> AssetRelationship:
    return AssetRelationship(
        id=RelationshipId(row["relationship_id"]),
        type=RelationshipType(row["type"]),
        asset_a=row["asset_a"],
        asset_b=row["asset_b"],
        status=RelationshipStatus(row["status"]),
        grade=Grade(row["grade"]),
        origin=RelationshipOrigin(row["origin"]),
        basis=Basis.from_canonical(row["basis"]),
        evidence=tuple(_evidence(e) for e in evidence),
        history=tuple(
            DecisionRecord(
                None if h["status_from"] is None else RelationshipStatus(h["status_from"]),
                RelationshipStatus(h["status_to"]),
                _moment(h["at"]),
                h["note"],
                tuple(str(k) for k in json.loads(h["evidence_snapshot"])),
            )
            for h in history
        ),
        created_at=_moment(row["created_at"]),
        updated_at=_moment(row["updated_at"]),
        a_interval=_interval(row, "a"),
        b_interval=_interval(row, "b"),
        offset=_offset(row),
        contradicted=bool(row["contradicted"]),
        outdated_since=None if row["outdated_since"] is None else _moment(row["outdated_since"]),
        endpoint_missing_since=(
            None
            if row["endpoint_missing_since"] is None
            else _moment(row["endpoint_missing_since"])
        ),
    )


def _evidence(row: sqlite3.Row) -> RelationshipEvidence:
    return RelationshipEvidence(
        kind=EvidenceKind(row["kind"]),
        provider=row["provider"],
        provider_version=row["provider_version"],
        observed_at=_moment(row["observed_at"]),
        detail=json.loads(row["detail"]),
        offset=_offset(row),
        superseded=bool(row["superseded"]),
    )


def load_claim(conn: sqlite3.Connection, relationship_id: str) -> AssetRelationship | None:
    """The claim with this id, or ``None``; a damaged one raises ``CorruptClaim``."""
    row = conn.execute(
        "SELECT * FROM relationships WHERE relationship_id = ?", (relationship_id,)
    ).fetchone()
    return _hydrate(conn, [row])[0] if row else None


def save_claim(conn: sqlite3.Connection, claim: AssetRelationship) -> None:
    """Write the changes of an EXISTING claim (the caller holds the write transaction)."""
    _save(conn, claim, is_new=False)


class SqliteRelationshipRepository:
    """Structurally implements ``RelationshipRepository``; owns ``asset_relationships.db``."""

    def __init__(self, database_path: Path) -> None:
        self._db = SqliteRelationshipDatabase(database_path)

    def update(
        self,
        relationship_id: RelationshipId,
        change: Callable[[AssetRelationship | None], AssetRelationship | None],
    ) -> AssetRelationship | None:
        def work(conn: sqlite3.Connection) -> AssetRelationship | None:
            found = self._load(conn, relationship_id.value)
            updated = change(found)
            if updated is None:
                return found
            if updated.id != relationship_id:
                raise InvariantViolation("A change cannot move a relationship to another id")
            _save(conn, updated, is_new=found is None)
            return updated

        return self._db.write(work)

    def get(self, relationship_id: RelationshipId) -> AssetRelationship | None:
        return self._db.read(lambda conn: self._load(conn, relationship_id.value))

    def find(self, criteria: RelationshipCriteria) -> Sequence[AssetRelationship]:
        clauses = ["(asset_a = ? OR asset_b = ?)"]
        params: list[object] = [criteria.asset_id, criteria.asset_id]
        for column, values in (
            ("type", sorted(t.value for t in criteria.types)),
            ("status", sorted(s.value for s in criteria.statuses)),
            ("grade", sorted(g.value for g in criteria.grades)),
            ("origin", sorted(o.value for o in criteria.origins)),
        ):
            if values:
                clauses.append(f"{column} IN ({_placeholders(len(values))})")
                params += values
        if criteria.after is not None:
            clauses.append("relationship_id > ?")
            params.append(criteria.after)
        sql = (
            f"SELECT * FROM relationships WHERE {' AND '.join(clauses)} "
            "ORDER BY relationship_id LIMIT ?"
        )
        params.append(criteria.limit)

        def work(conn: sqlite3.Connection) -> list[AssetRelationship]:
            return _hydrate(conn, conn.execute(sql, params).fetchall())

        return self._db.read(work)

    @staticmethod
    def _load(conn: sqlite3.Connection, relationship_id: str) -> AssetRelationship | None:
        row = conn.execute(
            "SELECT * FROM relationships WHERE relationship_id = ?", (relationship_id,)
        ).fetchone()
        return _hydrate(conn, [row])[0] if row else None
