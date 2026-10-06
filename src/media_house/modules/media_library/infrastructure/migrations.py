"""Schema migrations for the media library database.

Ordered, append-only, applied transactionally and versioned through ``PRAGMA user_version``
(the project's persistence convention, ADR-0007). To change the schema, *append* a new
:class:`Migration` with the next version number; never edit a released one.
Each migration is a tuple of single statements so no SQL splitting is ever needed.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


_V1_INITIAL = Migration(
    version=1,
    name="initial media library schema",
    statements=(
        """
        CREATE TABLE media_assets (
            id                      TEXT PRIMARY KEY,
            media_type              TEXT NOT NULL,
            mime_type               TEXT NOT NULL,
            extension               TEXT NOT NULL,
            original_filename       TEXT NOT NULL,
            display_name            TEXT NOT NULL,
            name_key                TEXT NOT NULL,
            filename_key            TEXT NOT NULL,
            storage_key             TEXT NOT NULL,
            file_size               INTEGER NOT NULL CHECK (file_size >= 0),
            checksum                TEXT NOT NULL,
            width                   INTEGER,
            height                  INTEGER,
            duration_seconds        REAL,
            technical               TEXT NOT NULL DEFAULT '{}',
            metadata                TEXT NOT NULL DEFAULT '{}',
            role                    TEXT NOT NULL,
            status                  TEXT NOT NULL,
            is_favorite             INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
            source_type             TEXT NOT NULL,
            source_url              TEXT,
            source_provider         TEXT,
            source_asset_id         TEXT REFERENCES media_assets (id) ON DELETE CASCADE,
            operation               TEXT,
            operation_version       INTEGER,
            processing_config       TEXT,
            processing_fingerprint  TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            CHECK ((source_asset_id IS NULL) = (processing_fingerprint IS NULL)),
            CHECK ((role = 'original') = (source_asset_id IS NULL))
        )
        """,
        # Invariants enforced by the database (race-safe deduplication):
        "CREATE UNIQUE INDEX ux_media_assets_original_checksum "
        "ON media_assets (checksum) WHERE source_asset_id IS NULL",
        "CREATE UNIQUE INDEX ux_media_assets_fingerprint "
        "ON media_assets (processing_fingerprint) WHERE processing_fingerprint IS NOT NULL",
        # Query patterns: blob reference counting, derivative listing, browsing, favourites.
        "CREATE INDEX ix_media_assets_storage_key ON media_assets (storage_key)",
        "CREATE INDEX ix_media_assets_source ON media_assets (source_asset_id) "
        "WHERE source_asset_id IS NOT NULL",
        "CREATE INDEX ix_media_assets_browse ON media_assets (status, role, created_at DESC)",
        "CREATE INDEX ix_media_assets_type ON media_assets (media_type, created_at DESC)",
        "CREATE INDEX ix_media_assets_updated ON media_assets (updated_at)",
        "CREATE INDEX ix_media_assets_name ON media_assets (name_key)",
        "CREATE INDEX ix_media_assets_favorites ON media_assets (created_at DESC) "
        "WHERE is_favorite = 1",
        """
        CREATE TABLE media_groups (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            name_key    TEXT NOT NULL,
            parent_id   TEXT REFERENCES media_groups (id) ON DELETE RESTRICT,
            description TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX ux_media_groups_sibling_name "
        "ON media_groups (COALESCE(parent_id, ''), name_key)",
        "CREATE INDEX ix_media_groups_parent ON media_groups (parent_id)",
        """
        CREATE TABLE media_group_members (
            group_id TEXT NOT NULL REFERENCES media_groups (id) ON DELETE CASCADE,
            asset_id TEXT NOT NULL REFERENCES media_assets (id) ON DELETE CASCADE,
            added_at TEXT NOT NULL,
            PRIMARY KEY (group_id, asset_id)
        ) WITHOUT ROWID
        """,
        "CREATE INDEX ix_media_group_members_asset ON media_group_members (asset_id)",
        "CREATE TABLE media_tags (name TEXT PRIMARY KEY) WITHOUT ROWID",
        """
        CREATE TABLE media_asset_tags (
            asset_id TEXT NOT NULL REFERENCES media_assets (id) ON DELETE CASCADE,
            tag      TEXT NOT NULL REFERENCES media_tags (name) ON DELETE CASCADE,
            PRIMARY KEY (asset_id, tag)
        ) WITHOUT ROWID
        """,
        "CREATE INDEX ix_media_asset_tags_tag ON media_asset_tags (tag, asset_id)",
    ),
)

MIGRATIONS: tuple[Migration, ...] = (_V1_INITIAL,)
LATEST_VERSION = MIGRATIONS[-1].version
