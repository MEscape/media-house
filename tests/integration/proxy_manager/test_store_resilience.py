"""Transient SQLite failures (another process switching modes, recovery after a killed process) are
retried when that is safe, and never when repeating could do something twice."""

import sqlite3
from pathlib import Path

import pytest

from media_house.modules.proxy_manager.infrastructure import sqlite_store
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.shared.errors import PersistenceError
from tests.support.proxy_store_helpers import begin

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path: Path) -> SqliteProxyStore:
    return SqliteProxyStore(tmp_path / "proxy.db", owner_token="me")


class Flaky:
    """Makes the first ``failures`` connection attempts fail like a database in recovery."""

    def __init__(self, store: SqliteProxyStore, failures: int, message: str) -> None:
        self.calls = 0
        self._failures = failures
        self._message = message
        self._real = store._connect

    def __call__(self) -> sqlite3.Connection:
        self.calls += 1
        if self.calls <= self._failures:
            raise sqlite3.OperationalError(self._message)
        return self._real()


def test_a_read_that_hits_a_transient_io_error_is_tried_again(
    store: SqliteProxyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.get_slot("s", "edit")  # create the database
    flaky = Flaky(store, 3, "disk I/O error")
    monkeypatch.setattr(store, "_connect", flaky)

    assert store.get_slot("s", "edit") is None
    assert flaky.calls == 4


def test_a_read_that_never_recovers_ends_as_a_typed_error(
    store: SqliteProxyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.get_slot("s", "edit")
    monkeypatch.setattr(sqlite_store, "_RETRY_WINDOW_SECONDS", 0.2)
    monkeypatch.setattr(store, "_connect", Flaky(store, 10**6, "disk I/O error"))

    with pytest.raises(PersistenceError, match="read failed"):
        store.get_slot("s", "edit")


def test_an_error_that_is_not_transient_is_never_retried(
    store: SqliteProxyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.get_slot("s", "edit")
    flaky = Flaky(store, 10**6, "no such table: attempts")
    monkeypatch.setattr(store, "_connect", flaky)

    with pytest.raises(PersistenceError):
        store.get_slot("s", "edit")
    assert flaky.calls == 1


def test_a_write_that_cannot_even_begin_is_tried_again(
    store: SqliteProxyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.get_slot("s", "edit")
    flaky = Flaky(store, 2, "database is locked")
    monkeypatch.setattr(store, "_connect", flaky)

    started = begin(store, 1)

    assert started.seq > 0 and flaky.calls == 3
    monkeypatch.undo()
    assert store.get_attempt("a1") is not None  # recorded exactly once


def test_a_write_is_never_repeated_once_its_work_has_run(
    store: SqliteProxyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.get_slot("s", "edit")
    runs: list[int] = []

    def work(conn: sqlite3.Connection) -> None:
        runs.append(1)
        raise sqlite3.OperationalError("disk I/O error")  # could have happened at any point

    with pytest.raises(PersistenceError, match="write failed"):
        store._write(work)

    assert runs == [1]  # the transaction was rolled back and NOT run a second time
