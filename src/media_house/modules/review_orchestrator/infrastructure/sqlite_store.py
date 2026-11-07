"""SQLite implementation of :class:`ReviewStore`: the sessions and their append-only history.

One short-lived connection per operation (thread-safe by construction, ADR-0007). A write is one
``BEGIN IMMEDIATE`` transaction that checks the revision the caller read, so two writers never
interleave: the loser gets a ``ConflictError`` and applies its step to the newer session.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

from media_house.modules.review_orchestrator.application.ports import StoredSession
from media_house.modules.review_orchestrator.domain.errors import InvalidReviewDocument
from media_house.modules.review_orchestrator.domain.serialization import (
    entry_from_json,
    entry_to_json,
    session_from_json,
    session_to_json,
)
from media_house.modules.review_orchestrator.domain.session import AuditEntry, ReviewSession
from media_house.modules.review_orchestrator.domain.values import TERMINAL_STATES
from media_house.shared.errors import ConflictError, PersistenceError

_BUSY_TIMEOUT_SECONDS = 15.0
_SCHEMA_VERSION = 1
_TERMINAL = "(" + ", ".join(f"'{s.value}'" for s in TERMINAL_STATES) + ")"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    anchor_asset_id TEXT NOT NULL,
    timeline_id     TEXT NOT NULL,
    state           TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    document        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_recording ON sessions (anchor_asset_id, timeline_id);
CREATE TABLE IF NOT EXISTS audit (
    session_id TEXT NOT NULL,
    sequence   INTEGER NOT NULL,
    at         TEXT NOT NULL,
    entry      TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence)
);
"""


class SqliteReviewStore:
    """Structurally implements ``ReviewStore``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False

    # --- writes ---------------------------------------------------------------------------------
    def create(self, session: ReviewSession, entries: Sequence[AuditEntry]) -> None:
        with self._write() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session.session_id,)
            ).fetchone()
            if exists:
                raise ConflictError(
                    f"Review session {session.session_id} already exists",
                    user_message="That review already exists.",
                )
            _insert(conn, session, 1)
            _append(conn, session.session_id, entries)

    def save(
        self,
        session: ReviewSession,
        entries: Sequence[AuditEntry],
        *,
        expected_revision: int,
    ) -> int:
        with self._write() as conn:
            row = conn.execute(
                "SELECT revision FROM sessions WHERE session_id = ?", (session.session_id,)
            ).fetchone()
            if row is None or row["revision"] != expected_revision:
                raise ConflictError(
                    f"Review session {session.session_id} was changed by another writer",
                    user_message="The review was changed by someone else; try again.",
                )
            revision = expected_revision + 1
            conn.execute(
                "UPDATE sessions SET state = ?, revision = ?, updated_at = ?, document = ? "
                "WHERE session_id = ?",
                (
                    session.state.value,
                    revision,
                    session.updated_at.isoformat(),
                    session_to_json(session),
                    session.session_id,
                ),
            )
            _append(conn, session.session_id, entries)
            return revision

    # --- reads ----------------------------------------------------------------------------------
    def get(self, session_id: str) -> StoredSession | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT revision, document FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return StoredSession(_load(row["document"], session_id), int(row["revision"]))

    def find(
        self, anchor_asset_id: str, *, timeline_id: str | None = None, active_only: bool = False
    ) -> list[ReviewSession]:
        sql = "SELECT session_id, document FROM sessions WHERE anchor_asset_id = ?"
        params: list[str] = [anchor_asset_id]
        if timeline_id is not None:
            sql += " AND timeline_id = ?"
            params.append(timeline_id)
        if active_only:
            sql += f" AND state NOT IN {_TERMINAL}"
        sql += " ORDER BY created_at DESC, session_id DESC"
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_load(r["document"], r["session_id"]) for r in rows]

    def entries(self, session_id: str, *, after: int = 0, limit: int = 500) -> list[AuditEntry]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT sequence, entry FROM audit WHERE session_id = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (session_id, after, limit),
            ).fetchall()
        try:
            return [_numbered(entry_from_json(r["entry"]), int(r["sequence"])) for r in rows]
        except InvalidReviewDocument as exc:
            raise PersistenceError(
                f"A stored history entry is unreadable: {exc}",
                details={"session_id": session_id},
            ) from exc

    # --- plumbing -------------------------------------------------------------------------------
    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(self._connect()) as conn:
                yield conn
        except sqlite3.Error as exc:
            raise PersistenceError(
                f"Reading the review store failed: {exc}", details={"database": str(self._path)}
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(
                f"Writing the review store failed: {exc}", details={"database": str(self._path)}
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._ready = True
        return conn


def _insert(conn: sqlite3.Connection, session: ReviewSession, revision: int) -> None:
    conn.execute(
        "INSERT INTO sessions (session_id, anchor_asset_id, timeline_id, state, revision, "
        "created_at, updated_at, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session.session_id,
            session.anchor_asset_id,
            session.timeline.timeline_id,
            session.state.value,
            revision,
            session.created_at.isoformat(),
            session.updated_at.isoformat(),
            session_to_json(session),
        ),
    )


def _append(conn: sqlite3.Connection, session_id: str, entries: Sequence[AuditEntry]) -> None:
    if not entries:
        return
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) AS last FROM audit WHERE session_id = ?", (session_id,)
    ).fetchone()
    for offset, entry in enumerate(entries, start=1):
        conn.execute(
            "INSERT INTO audit (session_id, sequence, at, entry) VALUES (?, ?, ?, ?)",
            (session_id, int(row["last"]) + offset, entry.at.isoformat(), entry_to_json(entry)),
        )


def _numbered(entry: AuditEntry, sequence: int) -> AuditEntry:
    return AuditEntry(
        kind=entry.kind,
        at=entry.at,
        actor=entry.actor,
        actor_kind=entry.actor_kind,
        summary=entry.summary,
        timeline_version=entry.timeline_version,
        subject=entry.subject,
        sequence=sequence,
        data=entry.data,
    )


def _load(text: str, session_id: str) -> ReviewSession:
    try:
        return session_from_json(text)
    except InvalidReviewDocument as exc:
        raise PersistenceError(
            f"The stored review {session_id} is unreadable: {exc}",
            details={"session_id": session_id},
        ) from exc
