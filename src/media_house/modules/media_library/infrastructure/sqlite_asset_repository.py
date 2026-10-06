"""SQLite adapter for :class:`MediaAssetRepository` (stdlib ``sqlite3``, no ORM)."""

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from media_house.modules.media_library.domain.errors import (
    DuplicateDerivative,
    DuplicateMedia,
    MediaNotFound,
)
from media_house.modules.media_library.domain.media_asset import MediaAsset, MediaFileInfo
from media_house.modules.media_library.domain.query import (
    MediaQuery,
    Page,
    SortField,
    SortOrder,
    TagUsage,
)
from media_house.modules.media_library.domain.values import (
    AssetRole,
    AssetStatus,
    Checksum,
    Derivation,
    MediaAssetId,
    MediaName,
    MediaSource,
    MediaType,
    ProcessingFingerprint,
    SourceType,
    StorageKey,
    Tag,
    canonical_json,
)
from media_house.modules.media_library.infrastructure.sqlite_database import SqliteMediaDatabase
from media_house.shared.errors import DomainError, PersistenceError

# Whitelist: callers choose a SortField, never an SQL fragment.
_SORT_COLUMNS: dict[SortField, str] = {
    SortField.CREATED_AT: "a.created_at",
    SortField.UPDATED_AT: "a.updated_at",
    SortField.NAME: "a.name_key",
    SortField.FILE_SIZE: "a.file_size",
    SortField.DURATION: "a.duration_seconds",
}
_CHUNK = 500  # stays far below SQLite's bound-variable limit


def format_timestamp(value: datetime) -> str:
    """UTC, fixed-width microseconds: lexicographic order == chronological order."""
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SqliteMediaAssetRepository:
    """Structurally implements ``MediaAssetRepository``."""

    def __init__(self, database: SqliteMediaDatabase) -> None:
        self._db = database

    # ------------------------------------------------------------------ writes
    def add(self, asset: MediaAsset) -> None:
        def work(conn: sqlite3.Connection) -> None:
            try:
                conn.execute(_INSERT, _insert_params(asset))
            except sqlite3.IntegrityError as exc:
                text = str(exc)
                if "media_assets.checksum" in text or "ux_media_assets_original_checksum" in text:
                    raise DuplicateMedia(asset.file.checksum.value) from exc
                if asset.derivation and (
                    "media_assets.processing_fingerprint" in text
                    or "ux_media_assets_fingerprint" in text
                ):
                    raise DuplicateDerivative(asset.derivation.fingerprint.value) from exc
                raise
            _write_tags(conn, asset)

        self._db.write(work)

    def save(self, asset: MediaAsset) -> None:
        def work(conn: sqlite3.Connection) -> None:
            cursor = conn.execute(
                "UPDATE media_assets SET display_name = ?, name_key = ?, metadata = ?, "
                "status = ?, is_favorite = ?, updated_at = ? WHERE id = ?",
                (
                    asset.display_name.value,
                    asset.display_name.key,
                    canonical_json(asset.metadata),
                    asset.status.value,
                    int(asset.is_favorite),
                    format_timestamp(asset.updated_at),
                    asset.id.value,
                ),
            )
            if cursor.rowcount == 0:
                raise MediaNotFound(asset.id.value)
            _write_tags(conn, asset)

        self._db.write(work)

    def delete(self, asset_ids: Sequence[MediaAssetId]) -> None:
        ids = [a.value for a in asset_ids]

        def work(conn: sqlite3.Connection) -> None:
            for start in range(0, len(ids), _CHUNK):
                chunk = ids[start : start + _CHUNK]
                # Tags and memberships go with the row (ON DELETE CASCADE).
                conn.execute(
                    f"DELETE FROM media_assets WHERE id IN ({_placeholders(len(chunk))})",  # noqa: S608
                    chunk,
                )

        self._db.write(work)

    # ------------------------------------------------------------------ reads
    def get(self, asset_id: MediaAssetId) -> MediaAsset | None:
        found = self.get_many([asset_id])
        return found[0] if found else None

    def get_many(self, asset_ids: Sequence[MediaAssetId]) -> Sequence[MediaAsset]:
        ids = list(dict.fromkeys(a.value for a in asset_ids))
        if not ids:
            return []

        def work(conn: sqlite3.Connection) -> list[MediaAsset]:
            rows: list[sqlite3.Row] = []
            for start in range(0, len(ids), _CHUNK):
                chunk = ids[start : start + _CHUNK]
                rows += conn.execute(
                    f"SELECT * FROM media_assets WHERE id IN ({_placeholders(len(chunk))})",  # noqa: S608
                    chunk,
                ).fetchall()
            by_id = {a.id.value: a for a in _hydrate_rows(conn, rows)}
            return [by_id[i] for i in ids if i in by_id]

        return self._db.read(work)

    def find_original_by_checksum(self, checksum: Checksum) -> MediaAsset | None:
        def work(conn: sqlite3.Connection) -> MediaAsset | None:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE checksum = ? AND source_asset_id IS NULL",
                (checksum.value,),
            ).fetchone()
            return _hydrate_rows(conn, [row])[0] if row else None

        return self._db.read(work)

    def find_derived(
        self,
        source_asset_id: MediaAssetId,
        fingerprint: ProcessingFingerprint,
    ) -> MediaAsset | None:
        def work(conn: sqlite3.Connection) -> MediaAsset | None:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE processing_fingerprint = ? "
                "AND source_asset_id = ?",
                (fingerprint.value, source_asset_id.value),
            ).fetchone()
            return _hydrate_rows(conn, [row])[0] if row else None

        return self._db.read(work)

    def search(self, query: MediaQuery) -> Page[MediaAsset]:
        where, params = _build_where(query)
        column = _SORT_COLUMNS[query.sort_by]
        direction = "ASC" if query.sort_order is SortOrder.ASC else "DESC"

        def work(conn: sqlite3.Connection) -> Page[MediaAsset]:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM media_assets a {where}",  # noqa: S608
                    params,
                ).fetchone()[0],
            )
            rows = conn.execute(
                f"SELECT a.* FROM media_assets a {where} "  # noqa: S608
                f"ORDER BY {column} {direction}, a.id LIMIT ? OFFSET ?",
                [*params, query.page_size, query.offset],
            ).fetchall()
            return Page(_hydrate_rows(conn, rows), total, query.page, query.page_size)

        return self._db.read(work)

    def descendant_ids(self, asset_id: MediaAssetId) -> Sequence[MediaAssetId]:
        def work(conn: sqlite3.Connection) -> list[MediaAssetId]:
            rows = conn.execute(
                "WITH RECURSIVE tree(id) AS ("
                " SELECT id FROM media_assets WHERE source_asset_id = ?"
                " UNION SELECT a.id FROM media_assets a JOIN tree t ON a.source_asset_id = t.id"
                ") SELECT id FROM tree",
                (asset_id.value,),
            ).fetchall()
            return [MediaAssetId(r["id"]) for r in rows]

        return self._db.read(work)

    def count_storage_references(self, key: StorageKey) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE storage_key = ?",
                (key.value,),
            ).fetchone()
            return int(row[0])

        return self._db.read(work)

    def list_tags(self, *, prefix: str | None, limit: int) -> Sequence[TagUsage]:
        def work(conn: sqlite3.Connection) -> list[TagUsage]:
            clause, params = "", []
            if prefix:
                clause = "WHERE tag LIKE ? ESCAPE '\\'"
                params.append(_like_pattern(prefix)[1:])  # prefix match: drop leading '%'
            rows = conn.execute(
                f"SELECT tag, COUNT(*) AS n FROM media_asset_tags {clause} "  # noqa: S608
                "GROUP BY tag ORDER BY n DESC, tag LIMIT ?",
                [*params, limit],
            ).fetchall()
            return [TagUsage(r["tag"], int(r["n"])) for r in rows]

        return self._db.read(work)


# ---------------------------------------------------------------------- SQL builders
_INSERT = (
    "INSERT INTO media_assets (id, media_type, mime_type, extension, original_filename, "
    "display_name, name_key, filename_key, storage_key, file_size, checksum, width, height, "
    "duration_seconds, technical, metadata, role, status, is_favorite, source_type, source_url, "
    "source_provider, source_asset_id, operation, operation_version, processing_config, "
    "processing_fingerprint, created_at, updated_at) "
    "VALUES (" + _placeholders(29) + ")"
)


def _insert_params(asset: MediaAsset) -> tuple[object, ...]:
    file, derivation = asset.file, asset.derivation
    return (
        asset.id.value,
        file.media_type.value,
        file.mime_type,
        file.extension,
        asset.original_filename,
        asset.display_name.value,
        asset.display_name.key,
        asset.original_filename.casefold(),
        file.storage_key.value,
        file.file_size,
        file.checksum.value,
        file.width,
        file.height,
        file.duration_seconds,
        canonical_json(file.technical),
        canonical_json(asset.metadata),
        asset.role.value,
        asset.status.value,
        int(asset.is_favorite),
        asset.source.source_type.value,
        asset.source.url,
        asset.source.provider,
        derivation.source_asset_id.value if derivation else None,
        derivation.operation if derivation else None,
        derivation.version if derivation else None,
        canonical_json(derivation.config) if derivation else None,
        derivation.fingerprint.value if derivation else None,
        format_timestamp(asset.created_at),
        format_timestamp(asset.updated_at),
    )


def _write_tags(conn: sqlite3.Connection, asset: MediaAsset) -> None:
    names = sorted(t.value for t in asset.tags)
    conn.execute("DELETE FROM media_asset_tags WHERE asset_id = ?", (asset.id.value,))
    conn.executemany("INSERT OR IGNORE INTO media_tags (name) VALUES (?)", [(n,) for n in names])
    conn.executemany(
        "INSERT INTO media_asset_tags (asset_id, tag) VALUES (?, ?)",
        [(asset.id.value, n) for n in names],
    )


def _build_where(query: MediaQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if query.text:
        like = _like_pattern(query.text)
        clauses.append("(a.name_key LIKE ? ESCAPE '\\' OR a.filename_key LIKE ? ESCAPE '\\')")
        params += [like, like]
    if query.media_types:
        clauses.append(f"a.media_type IN ({_placeholders(len(query.media_types))})")
        params += sorted(t.value for t in query.media_types)
    if query.group_ids:
        marks = _placeholders(len(query.group_ids))
        if query.include_subgroups:
            clauses.append(
                "a.id IN (SELECT m.asset_id FROM media_group_members m WHERE m.group_id IN ("
                "WITH RECURSIVE tree(id) AS ("
                f" SELECT id FROM media_groups WHERE id IN ({marks})"
                " UNION SELECT g.id FROM media_groups g JOIN tree t ON g.parent_id = t.id"
                ") SELECT id FROM tree))",
            )
        else:
            clauses.append(
                f"a.id IN (SELECT m.asset_id FROM media_group_members m WHERE m.group_id IN ({marks}))",
            )
        params += sorted(query.group_ids)
    if query.tags:
        clauses.append(
            "(SELECT COUNT(*) FROM media_asset_tags t WHERE t.asset_id = a.id "
            f"AND t.tag IN ({_placeholders(len(query.tags))})) = ?",
        )
        params += [*sorted(query.tags), len(query.tags)]
    if query.created_from is not None:
        clauses.append("a.created_at >= ?")
        params.append(format_timestamp(query.created_from))
    if query.created_to is not None:
        clauses.append("a.created_at < ?")
        params.append(format_timestamp(query.created_to))
    if query.favorite is not None:
        clauses.append("a.is_favorite = ?")
        params.append(int(query.favorite))
    if query.statuses:
        clauses.append(f"a.status IN ({_placeholders(len(query.statuses))})")
        params += sorted(s.value for s in query.statuses)
    if query.roles:
        clauses.append(f"a.role IN ({_placeholders(len(query.roles))})")
        params += sorted(r.value for r in query.roles)
    if query.source_types:
        clauses.append(f"a.source_type IN ({_placeholders(len(query.source_types))})")
        params += sorted(s.value for s in query.source_types)
    if query.source_asset_id is not None:
        clauses.append("a.source_asset_id = ?")
        params.append(query.source_asset_id)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


# ---------------------------------------------------------------------- hydration
def _hydrate_rows(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[MediaAsset]:
    """Rows -> aggregates, loading all tags with ONE extra query (no N+1)."""
    if not rows:
        return []
    tags: dict[str, list[str]] = {}
    ids = [r["id"] for r in rows]
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        for tag_row in conn.execute(
            "SELECT asset_id, tag FROM media_asset_tags "
            f"WHERE asset_id IN ({_placeholders(len(chunk))})",
            chunk,
        ):
            tags.setdefault(tag_row["asset_id"], []).append(tag_row["tag"])
    return [_to_entity(row, tags.get(row["id"], [])) for row in rows]


def _to_entity(row: sqlite3.Row, tag_names: list[str]) -> MediaAsset:
    try:
        derivation = (
            Derivation(
                source_asset_id=MediaAssetId(row["source_asset_id"]),
                operation=row["operation"],
                version=row["operation_version"],
                config=json.loads(row["processing_config"]),
                fingerprint=ProcessingFingerprint(row["processing_fingerprint"]),
            )
            if row["source_asset_id"] is not None
            else None
        )
        return MediaAsset(
            id=MediaAssetId(row["id"]),
            file=MediaFileInfo(
                media_type=MediaType(row["media_type"]),
                mime_type=row["mime_type"],
                extension=row["extension"],
                file_size=row["file_size"],
                checksum=Checksum(row["checksum"]),
                storage_key=StorageKey(row["storage_key"]),
                width=row["width"],
                height=row["height"],
                duration_seconds=row["duration_seconds"],
                technical=json.loads(row["technical"]),
            ),
            original_filename=row["original_filename"],
            display_name=MediaName(row["display_name"]),
            source=MediaSource(
                SourceType(row["source_type"]),
                row["source_url"],
                row["source_provider"],
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            role=AssetRole(row["role"]),
            status=AssetStatus(row["status"]),
            is_favorite=bool(row["is_favorite"]),
            tags=[Tag(name) for name in tag_names],
            metadata=json.loads(row["metadata"]),
            derivation=derivation,
        )
    except (DomainError, ValueError, TypeError, KeyError) as exc:
        raise PersistenceError(
            "Stored media asset is corrupt",
            details={"asset_id": row["id"]},
        ) from exc
