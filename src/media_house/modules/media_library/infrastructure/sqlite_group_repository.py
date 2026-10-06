"""SQLite adapter for :class:`MediaGroupRepository`."""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime

from media_house.modules.media_library.domain.errors import GroupNameTaken, GroupNotFound
from media_house.modules.media_library.domain.media_group import MediaGroup
from media_house.modules.media_library.domain.values import GroupName, MediaAssetId, MediaGroupId
from media_house.modules.media_library.infrastructure.sqlite_asset_repository import (
    format_timestamp,
)
from media_house.modules.media_library.infrastructure.sqlite_database import SqliteMediaDatabase
from media_house.shared.errors import DomainError, PersistenceError

_CHUNK = 500


class SqliteMediaGroupRepository:
    """Structurally implements ``MediaGroupRepository``."""

    def __init__(self, database: SqliteMediaDatabase) -> None:
        self._db = database

    def add(self, group: MediaGroup) -> None:
        def work(conn: sqlite3.Connection) -> None:
            try:
                conn.execute(
                    "INSERT INTO media_groups (id, name, name_key, parent_id, description, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        group.id.value,
                        group.name.value,
                        group.name.key,
                        group.parent_id.value if group.parent_id else None,
                        group.description,
                        format_timestamp(group.created_at),
                        format_timestamp(group.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc):
                    raise GroupNameTaken(group.name.value) from exc
                raise

        self._db.write(work)

    def save(self, group: MediaGroup) -> None:
        def work(conn: sqlite3.Connection) -> None:
            try:
                cursor = conn.execute(
                    "UPDATE media_groups SET name = ?, name_key = ?, description = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        group.name.value,
                        group.name.key,
                        group.description,
                        format_timestamp(group.updated_at),
                        group.id.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc):
                    raise GroupNameTaken(group.name.value) from exc
                raise
            if cursor.rowcount == 0:
                raise GroupNotFound(group.id.value)

        self._db.write(work)

    def get(self, group_id: MediaGroupId) -> MediaGroup | None:
        def work(conn: sqlite3.Connection) -> MediaGroup | None:
            row = conn.execute(
                "SELECT * FROM media_groups WHERE id = ?",
                (group_id.value,),
            ).fetchone()
            return _to_entity(row) if row else None

        return self._db.read(work)

    def list_all(self) -> Sequence[MediaGroup]:
        def work(conn: sqlite3.Connection) -> list[MediaGroup]:
            rows = conn.execute("SELECT * FROM media_groups ORDER BY name_key, id").fetchall()
            return [_to_entity(r) for r in rows]

        return self._db.read(work)

    def has_children(self, group_id: MediaGroupId) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            row = conn.execute(
                "SELECT 1 FROM media_groups WHERE parent_id = ? LIMIT 1",
                (group_id.value,),
            ).fetchone()
            return row is not None

        return self._db.read(work)

    def delete(self, group_id: MediaGroupId) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM media_groups WHERE id = ?", (group_id.value,))

        self._db.write(work)

    def add_member(self, group_id: MediaGroupId, asset_id: MediaAssetId, *, now: datetime) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO media_group_members (group_id, asset_id, added_at) "
                "VALUES (?, ?, ?)",
                (group_id.value, asset_id.value, format_timestamp(now)),
            )
            return cursor.rowcount > 0

        return self._db.write(work)

    def remove_member(self, group_id: MediaGroupId, asset_id: MediaAssetId) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM media_group_members WHERE group_id = ? AND asset_id = ?",
                (group_id.value, asset_id.value),
            )
            return cursor.rowcount > 0

        return self._db.write(work)

    def group_ids_for_assets(self, asset_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        ids = list(dict.fromkeys(asset_ids))

        def work(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
            found: dict[str, list[str]] = {}
            for start in range(0, len(ids), _CHUNK):
                chunk = ids[start : start + _CHUNK]
                rows = conn.execute(
                    "SELECT asset_id, group_id FROM media_group_members "  # noqa: S608
                    f"WHERE asset_id IN ({','.join('?' * len(chunk))}) ORDER BY group_id",
                    chunk,
                ).fetchall()
                for row in rows:
                    found.setdefault(row["asset_id"], []).append(row["group_id"])
            return {k: tuple(v) for k, v in found.items()}

        return self._db.read(work) if ids else {}

    def member_counts(self) -> Mapping[str, int]:
        def work(conn: sqlite3.Connection) -> dict[str, int]:
            rows = conn.execute(
                "SELECT group_id, COUNT(*) AS n FROM media_group_members GROUP BY group_id",
            ).fetchall()
            return {r["group_id"]: int(r["n"]) for r in rows}

        return self._db.read(work)


def _to_entity(row: sqlite3.Row) -> MediaGroup:
    try:
        return MediaGroup(
            id=MediaGroupId(row["id"]),
            name=GroupName(row["name"]),
            parent_id=MediaGroupId(row["parent_id"]) if row["parent_id"] else None,
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    except (DomainError, ValueError, KeyError) as exc:
        raise PersistenceError(
            "Stored media group is corrupt",
            details={"group_id": row["id"]},
        ) from exc
