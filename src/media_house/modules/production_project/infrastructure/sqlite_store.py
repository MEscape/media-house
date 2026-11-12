"""SQLite implementation of ``ProjectStore``: one strict-JSON document per project and an
append-only audit log. One short-lived connection per operation; a write is one
``BEGIN IMMEDIATE`` transaction that checks the revision the caller read (ADR-0007)."""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from media_house.core.domain import json_codec
from media_house.modules.production_project.application.ports import AuditEntry, ProjectSummary
from media_house.modules.production_project.domain.project import Project
from media_house.shared.errors import ConflictError, PersistenceError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY, title TEXT NOT NULL, client TEXT NOT NULL,
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    project_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL, PRIMARY KEY (project_id, seq));
"""
_DOCUMENT = "production_project"


class SqliteProjectStore:
    """Structurally implements ``ProjectStore``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False

    def create(self, project: Project) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project.project_id,)
            ).fetchone():
                raise ConflictError(
                    f"Project {project.project_id} exists",
                    user_message="Das Projekt gibt es schon.",
                )
            self._insert(conn, project)
            conn.commit()

    def get(self, project_id: str) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT document FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        return None if row is None else self._read(row[0])

    def save(self, project: Project, *, expected_revision: int) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision FROM projects WHERE project_id = ?", (project.project_id,)
            ).fetchone()
            if row is None or int(row[0]) != expected_revision:
                raise ConflictError(
                    f"Project {project.project_id} changed (expected revision {expected_revision})",
                    user_message="Das Projekt wurde gleichzeitig geändert.",
                )
            revision = expected_revision + 1
            self._insert(conn, _with_revision(project, revision), replace=True)
            conn.commit()
        return revision

    def list(self) -> Sequence[ProjectSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, title, client, updated_at, revision FROM projects "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [
            ProjectSummary(r[0], r[1], r[2], datetime.fromisoformat(r[3]), int(r[4])) for r in rows
        ]

    def delete(self, project_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM audit WHERE project_id = ?", (project_id,))
            conn.commit()

    def append_audit(self, project_id: str, entry: AuditEntry) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM audit WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO audit (project_id, seq, at, action, detail) VALUES (?, ?, ?, ?, ?)",
                (project_id, seq, entry.at.isoformat(), entry.action, entry.detail),
            )
            conn.commit()

    def audit(self, project_id: str) -> Sequence[AuditEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT at, action, detail FROM audit WHERE project_id = ? ORDER BY seq",
                (project_id,),
            ).fetchall()
        return [AuditEntry(datetime.fromisoformat(r[0]), r[1], r[2]) for r in rows]

    # --- plumbing -------------------------------------------------------------------------------
    @staticmethod
    def _insert(conn: sqlite3.Connection, project: Project, *, replace: bool = False) -> None:
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        conn.execute(
            f"{verb} INTO projects (project_id, title, client, revision, updated_at, document) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                project.project_id,
                project.settings.title,
                project.settings.client,
                project.revision,
                project.updated_at.isoformat(),
                json_codec.dumps(_DOCUMENT, project),
            ),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._path, timeout=15.0, isolation_level=None)) as conn:
                if not self._ready:
                    conn.executescript(_SCHEMA)
                    self._ready = True
                yield conn
        except sqlite3.Error as exc:
            raise PersistenceError(
                f"Project store failed: {exc}", details={"database": str(self._path)}
            ) from exc

    def _read(self, text: str) -> Project:
        try:
            raw = json.loads(text)
            raw.pop("document_type", None)
            project: Project = json_codec.decode(Project, raw)
        except (ValueError, TypeError, AttributeError) as exc:
            raise PersistenceError(
                f"Corrupt project document: {exc}", details={"database": str(self._path)}
            ) from exc
        return project


def _with_revision(project: Project, revision: int) -> Project:
    from dataclasses import replace

    return replace(project, revision=revision)
