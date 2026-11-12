"""SQLite implementation of ``StyleStore``: documents as strict JSON, one short-lived connection
per operation (ADR-0007). A corrupt row is a ``PersistenceError``, never a silent default."""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from media_house.core.domain import json_codec
from media_house.modules.style_memory.domain.model import (
    Exemplar,
    PreferenceEvent,
    ReferenceCard,
    StyleProfile,
    StyleRule,
)
from media_house.shared.errors import PersistenceError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (version INTEGER PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT PRIMARY KEY, status TEXT NOT NULL, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exemplars (exemplar_id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY, document TEXT NOT NULL);
"""


class SqliteStyleStore:
    """Structurally implements ``StyleStore``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False

    # --- profile --------------------------------------------------------------------------------
    def load_profile(self) -> StyleProfile | None:
        row = self._one("SELECT document FROM profile ORDER BY version DESC LIMIT 1")
        return None if row is None else self._read(StyleProfile, row[0])

    def save_profile(self, profile: StyleProfile) -> None:
        self._exec(
            "INSERT OR REPLACE INTO profile (version, document) VALUES (?, ?)",
            (profile.version, json_codec.dumps("style_profile", profile)),
        )

    def profile_versions(self) -> Sequence[int]:
        return [int(r[0]) for r in self._all("SELECT version FROM profile ORDER BY version")]

    def load_profile_version(self, version: int) -> StyleProfile | None:
        row = self._one("SELECT document FROM profile WHERE version = ?", (version,))
        return None if row is None else self._read(StyleProfile, row[0])

    # --- rules ----------------------------------------------------------------------------------
    def list_rules(self) -> Sequence[StyleRule]:
        return [
            self._read(StyleRule, r[0])
            for r in self._all("SELECT document FROM rules ORDER BY rule_id")
        ]

    def get_rule(self, rule_id: str) -> StyleRule | None:
        row = self._one("SELECT document FROM rules WHERE rule_id = ?", (rule_id,))
        return None if row is None else self._read(StyleRule, row[0])

    def save_rule(self, rule: StyleRule) -> None:
        self._exec(
            "INSERT OR REPLACE INTO rules (rule_id, status, document) VALUES (?, ?, ?)",
            (rule.rule_id, rule.status.value, json_codec.dumps("style_rule", rule)),
        )

    def delete_rule(self, rule_id: str) -> None:
        self._exec("DELETE FROM rules WHERE rule_id = ?", (rule_id,))

    # --- events ---------------------------------------------------------------------------------
    def append_event(self, event: PreferenceEvent) -> None:
        self._exec(
            "INSERT OR IGNORE INTO events (event_id, document) VALUES (?, ?)",
            (event.event_id, json_codec.dumps("preference_event", event)),
        )

    def list_events(self) -> Sequence[PreferenceEvent]:
        return [
            self._read(PreferenceEvent, r[0])
            for r in self._all("SELECT document FROM events ORDER BY seq")
        ]

    # --- exemplars and cards --------------------------------------------------------------------
    def list_exemplars(self) -> Sequence[Exemplar]:
        return [
            self._read(Exemplar, r[0])
            for r in self._all("SELECT document FROM exemplars ORDER BY exemplar_id")
        ]

    def save_exemplar(self, exemplar: Exemplar) -> None:
        self._exec(
            "INSERT OR REPLACE INTO exemplars (exemplar_id, document) VALUES (?, ?)",
            (exemplar.exemplar_id, json_codec.dumps("exemplar", exemplar)),
        )

    def delete_exemplar(self, exemplar_id: str) -> None:
        self._exec("DELETE FROM exemplars WHERE exemplar_id = ?", (exemplar_id,))

    def list_cards(self) -> Sequence[ReferenceCard]:
        return [
            self._read(ReferenceCard, r[0])
            for r in self._all("SELECT document FROM cards ORDER BY card_id")
        ]

    def save_card(self, card: ReferenceCard) -> None:
        self._exec(
            "INSERT OR REPLACE INTO cards (card_id, document) VALUES (?, ?)",
            (card.card_id, json_codec.dumps("reference_card", card)),
        )

    # --- plumbing -------------------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._path, timeout=15.0)) as conn:
                if not self._ready:
                    conn.executescript(_SCHEMA)
                    self._ready = True
                yield conn
        except sqlite3.Error as exc:
            raise PersistenceError(
                f"Style store failed: {exc}", details={"database": str(self._path)}
            ) from exc

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._connect() as conn:
            return [tuple(r) for r in conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def _read[T](self, tp: type[T], text: str) -> T:
        try:
            raw = json.loads(text)
            raw.pop("document_type", None)
            return json_codec.decode(tp, raw)  # type: ignore[no-any-return]
        except (ValueError, TypeError, AttributeError) as exc:
            raise PersistenceError(
                f"Corrupt {tp.__name__} in the style store: {exc}",
                details={"database": str(self._path)},
            ) from exc
