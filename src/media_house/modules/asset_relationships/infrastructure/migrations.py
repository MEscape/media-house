"""Schema migrations for ``asset_relationships.db``.

Ordered, append-only, applied transactionally and versioned through ``PRAGMA user_version`` (the
project's persistence convention, ADR-0007). To change the schema, *append* a new
:class:`Migration` with the next version number; never edit a released one.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


_INTERVAL_PARTS = ("domain", "origin", "start_num", "start_den", "end_num", "end_den")


def _interval_columns(side: str) -> str:
    """Columns of one time interval."""
    return (
        f"{side}_domain TEXT, {side}_origin TEXT, "
        f"{side}_start_num INTEGER, {side}_start_den INTEGER, "
        f"{side}_end_num INTEGER, {side}_end_den INTEGER"
    )


def _interval_checks(side: str) -> str:
    """All of an interval's columns are NULL or all are set, and denominators are positive."""
    names = [f"{side}_{part}" for part in _INTERVAL_PARTS]
    all_null = " AND ".join(f"{n} IS NULL" for n in names)
    all_set = " AND ".join(f"{n} IS NOT NULL" for n in names)
    return (
        f"CHECK (({all_null}) OR ({all_set})), "
        f"CHECK ({side}_start_den IS NULL OR ({side}_start_den > 0 AND {side}_end_den > 0))"
    )


_V1_INITIAL = Migration(
    version=1,
    name="initial asset relationships schema",
    statements=(
        f"""
        CREATE TABLE relationships (
            relationship_id TEXT PRIMARY KEY,
            type            TEXT NOT NULL,
            symmetric       INTEGER NOT NULL CHECK (symmetric IN (0, 1)),
            asset_a         TEXT NOT NULL,
            asset_b         TEXT NOT NULL,
            status          TEXT NOT NULL
                CHECK (status IN ('proposed', 'confirmed', 'rejected', 'stale')),
            grade           TEXT NOT NULL
                CHECK (grade IN ('recorded', 'corroborated', 'indicated', 'suggestive')),
            origin          TEXT NOT NULL,
            basis           TEXT NOT NULL,
            contradicted    INTEGER NOT NULL DEFAULT 0 CHECK (contradicted IN (0, 1)),
            {_interval_columns("a")},
            {_interval_columns("b")},
            offset_num      INTEGER,
            offset_den      INTEGER,
            offset_unc_num  INTEGER,
            offset_unc_den  INTEGER,
            offset_origin   TEXT,
            outdated_since  TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            {_interval_checks("a")},
            {_interval_checks("b")},
            CHECK (asset_a <> asset_b),
            CHECK (symmetric = 0 OR asset_a < asset_b),
            CHECK ((offset_num IS NULL) = (offset_den IS NULL)
                   AND (offset_num IS NULL) = (offset_origin IS NULL)
                   AND (offset_den IS NULL OR offset_den > 0)),
            CHECK ((offset_unc_num IS NULL) = (offset_unc_den IS NULL)
                   AND (offset_unc_den IS NULL OR (offset_unc_den > 0 AND offset_unc_num >= 0))),
            UNIQUE (type, asset_a, asset_b)
        )
        """,
        # Forward and reverse lookup without storing both directions of a pair.
        "CREATE INDEX ix_relationships_a ON relationships (asset_a, type, status)",
        "CREATE INDEX ix_relationships_b ON relationships (asset_b, type, status)",
        "CREATE INDEX ix_relationships_status ON relationships (status, updated_at)",
        """
        CREATE TABLE relationship_evidence (
            evidence_id      INTEGER PRIMARY KEY,
            relationship_id  TEXT NOT NULL
                REFERENCES relationships (relationship_id) ON DELETE CASCADE,
            kind             TEXT NOT NULL,
            provider         TEXT NOT NULL,
            provider_version TEXT NOT NULL,
            detail           TEXT NOT NULL,
            offset_num       INTEGER,
            offset_den       INTEGER,
            offset_unc_num   INTEGER,
            offset_unc_den   INTEGER,
            offset_origin    TEXT,
            observed_at      TEXT NOT NULL,
            superseded       INTEGER NOT NULL DEFAULT 0 CHECK (superseded IN (0, 1)),
            CHECK ((offset_num IS NULL) = (offset_den IS NULL)
                   AND (offset_num IS NULL) = (offset_origin IS NULL)
                   AND (offset_den IS NULL OR offset_den > 0)),
            CHECK ((offset_unc_num IS NULL) = (offset_unc_den IS NULL)
                   AND (offset_unc_den IS NULL OR (offset_unc_den > 0 AND offset_unc_num >= 0)))
        )
        """,
        "CREATE INDEX ix_relationship_evidence_rel "
        "ON relationship_evidence (relationship_id, kind)",
        """
        CREATE TABLE decision_history (
            seq               INTEGER PRIMARY KEY,
            relationship_id   TEXT NOT NULL
                REFERENCES relationships (relationship_id) ON DELETE CASCADE,
            status_from       TEXT,
            status_to         TEXT NOT NULL,
            at                TEXT NOT NULL,
            note              TEXT NOT NULL,
            evidence_snapshot TEXT NOT NULL
        )
        """,
        "CREATE INDEX ix_decision_history_rel ON decision_history (relationship_id, seq)",
    ),
)

_V2_RECONCILIATION = Migration(
    version=2,
    name="reconciliation: missing-endpoint marker, sweep state, purge audit",
    statements=(
        # NULL = healthy. Not a status: it never changes what a person decided.
        "ALTER TABLE relationships ADD COLUMN endpoint_missing_since TEXT",
        "CREATE INDEX ix_relationships_missing ON relationships (endpoint_missing_since) "
        "WHERE endpoint_missing_since IS NOT NULL",
        # claim_cursor, sweep_generation, sweep_started_at, last_sweep_completed_at,
        # consecutive_failures, next_attempt_after, last_run
        "CREATE TABLE scan_state (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
        # One small append-only row per purged claim: the audit trail of decisions that would
        # otherwise vanish silently. It keeps no evidence.
        """
        CREATE TABLE purge_log (
            seq                    INTEGER PRIMARY KEY,
            relationship_id        TEXT NOT NULL,
            type                   TEXT NOT NULL,
            asset_a                TEXT NOT NULL,
            asset_b                TEXT NOT NULL,
            final_status           TEXT NOT NULL
                CHECK (final_status IN ('proposed', 'confirmed', 'rejected', 'stale')),
            decided_at             TEXT,
            endpoint_missing_since TEXT NOT NULL,
            purged_at              TEXT NOT NULL,
            reason                 TEXT NOT NULL
                CHECK (reason IN ('undecided_orphan', 'retention_expired'))
        )
        """,
        "CREATE INDEX ix_purge_log_assets ON purge_log (asset_a, asset_b)",
    ),
)

MIGRATIONS: tuple[Migration, ...] = (_V1_INITIAL, _V2_RECONCILIATION)
LATEST_VERSION = MIGRATIONS[-1].version
