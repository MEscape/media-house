"""Every StyleStore behaves the same: the in-memory fake and the SQLite adapter."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.style_memory.application.ports import StyleStore
from media_house.modules.style_memory.domain.seed import (
    manual_session_events,
    mesc_profile,
    mesc_rules,
)
from media_house.modules.style_memory.infrastructure.sqlite_store import SqliteStyleStore
from media_house.shared.errors import PersistenceError
from tests.support.fakes import T0
from tests.unit.style_memory.fakes import InMemoryStyleStore

pytestmark = pytest.mark.integration

Factory = Callable[[], StyleStore]


@pytest.fixture(params=["in_memory", "sqlite"])
def make_store(request: pytest.FixtureRequest, tmp_path: Path) -> Factory:
    if request.param == "in_memory":
        shared = InMemoryStyleStore()
        return lambda: shared
    return lambda: SqliteStyleStore(tmp_path / "db" / "style.db")


def test_profile_versions_roundtrip(make_store: Factory) -> None:
    store = make_store()
    assert store.load_profile() is None
    first = mesc_profile(T0)
    store.save_profile(first)
    store.save_profile(replace(first, version=2, greeting="Hi"))

    assert store.load_profile() == replace(first, version=2, greeting="Hi")
    assert store.load_profile_version(1) == first
    assert list(store.profile_versions()) == [1, 2]


def test_rules_roundtrip_update_and_delete(make_store: Factory) -> None:
    store = make_store()
    rules = mesc_rules(T0)
    for rule in rules:
        store.save_rule(rule)

    assert {r.rule_id for r in store.list_rules()} == {r.rule_id for r in rules}
    assert store.get_rule(rules[0].rule_id) == rules[0]
    store.save_rule(replace(rules[0], text="neu"))
    assert store.get_rule(rules[0].rule_id) is not None
    store.delete_rule(rules[0].rule_id)
    assert store.get_rule(rules[0].rule_id) is None


def test_events_are_append_only_and_idempotent(make_store: Factory) -> None:
    store = make_store()
    events = manual_session_events(T0)
    for event in (*events, *events):
        store.append_event(event)

    assert [e.event_id for e in store.list_events()] == [e.event_id for e in events]


def test_a_corrupt_row_is_a_persistence_error(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    path = tmp_path / "style.db"
    store = SqliteStyleStore(path)
    store.save_rule(mesc_rules(T0)[0])
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE rules SET document = '{\"text\": 1}'")
        conn.commit()

    with pytest.raises(PersistenceError):
        store.list_rules()
