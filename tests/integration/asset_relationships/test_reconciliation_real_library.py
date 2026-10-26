"""Reconciliation against the REAL Media Library, the real SQLite store and the real wiring.

Assets are imported, archived and deleted through ``MediaLibrary``; the job comes from the container
as ``RelationshipMaintenance``; time is the test's fixed clock, advanced by days. Nothing about
lineage is stored: a library with derivatives and proxies is swept and the store holds claims only.
"""

import sqlite3
from collections.abc import Sequence
from contextlib import closing

import pytest

from media_house.modules.asset_relationships.application.contracts import (
    EvidenceKind,
    EvidenceSubmission,
    OutcomeReason,
    OutcomeStatus,
    ProposeRelationshipCommand,
    ReconcileCommand,
    RelationshipOrigin,
    RelationshipQuery,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.asset_relationships.application.reconciliation import (
    ReconcileRelationships,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_reconciliation_store import (
    SqliteReconciliationStore,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, NotFoundError, Ok, PersistenceError
from tests.integration.asset_relationships.conftest import Env
from tests.support.proxy_fakes import source_facts

pytestmark = pytest.mark.integration

TAKE = RelationshipType.ALTERNATE_TAKE_OF
FACTS = source_facts()


def reconcile(env: Env, *, force: bool = False):  # type: ignore[no-untyped-def]
    return env.maintenance.reconcile(ReconcileCommand(force), JobContext.detached())


def related(env: Env, first: str, second: str, *, decided: bool = False) -> str:
    proposed = env.editor.propose(
        ProposeRelationshipCommand(
            TAKE,
            first,
            second,
            RelationshipOrigin.USER,
            (EvidenceSubmission(EvidenceKind.USER_ASSERTION, "user", "1"),),
        )
    )
    assert isinstance(proposed, Ok), proposed
    if decided:
        assert isinstance(env.editor.confirm(proposed.value.relationship_id, "same scene"), Ok)
    return proposed.value.relationship_id


def stored_ids(env: Env) -> list[str]:
    with closing(sqlite3.connect(env.paths.data_dir / "asset_relationships.db")) as conn:
        return [r[0] for r in conn.execute("SELECT relationship_id FROM relationships")]


def purge_rows(env: Env) -> list[tuple[str, str, str]]:
    with closing(sqlite3.connect(env.paths.data_dir / "asset_relationships.db")) as conn:
        return [
            tuple(r)
            for r in conn.execute("SELECT relationship_id, final_status, reason FROM purge_log")
        ]


def two_clips(env: Env) -> tuple[str, str]:
    return env.import_media(env.samples.cfr()), env.import_media(env.samples.cfr_25())


class TestDeletedAssets:
    def test_a_deleted_asset_marks_the_claim_and_the_grace_then_purges_it(self, env: Env) -> None:
        first, second = two_clips(env)
        claim = related(env, first, second)
        assert isinstance(env.library.delete(second), Ok)

        marked = reconcile(env)
        shown = env.catalog.get(claim)

        assert marked.status is OutcomeStatus.COMPLETED
        assert marked.marked_missing == 1
        assert isinstance(shown, Ok)
        assert shown.value.endpoint_missing_since == env.clock.now()
        assert shown.value.endpoint_states[second].value == "missing"

        env.clock.advance(days=6)
        assert reconcile(env, force=True).purged_undecided == 0
        assert isinstance(env.catalog.get(claim), Ok)

        env.clock.advance(days=1)
        due = reconcile(env, force=True)

        assert due.purged_undecided == 1
        gone = env.catalog.get(claim)
        assert isinstance(gone, Err)
        assert isinstance(gone.error, NotFoundError)
        assert purge_rows(env) == [(claim, "proposed", "undecided_orphan")]
        assert stored_ids(env) == []

    def test_a_confirmed_claim_outlives_the_grace_and_is_purged_after_the_retention(
        self, env: Env
    ) -> None:
        first, second = two_clips(env)
        claim = related(env, first, second, decided=True)
        assert isinstance(env.library.delete(first), Ok)
        reconcile(env)

        env.clock.advance(days=300)
        reconcile(env, force=True)
        still = env.catalog.get(claim)
        assert isinstance(still, Ok)
        assert still.value.status is RelationshipStatus.CONFIRMED
        assert still.value.endpoint_missing_since is not None

        env.clock.advance(days=66)
        due = reconcile(env, force=True)

        assert due.purged_decided == 1
        assert purge_rows(env) == [(claim, "confirmed", "retention_expired")]

    def test_deleting_a_source_with_derivatives_still_marks_the_claim_once(self, env: Env) -> None:
        first, second = two_clips(env)
        claim = related(env, first, second)
        registered = env.library.register_derived(
            first, env.samples.audio_only(), operation="test_extraction", config={"n": 1}
        )
        assert isinstance(registered, Ok)
        assert isinstance(env.library.delete(first), Ok)  # cascades to the derivative

        outcome = reconcile(env)

        assert outcome.marked_missing == 1
        shown = env.catalog.get(claim)
        assert isinstance(shown, Ok)
        assert shown.value.endpoint_states[first].value == "missing"


class TestArchiveAndRestore:
    def test_an_archived_endpoint_is_never_marked_or_purged(self, env: Env) -> None:
        first, second = two_clips(env)
        claim = related(env, first, second, decided=True)
        env.clock.advance(seconds=1)  # the library saves a change only when updated_at moves
        assert isinstance(env.library.archive(second), Ok)

        for _ in range(3):
            env.clock.advance(days=400)
            outcome = reconcile(env, force=True)

        shown = env.catalog.get(claim)
        assert isinstance(shown, Ok)
        assert shown.value.endpoint_missing_since is None
        assert shown.value.endpoint_states[second].value == "archived"
        assert (outcome.marked_missing, outcome.purged_decided, outcome.purged_undecided) == (
            0,
            0,
            0,
        )

    def test_restoring_an_archived_endpoint_changes_nothing(self, env: Env) -> None:
        first, second = two_clips(env)
        claim = related(env, first, second)
        env.clock.advance(seconds=1)
        env.library.archive(second)
        reconcile(env)
        env.clock.advance(seconds=1)
        assert isinstance(env.library.restore(second), Ok)
        env.clock.advance(days=2)

        outcome = reconcile(env)

        assert outcome.claims_changed == 0
        shown = env.catalog.get(claim)
        assert isinstance(shown, Ok)
        assert shown.value.endpoint_states[second].value == "active"


class TestLineageIsNotStored:
    def test_a_library_with_derivatives_and_proxies_adds_nothing_to_the_store(
        self, env: Env
    ) -> None:
        source = env.proxy.clip()
        other = env.import_media(env.samples.cfr())
        claim = related(env, source, other)
        use_case, _encoder, _probe = env.proxy.scripted(FACTS)
        env.proxy.ok(use_case, source, max_long_edge=480)
        env.library.register_derived(
            other, env.samples.audio_only(), operation="test_extraction", config={"n": 1}
        )

        outcome = reconcile(env)

        assert outcome.status is OutcomeStatus.COMPLETED
        assert stored_ids(env) == [claim]
        page = env.catalog.relationships_of(source, RelationshipQuery())
        assert isinstance(page, Ok)
        assert {i.type for i in page.value.items} == {TAKE, RelationshipType.PROXY_OF}
        assert stored_ids(env) == [claim]  # reading lineage afterwards still stores nothing


class TestMaintenanceContract:
    def test_the_contract_is_wired_and_remembers_the_last_run(self, env: Env) -> None:
        first, second = two_clips(env)
        related(env, first, second)
        assert env.maintenance.last_run() is None

        done = reconcile(env)
        again = reconcile(env)

        assert (done.status, again.status) == (OutcomeStatus.COMPLETED, OutcomeStatus.NOT_DUE)
        assert again.reason is OutcomeReason.INTERVAL
        assert env.maintenance.last_run() == done

    def test_the_state_survives_a_restart(self, env: Env) -> None:
        first, second = two_clips(env)
        related(env, first, second)
        done = reconcile(env)

        restarted = ReconcileRelationships(
            SqliteReconciliationStore(env.paths.data_dir / "asset_relationships.db"),
            env.library,
            env.clock,
        )

        assert restarted.last_run() == done
        assert restarted.reconcile(ReconcileCommand(), JobContext.detached()).status is (
            OutcomeStatus.NOT_DUE
        )

    def test_a_cancelled_job_leaves_a_resumable_state(self, env: Env) -> None:
        from media_house.shared.concurrency import CancellationToken
        from media_house.shared.errors import OperationCancelledError

        first, second = two_clips(env)
        related(env, first, second)
        token = CancellationToken()
        token.cancel()

        class Progress:
            def report(self, current: int, total: int | None = None, message: str = "") -> None:
                return None

        with pytest.raises(OperationCancelledError):
            env.maintenance.reconcile(ReconcileCommand(), JobContext("job", token, Progress()))

        last = env.maintenance.last_run()
        assert last is not None
        assert last.status is OutcomeStatus.INTERRUPTED
        assert reconcile(env).status is OutcomeStatus.COMPLETED


class FailingLibrary:
    """A library whose database fails, around the real one."""

    def __init__(self, real: object) -> None:
        self._real = real
        self.failing = True

    def get(self, asset_id: str):  # type: ignore[no-untyped-def]
        return self._real.get(asset_id)  # type: ignore[attr-defined]

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]:
        if self.failing:
            raise PersistenceError("The media database is unavailable")
        return self._real.get_many(asset_ids)  # type: ignore[attr-defined, no-any-return]


class TestTransientFailures:
    def test_a_failing_library_changes_nothing_and_a_later_job_recovers(self, env: Env) -> None:
        from media_house.modules.asset_relationships.application.reconciliation import (
            ReconciliationSettings,
        )

        first, second = two_clips(env)
        claim = related(env, first, second)
        assert isinstance(env.library.delete(second), Ok)
        library = FailingLibrary(env.library)
        job = ReconcileRelationships(
            SqliteReconciliationStore(env.paths.data_dir / "asset_relationships.db"),
            library,
            env.clock,
            (),
            ReconciliationSettings(retry_delays_seconds=(0.0, 0.0), suspicious_recheck_seconds=0.0),
        )

        failed = job.reconcile(ReconcileCommand(), JobContext.detached())

        assert (failed.status, failed.reason) == (
            OutcomeStatus.PARTIAL,
            OutcomeReason.TRANSIENT_LIBRARY,
        )
        assert failed.retries == 2
        shown = env.catalog.get(claim)
        assert isinstance(shown, Ok)
        assert shown.value.endpoint_missing_since is None  # a failure is not an absence

        library.failing = False
        blocked = job.reconcile(ReconcileCommand(force=True), JobContext.detached())
        assert blocked.reason is OutcomeReason.BACKOFF
        env.clock.advance(minutes=31)
        recovered = job.reconcile(ReconcileCommand(), JobContext.detached())

        assert recovered.status is OutcomeStatus.COMPLETED
        assert recovered.marked_missing == 1
