"""SQLite adapter for :class:`WorkspaceRepository` (stdlib ``sqlite3``, no ORM).

A connection is opened per operation, which makes the adapter safe to use from
any thread (UI thread, job workers) without sharing connection objects.
Schema versioning uses ``PRAGMA user_version``; adopt Alembic when the schema
starts to evolve (see ADR-0007).
"""

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from media_house.modules.workspace.domain.repository import WorkspaceNameTaken
from media_house.modules.workspace.domain.values import WorkspaceId, WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.shared.errors import DomainError, PersistenceError

_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    name_key   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
"""


class SqliteWorkspaceRepository:
    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def add(self, workspace: Workspace) -> None:
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO workspaces (id, name, name_key, created_at) VALUES (?, ?, ?, ?)",
                    (
                        workspace.id.value,
                        workspace.name.value,
                        workspace.name.key,
                        workspace.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkspaceNameTaken(workspace.name) from exc
        except sqlite3.Error as exc:
            raise self._persistence_error("add workspace", exc) from exc

    def get(self, workspace_id: WorkspaceId) -> Workspace | None:
        row = self._fetch("SELECT * FROM workspaces WHERE id = ?", (workspace_id.value,))
        return self._to_entity(row[0]) if row else None

    def list_all(self) -> Sequence[Workspace]:
        rows = self._fetch("SELECT * FROM workspaces ORDER BY created_at, id", ())
        return [self._to_entity(row) for row in rows]

    def name_exists(self, name: WorkspaceName) -> bool:
        rows = self._fetch("SELECT 1 FROM workspaces WHERE name_key = ?", (name.key,))
        return bool(rows)

    # ------------------------------------------------------------------ #
    def _fetch(self, sql: str, params: tuple[str, ...]) -> list[sqlite3.Row]:
        try:
            with self._connection() as conn:
                return conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise self._persistence_error("query workspaces", exc) from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_schema()
        with closing(sqlite3.connect(self._path, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:  # commit on success, rollback on error
                yield conn

    def _ensure_schema(self) -> None:
        with self._schema_lock:
            if self._schema_ready:
                return
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self._path, timeout=5.0)) as conn, conn:
                    conn.executescript(_SCHEMA)
                    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            except (OSError, sqlite3.Error) as exc:
                raise self._persistence_error("initialise database", exc) from exc
            self._schema_ready = True

    def _to_entity(self, row: sqlite3.Row) -> Workspace:
        try:
            return Workspace(
                id=WorkspaceId(row["id"]),
                name=WorkspaceName(row["name"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        except (DomainError, ValueError) as exc:
            raise PersistenceError(
                "Stored workspace is corrupt",
                details={"workspace_id": row["id"]},
            ) from exc

    def _persistence_error(self, action: str, exc: Exception) -> PersistenceError:
        return PersistenceError(
            f"Failed to {action}: {exc}",
            details={"database": str(self._path)},
        )
