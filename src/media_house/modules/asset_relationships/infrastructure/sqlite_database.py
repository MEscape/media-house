"""Connection handling and migrations for ``asset_relationships.db``.

One short-lived connection per operation (thread-safe by construction, ADR-0007). Writes use
``BEGIN IMMEDIATE`` so concurrent writers queue on the busy timeout; WAL lets readers proceed during
writes. Any ``sqlite3.Error`` that escapes a unit of work becomes a ``PersistenceError`` (original
exception chained); domain errors raised *inside* the work roll the transaction back and propagate.

Two transient failures are retried for a few seconds because SQLite does not wait for them like a
write lock: creating the database from several processes at once (the switch to WAL) and the first
read after a process was killed (WAL recovery). A write is never retried once its transaction began.
"""

import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing, suppress
from pathlib import Path

from media_house.modules.asset_relationships.infrastructure.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
)
from media_house.shared.errors import PersistenceError

_BUSY_TIMEOUT_SECONDS = 15.0
_RETRY_SECONDS = 5.0
_RETRY_PAUSE_SECONDS = 0.05
_TRANSIENT = ("database is locked", "disk i/o error")


def _is_transient(exc: sqlite3.Error) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        text in str(exc).lower() for text in _TRANSIENT
    )


class SqliteRelationshipDatabase:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def read[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run read-only ``work`` on a fresh connection."""
        deadline = time.monotonic() + _RETRY_SECONDS
        while True:
            try:
                self._ensure_schema()
                with closing(self._connect()) as conn:
                    return work(conn)
            except sqlite3.Error as exc:
                if _is_transient(exc) and time.monotonic() < deadline:
                    time.sleep(_RETRY_PAUSE_SECONDS)
                    continue
                raise self._wrap("read", exc) from exc

    def write[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``work`` in one immediate transaction: commit on success, roll back on error."""
        try:
            self._ensure_schema()
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = work(conn)
                except BaseException:
                    with suppress(sqlite3.Error):
                        conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
                return result
        except sqlite3.Error as exc:
            raise self._wrap("write", exc) from exc

    # ------------------------------------------------------------------ internals
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._ready:
                return
            deadline = time.monotonic() + _RETRY_SECONDS
            while True:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(self._connect()) as conn:
                        conn.execute("PRAGMA journal_mode = WAL")
                        self._migrate(conn)
                    break
                except (OSError, sqlite3.Error) as exc:
                    transient = isinstance(exc, sqlite3.Error) and _is_transient(exc)
                    if transient and time.monotonic() < deadline:
                        time.sleep(_RETRY_PAUSE_SECONDS)
                        continue
                    raise self._wrap("initialise", exc) from exc
            self._ready = True

    def _migrate(self, conn: sqlite3.Connection) -> None:
        # IMMEDIATE + re-reading the version inside the transaction makes concurrent
        # processes starting at the same time apply each migration exactly once.
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current > LATEST_VERSION:
                raise PersistenceError(
                    "The relationships database was created by a newer version of Media-House",
                    details={"database": str(self._path), "version": current},
                )
            for migration in MIGRATIONS:
                if migration.version <= current:
                    continue
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(migration.version)}")
        except BaseException:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _wrap(self, action: str, exc: sqlite3.Error | OSError) -> PersistenceError:
        return PersistenceError(
            f"Relationships database {action} failed: {exc}",
            details={"database": str(self._path)},
        )
