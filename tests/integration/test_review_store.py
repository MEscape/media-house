"""``ReviewStore`` contract: the in-memory fake and the SQLite adapter behave the same."""

from collections.abc import Callable
from pathlib import Path

import pytest

from media_house.modules.review_orchestrator.application.ports import ReviewStore
from media_house.modules.review_orchestrator.domain import workflow as w
from media_house.modules.review_orchestrator.domain.permissions import PermissionPolicy
from media_house.modules.review_orchestrator.domain.session import ReviewSession
from media_house.modules.review_orchestrator.domain.values import ActorKind, ReviewState
from media_house.modules.review_orchestrator.infrastructure.sqlite_store import SqliteReviewStore
from media_house.shared.errors import ConflictError, PersistenceError
from tests.support.fakes import T0
from tests.support.review_fakes import ANCHOR, InMemoryReviewStore, make_facts

pytestmark = pytest.mark.integration


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ReviewStore:
    if request.param == "memory":
        return InMemoryReviewStore()
    return SqliteReviewStore(tmp_path / "review.db")


def new_session(session_id: str = "s1") -> w.Update:
    return w.open_session(
        session_id=session_id, project_id="p", facts=make_facts(1), reviewer="Ada",
        policy=PermissionPolicy(), resolve_project_ref="", resolve_timeline_ref="", now=T0,
    )  # fmt: skip


def moved(session: ReviewSession, to: ReviewState) -> w.Update:
    return w.advance(session, to, now=T0, actor="t", actor_kind=ActorKind.SYSTEM)


def test_a_session_round_trips_exactly(store: ReviewStore) -> None:
    update = new_session()
    store.create(update.session, update.entries)

    stored = store.get("s1")

    assert stored is not None and stored.revision == 1
    assert stored.session == update.session


def test_an_unknown_session_is_none(store: ReviewStore) -> None:
    assert store.get("nope") is None


def test_a_session_id_is_created_once(store: ReviewStore) -> None:
    update = new_session()
    store.create(update.session, update.entries)

    with pytest.raises(ConflictError):
        store.create(update.session, update.entries)


def test_a_save_needs_the_revision_that_was_read(store: ReviewStore) -> None:
    update = new_session()
    store.create(update.session, update.entries)
    step = moved(update.session, ReviewState.ANALYSIS_PENDING)

    assert store.save(step.session, step.entries, expected_revision=1) == 2
    with pytest.raises(ConflictError):  # a second writer still holding revision 1
        store.save(step.session, step.entries, expected_revision=1)
    stored = store.get("s1")
    assert stored is not None and stored.session.state is ReviewState.ANALYSIS_PENDING


def test_the_history_is_numbered_in_order_and_can_be_read_from_a_point(store: ReviewStore) -> None:
    update = new_session()
    store.create(update.session, update.entries)
    step = moved(update.session, ReviewState.ANALYSIS_PENDING)
    store.save(step.session, step.entries, expected_revision=1)

    everything = store.entries("s1")
    later = store.entries("s1", after=everything[0].sequence)

    assert [e.sequence for e in everything] == [1, 2]
    assert [e.kind for e in everything] == ["opened", "state"]
    assert [e.sequence for e in later] == [2]
    assert everything[1].data == (("from", "created"), ("to", "analysis_pending"))


def test_sessions_are_found_by_recording_timeline_and_activity(store: ReviewStore) -> None:
    first = new_session("s1")
    store.create(first.session, first.entries)
    cancelled = w.cancel(
        new_session("s2").session, now=T0, actor="a", actor_kind=ActorKind.HUMAN, reason="x"
    )
    store.create(cancelled.session, cancelled.entries)

    assert {s.session_id for s in store.find(ANCHOR)} == {"s1", "s2"}
    assert [s.session_id for s in store.find(ANCHOR, active_only=True)] == ["s1"]
    assert store.find(ANCHOR, timeline_id="other") == []
    assert store.find("other-recording") == []


def test_the_sqlite_store_refuses_a_stored_document_it_cannot_read(tmp_path: Path) -> None:
    store = SqliteReviewStore(tmp_path / "review.db")
    update = new_session()
    store.create(update.session, update.entries)
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(tmp_path / "review.db")) as conn:
        conn.execute("UPDATE sessions SET document = ?", ('{"document_type": "review_session"}',))
        conn.commit()

    read: Callable[[], object] = lambda: store.get("s1")  # noqa: E731
    with pytest.raises(PersistenceError, match="unreadable"):
        read()
