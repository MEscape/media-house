"""Contract tests: every WorkspaceRepository implementation must behave identically."""

import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from media_house.modules.workspace.domain.repository import WorkspaceNameTaken, WorkspaceRepository
from media_house.modules.workspace.domain.values import WorkspaceId, WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.modules.workspace.infrastructure.sqlite_repository import SqliteWorkspaceRepository
from media_house.shared.errors import PersistenceError
from tests.support.fakes import T0, FixedClock, InMemoryWorkspaceRepository

pytestmark = pytest.mark.integration

Factory = Callable[[], WorkspaceRepository]


@pytest.fixture(params=["in_memory", "sqlite"])
def make_repository(request: pytest.FixtureRequest, tmp_path: Path) -> Factory:
    if request.param == "in_memory":
        shared = InMemoryWorkspaceRepository()
        return lambda: shared
    return lambda: SqliteWorkspaceRepository(tmp_path / "db" / "workspaces.db")


def new(name: str, clock: FixedClock | None = None) -> Workspace:
    return Workspace.create(WorkspaceName.of(name), now=(clock or FixedClock()).now())


def test_add_get_roundtrip(make_repository: Factory) -> None:
    repository = make_repository()
    workspace = new("Docs")
    repository.add(workspace)
    loaded = repository.get(workspace.id)
    assert loaded is not None
    assert (loaded.id, loaded.name, loaded.created_at) == (
        workspace.id,
        workspace.name,
        workspace.created_at,
    )
    assert loaded.pull_events() == []


def test_get_unknown_returns_none(make_repository: Factory) -> None:
    assert make_repository().get(WorkspaceId("missing")) is None


def test_list_is_ordered_oldest_first(make_repository: Factory) -> None:
    repository, clock = make_repository(), FixedClock()
    for name in ("first", "second", "third"):
        repository.add(new(name, clock))
        clock.advance(minutes=1)
    assert [w.name.value for w in repository.list_all()] == ["first", "second", "third"]


def test_names_are_unique_case_insensitively(make_repository: Factory) -> None:
    repository = make_repository()
    repository.add(new("Docs"))
    assert repository.name_exists(WorkspaceName.of("DOCS"))
    with pytest.raises(WorkspaceNameTaken):
        repository.add(new("docs"))
    assert len(repository.list_all()) == 1


def test_name_exists_false_for_unknown(make_repository: Factory) -> None:
    assert not make_repository().name_exists(WorkspaceName.of("nope"))


# --- SQLite-specific behaviour ------------------------------------------------ #
def test_sqlite_data_survives_a_new_repository_instance(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    SqliteWorkspaceRepository(path).add(new("Persistent"))
    assert [w.name.value for w in SqliteWorkspaceRepository(path).list_all()] == ["Persistent"]


def test_sqlite_unicode_and_timezone_roundtrip(tmp_path: Path) -> None:
    repository = SqliteWorkspaceRepository(tmp_path / "w.db")
    workspace = new("Café ☕")
    repository.add(workspace)
    loaded = repository.list_all()[0]
    assert loaded.name.value == "Café ☕"
    assert loaded.created_at == T0
    assert loaded.created_at.tzinfo is not None


def test_sqlite_is_safe_to_use_from_many_threads(tmp_path: Path) -> None:
    repository = SqliteWorkspaceRepository(tmp_path / "w.db")
    errors: list[BaseException] = []

    def add(index: int) -> None:
        try:
            repository.add(new(f"ws-{index}"))
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(repository.list_all()) == 8


def test_sqlite_corrupt_file_becomes_a_persistence_error(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 20)
    with pytest.raises(PersistenceError) as caught:
        SqliteWorkspaceRepository(path).list_all()
    assert caught.value.__cause__ is not None  # original exception preserved
    assert "database" in caught.value.details


def test_sqlite_corrupt_row_becomes_a_persistence_error(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "w.db"
    repository = SqliteWorkspaceRepository(path)
    repository.add(new("ok"))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE workspaces SET name = ''")
    with pytest.raises(PersistenceError, match="corrupt"):
        repository.list_all()
