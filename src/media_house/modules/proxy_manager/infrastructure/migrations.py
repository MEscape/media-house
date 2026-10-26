# ruff: noqa: S608 - the seed rows are built from fixed literals in this file.
"""Schema migrations of the proxy manager database.

Ordered, append-only, applied transactionally and versioned through ``PRAGMA user_version`` (the
project's persistence convention, ADR-0007). To change the schema APPEND a new :class:`Migration`;
never edit a released one. The allowed attempt transitions are seeded here as literal rows so the
database refuses an illegal move even from a buggy writer; a test keeps them equal to
``domain.attempt.TRANSITIONS``.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


_STAGES = ("queued", "generating", "validating", "publishing")
_ENDS = ("failed", "cancelled", "interrupted")
_MOVES = (
    ("queued", "generating"),
    ("generating", "validating"),
    ("validating", "publishing"),
    ("queued", "validating"),
    ("queued", "publishing"),
    ("publishing", "ready"),
    ("publishing", "superseded"),
    *((stage, end) for stage in _STAGES for end in _ENDS),
)
_SEED = "INSERT INTO attempt_transitions (from_state, to_state) VALUES " + ", ".join(
    f"('{a}', '{b}')" for a, b in _MOVES
)

_V1 = Migration(
    version=1,
    name="slots, artifacts and attempts",
    statements=(
        """
        CREATE TABLE attempts (
            seq               INTEGER PRIMARY KEY AUTOINCREMENT,
            id                TEXT NOT NULL UNIQUE,
            source_asset_id   TEXT NOT NULL,
            profile           TEXT NOT NULL,
            profile_version   INTEGER NOT NULL CHECK (profile_version >= 1),
            spec_fingerprint  TEXT NOT NULL CHECK (length(spec_fingerprint) = 64),
            state             TEXT NOT NULL CHECK (state IN ('queued', 'generating', 'validating',
                                  'publishing', 'ready', 'superseded', 'failed', 'cancelled',
                                  'interrupted')),
            owner_token       TEXT NOT NULL,
            scratch_name      TEXT,
            reserved_bytes    INTEGER NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
            reserved_volume   TEXT,
            replaces_asset_id TEXT,
            artifact_asset_id TEXT,
            encoder           TEXT,
            fallback          TEXT,
            toolchain         TEXT,
            failure           TEXT CHECK (failure IN ('transient', 'permanent', 'cancelled')),
            error_message     TEXT,
            created_at        TEXT NOT NULL,
            finished_at       TEXT,
            CHECK ((state IN ('failed', 'cancelled', 'interrupted')) = (failure IS NOT NULL)),
            CHECK ((state IN ('ready', 'superseded', 'failed', 'cancelled', 'interrupted'))
                   = (finished_at IS NOT NULL)),
            CHECK (state NOT IN ('ready', 'superseded') OR artifact_asset_id IS NOT NULL)
        )
        """,
        # One live generation per source + profile + exact specification, across processes.
        "CREATE UNIQUE INDEX ux_attempts_in_flight ON attempts "
        "(source_asset_id, profile, spec_fingerprint) "
        "WHERE state IN ('queued', 'generating', 'validating', 'publishing')",
        "CREATE INDEX ix_attempts_slot ON attempts (source_asset_id, profile, seq DESC)",
        "CREATE INDEX ix_attempts_fingerprint ON attempts (source_asset_id, spec_fingerprint)",
        "CREATE INDEX ix_attempts_reserved ON attempts (reserved_volume) "
        "WHERE state IN ('queued', 'generating', 'validating', 'publishing')",
        """
        CREATE TABLE attempt_transitions (
            from_state TEXT NOT NULL,
            to_state   TEXT NOT NULL,
            PRIMARY KEY (from_state, to_state)
        ) WITHOUT ROWID
        """,
        _SEED,
        """
        CREATE TRIGGER trg_attempt_transition BEFORE UPDATE OF state ON attempts
        WHEN new.state <> old.state AND NOT EXISTS (
            SELECT 1 FROM attempt_transitions
            WHERE from_state = old.state AND to_state = new.state)
        BEGIN SELECT RAISE(ABORT, 'illegal attempt state transition'); END
        """,
        """
        CREATE TABLE artifacts (
            asset_id          TEXT PRIMARY KEY,
            source_asset_id   TEXT NOT NULL,
            profile           TEXT NOT NULL,
            spec_fingerprint  TEXT NOT NULL CHECK (length(spec_fingerprint) = 64),
            attempt_id        TEXT NOT NULL REFERENCES attempts (id),
            verdict           TEXT NOT NULL CHECK (verdict IN ('valid', 'valid_with_transforms',
                                  'invalid', 'unverifiable')),
            evidence          TEXT NOT NULL CHECK (evidence IN ('packets', 'decoded')),
            validator_version INTEGER NOT NULL CHECK (validator_version >= 1),
            validated_at      TEXT NOT NULL,
            state             TEXT NOT NULL DEFAULT 'live' CHECK (state IN ('live', 'pruning')),
            UNIQUE (source_asset_id, spec_fingerprint)
        )
        """,
        """
        CREATE TABLE slots (
            source_asset_id   TEXT NOT NULL,
            profile           TEXT NOT NULL,
            active_asset_id   TEXT NOT NULL REFERENCES artifacts (asset_id),
            previous_asset_id TEXT REFERENCES artifacts (asset_id),
            activated_seq     INTEGER NOT NULL REFERENCES attempts (seq),
            updated_at        TEXT NOT NULL,
            PRIMARY KEY (source_asset_id, profile),
            CHECK (previous_asset_id IS NULL OR previous_asset_id <> active_asset_id)
        )
        """,
        "CREATE INDEX ix_slots_active ON slots (active_asset_id)",
        "CREATE INDEX ix_slots_previous ON slots (previous_asset_id) "
        "WHERE previous_asset_id IS NOT NULL",
    ),
)

_V2 = Migration(
    version=2,
    name="attempt heartbeat for lease takeover",
    statements=(
        # NULL on rows written before this migration: readers fall back to created_at
        "ALTER TABLE attempts ADD COLUMN heartbeat_at TEXT",
    ),
)

MIGRATIONS: tuple[Migration, ...] = (_V1, _V2)
LATEST_VERSION = MIGRATIONS[-1].version
