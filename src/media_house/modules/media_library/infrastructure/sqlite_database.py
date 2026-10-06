"""Connection handling and migrations for the media library SQLite database.

One short-lived connection per operation (thread-safe by construction, ADR-0007). Writes use
``BEGIN IMMEDIATE`` so concurrent writers queue on the busy timeout instead of failing with a
lock-upgrade error; WAL mode lets readers proceed during writes. Any ``sqlite3.Error`` that
escapes a unit of work becomes a :class:`PersistenceError` (original exception chained);
domain errors raised *inside* the work roll the transaction back and propagate unchanged.
"""

import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing, suppress
from pathlib import Path

from media_house.modules.media_library.infrastructure.migrations import LATEST_VERSION, MIGRATIONS
from media_house.shared.errors import PersistenceError

_BUSY_TIMEOUT_SECONDS = 15.0


class SqliteMediaDatabase:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def read[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run read-only ``work`` on a fresh connection."""
        try:
            self._ensure_schema()
            with closing(self._connect()) as conn:
                return work(conn)
        except sqlite3.Error as exc:
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
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with closing(self._connect()) as conn:
                    conn.execute("PRAGMA journal_mode = WAL")
                    self._migrate(conn)
            except (OSError, sqlite3.Error) as exc:
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
                    "The media database was created by a newer version of Media-House",
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
            f"Media database {action} failed: {exc}",
            details={"database": str(self._path)},
        )
