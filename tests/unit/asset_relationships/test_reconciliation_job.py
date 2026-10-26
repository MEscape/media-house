"""The reconciliation job over fakes: sweeps, retention, failures, resuming, cancelling, decisions.

The fakes keep the real semantics (the library skips unknown ids in ``get_many`` and raises on a
fault, the store applies each claim in its own atomic step and moves the cursor by compare-and-set),
so these tests prove the job's logic. The same behaviour against SQLite and the real library is in
``tests/integration/asset_relationships``.
"""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from media_house.modules.asset_relationships.application.commands import (
    EvidenceSubmission,
    ProposeRelationshipCommand,
)
from media_house.modules.asset_relationships.application.propose_relationship import (
    ProposeRelationship,
)
from media_house.modules.asset_relationships.application.reconciliation import (
    OutcomeReason,
    OutcomeStatus,
    ReconcileCommand,
    ReconcileOutcome,
    ReconcileRelationships,
    ReconciliationSettings,
)
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.reconciliation import PurgeReason
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.media_library.application.contracts import AssetStatus
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok, OperationCancelledError
from tests.support.fakes import T0, FixedClock
from tests.support.relationship_fakes import (
    FakeAssetLookup,
    InMemoryReconciliationStore,
    InMemoryRelationshipRepository,
    derivative,
    original,
    proxy,
    tag_match,
)

TAKE = RelationshipType.ALTERNATE_TAKE_OF
DETECTOR = Basis("take-detector", "1")
DAY = timedelta(days=1)


def settings(**changes: object) -> ReconciliationSettings:
    base = ReconciliationSettings(
        batch_claims=3,
        retry_delays_seconds=(0.0, 0.0, 0.0),
        suspicious_recheck_seconds=0.0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


class Rig:
    """A store, a library, a clock, and the job over them."""

    def __init__(
        self,
        claims: int = 7,
        *,
        config: ReconciliationSettings | None = None,
        checks: tuple[object, ...] = (),
    ) -> None:
        self.repository = InMemoryRelationshipRepository()
        self.store = InMemoryReconciliationStore(self.repository)
        self.library = FakeAssetLookup()
        self.clock = FixedClock()
        self.config = config or settings()
        self.ids: list[str] = []
        for number in range(claims):
            self.add(f"a{number}", f"b{number}")
        self.job = ReconcileRelationships(
            self.store,
            self.library,
            self.clock,
            checks,  # type: ignore[arg-type]
            self.config,
        )

    def add(
        self,
        first: str,
        second: str,
        *,
        status: RelationshipStatus = RelationshipStatus.PROPOSED,
        basis: Basis = DETECTOR,
        assets: bool = True,
    ) -> str:
        if assets:
            for asset_id in (first, second):
                if not self.library.has(asset_id):
                    self.library.add(original(asset_id))
        claim = ClaimInput.of(
            TAKE,
            first,
            second,
            origin=RelationshipOrigin.DETECTOR,
            basis=basis,
            evidence=(tag_match(),),
        )
        relationship = AssetRelationship.create(claim, now=T0)
        if status is RelationshipStatus.CONFIRMED:
            relationship.confirm(now=T0)
        elif status is RelationshipStatus.REJECTED:
            relationship.reject(now=T0)
        self.repository.update(claim.relationship_id, lambda _existing: relationship)
        self.ids.append(claim.relationship_id.value)
        return claim.relationship_id.value

    def claim(self, relationship_id: str) -> AssetRelationship | None:
        return self.repository.get(RelationshipId(relationship_id))

    def run(self, *, force: bool = False, ctx: JobContext | None = None) -> ReconcileOutcome:
        return self.job.reconcile(ReconcileCommand(force), ctx or JobContext.detached())

    def endpoints_of(self, relationship_id: str) -> tuple[str, str]:
        found = self.claim(relationship_id)
        assert found is not None
        return found.asset_a, found.asset_b

    def lose(self, asset_id: str) -> None:
        self.library.remove(asset_id)

    def marked(self) -> set[str]:
        return {
            i
            for i in self.ids
            if (c := self.claim(i)) is not None and c.endpoint_missing_since is not None
        }


class Progress:
    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        return None


def context(token: CancellationToken) -> JobContext:
    return JobContext("job", token, Progress())


class TestSweep:
    def test_a_sweep_visits_every_claim_once_in_batches_and_completes(self) -> None:
        rig = Rig(7)

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED
        assert outcome.reason is None
        assert (outcome.claims_checked, outcome.batches) == (7, 3)
        assert (outcome.cursor, outcome.sweep_generation) == (None, 1)
        assert [len(call) for call in rig.library.get_many_calls] == [6, 6, 2]
        assert rig.store.reconcile_calls == []  # nothing needed attention: no transaction

    def test_every_batch_is_one_library_call_of_distinct_endpoints(self) -> None:
        rig = Rig(0)
        for number in range(6):
            rig.add("shared", f"b{number}")

        rig.run()

        for call in rig.library.get_many_calls:
            assert len(call) == len(set(call))
        assert sum(len(call) for call in rig.library.get_many_calls) <= 2 * 6

    def test_an_empty_store_completes_at_once(self) -> None:
        rig = Rig(0)

        outcome = rig.run()

        assert (outcome.status, outcome.claims_checked, outcome.batches) == (
            OutcomeStatus.COMPLETED,
            0,
            0,
        )
        assert rig.library.get_many_calls == []

    def test_healthy_claims_cause_no_write_and_a_repeat_changes_nothing(self) -> None:
        rig = Rig(7)
        before = {i: rig.claim(i).updated_at for i in rig.ids}  # type: ignore[union-attr]

        first = rig.run()
        second = rig.run(force=True)

        assert first.claims_changed == second.claims_changed == 0
        assert {i: rig.claim(i).updated_at for i in rig.ids} == before  # type: ignore[union-attr]
        assert second.sweep_generation == 2

    def test_a_progress_report_follows_every_batch(self) -> None:
        reports: list[int] = []

        class Counting(Progress):
            def report(self, current: int, total: int | None = None, message: str = "") -> None:
                reports.append(current)

        Rig(7).run(ctx=JobContext("job", CancellationToken(), Counting()))

        assert reports == [3, 6, 7]

    def test_a_claim_deleted_while_the_job_runs_is_not_an_error(self) -> None:
        rig = Rig(7)
        victim = rig.ids[0]
        rig.library.on_get_many = lambda _ids: rig.repository._items.pop(victim, None)

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED


class TestOnlyWhatNeedsAttentionIsJudged:
    def test_a_healthy_sweep_opens_no_transaction_for_any_claim(self) -> None:
        rig = Rig(40)

        outcome = rig.run()

        assert outcome.claims_checked == 40
        assert rig.store.reconcile_calls == []

    def test_only_claims_with_a_missing_endpoint_are_judged(self) -> None:
        rig = Rig(7)
        rig.lose("a1")
        rig.lose("b3")

        rig.run()

        assert sorted(rig.store.reconcile_calls) == sorted([rig.ids[1], rig.ids[3]])

    def test_a_marked_claim_is_judged_again_until_it_is_restored_or_purged(self) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b")
        rig.lose("a")
        rig.run()
        rig.clock.advance(days=2)
        rig.run()
        assert rig.store.reconcile_calls == [victim, victim]
        rig.library.add(original("a"))
        rig.clock.advance(days=2)
        rig.run()  # present again: judged once more, cleared
        rig.clock.advance(days=2)
        rig.run()  # healthy now: not judged

        assert rig.store.reconcile_calls == [victim, victim, victim]
        assert rig.claim(victim).endpoint_missing_since is None  # type: ignore[union-attr]

    def test_with_a_basis_check_the_check_decides_which_claims_are_judged(self) -> None:
        current = Rig(7, checks=(TestVersionInvalidation.Versions({"take-detector": "1"}),))
        outdated = Rig(7, checks=(TestVersionInvalidation.Versions({"take-detector": "2"}),))

        current.run()
        outdated.run()

        assert current.store.reconcile_calls == []
        assert sorted(outdated.store.reconcile_calls) == sorted(outdated.ids)


class TestMissingEndpoints:
    def test_the_first_sweep_marks_but_never_purges_even_with_no_grace(self) -> None:
        rig = Rig(5, config=settings(undecided_orphan_grace=timedelta(0)))
        victim = rig.ids[2]
        rig.lose(rig.endpoints_of(victim)[0])

        outcome = rig.run()

        assert outcome.marked_missing == 1
        assert outcome.purged_undecided == 0
        assert rig.marked() == {victim}
        assert rig.claim(victim).endpoint_missing_since == T0  # type: ignore[union-attr]

    def test_an_undecided_claim_is_kept_through_the_grace_then_purged_with_an_audit_row(
        self,
    ) -> None:
        rig = Rig(5)
        victim = rig.ids[2]
        rig.lose(rig.endpoints_of(victim)[0])
        rig.run()

        rig.clock.advance(days=6)
        early = rig.run()
        rig.clock.advance(days=1)
        due = rig.run()

        assert early.purged_undecided == 0
        assert due.purged_undecided == 1
        assert rig.claim(victim) is None
        (row,) = rig.store.purge_log
        assert row["relationship_id"] == victim
        assert row["reason"] is PurgeReason.UNDECIDED_ORPHAN
        assert row["final_status"] is RelationshipStatus.PROPOSED
        assert row["decided_at"] is None
        assert row["endpoint_missing_since"] == T0

    @pytest.mark.parametrize("status", [RelationshipStatus.CONFIRMED, RelationshipStatus.REJECTED])
    def test_a_decided_claim_survives_the_grace_and_is_purged_after_the_retention(
        self, status: RelationshipStatus
    ) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b", status=status)
        rig.lose("a")
        rig.run()

        rig.clock.advance(days=300)
        rig.run()
        assert rig.claim(victim) is not None

        rig.clock.advance(days=65)
        outcome = rig.run()

        assert outcome.purged_decided == 1
        assert rig.claim(victim) is None
        (row,) = rig.store.purge_log
        assert row["reason"] is PurgeReason.RETENTION_EXPIRED
        assert row["final_status"] is status
        assert row["decided_at"] == T0

    def test_retention_counts_from_the_first_absence_not_the_latest_sweep(self) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b", status=RelationshipStatus.CONFIRMED)
        rig.lose("a")
        rig.run()
        for _ in range(14):
            rig.clock.advance(days=30)
            rig.run()
            if rig.claim(victim) is None:
                break

        assert rig.claim(victim) is None
        assert rig.store.purge_log[0]["endpoint_missing_since"] == T0

    def test_an_endpoint_that_returns_clears_the_marker_and_nothing_is_purged(self) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b")
        rig.lose("a")
        rig.run()
        rig.library.add(original("a"))
        rig.clock.advance(days=30)  # long past the grace

        outcome = rig.run()

        assert outcome.restored == 1
        assert outcome.purged_undecided == 0
        assert rig.claim(victim).endpoint_missing_since is None  # type: ignore[union-attr]
        assert rig.store.purge_log == []

    def test_a_claim_is_healthy_only_when_both_endpoints_are_present(self) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b")
        rig.lose("a")
        rig.lose("b")
        rig.run()
        rig.library.add(original("a"))
        rig.clock.advance(days=2)

        rig.run()

        assert rig.claim(victim).endpoint_missing_since == T0  # type: ignore[union-attr]

    def test_an_archived_endpoint_is_present_and_never_purged(self) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b", status=RelationshipStatus.CONFIRMED)
        rig.library.add(original("a", status=AssetStatus.ARCHIVED))

        for _ in range(3):
            rig.clock.advance(days=400)
            outcome = rig.run()

        assert outcome.marked_missing == outcome.purged_decided == outcome.purged_undecided == 0
        assert rig.claim(victim).endpoint_missing_since is None  # type: ignore[union-attr]

    def test_a_purged_claim_is_gone_but_the_other_claims_are_untouched(self) -> None:
        rig = Rig(5)
        victim = rig.ids[0]
        rig.lose(rig.endpoints_of(victim)[0])
        rig.run()
        rig.clock.advance(days=8)

        rig.run()

        assert rig.claim(victim) is None
        assert all(rig.claim(i) is not None for i in rig.ids if i != victim)


class TestDecisionsSurvive:
    @pytest.mark.parametrize("status", [RelationshipStatus.CONFIRMED, RelationshipStatus.REJECTED])
    def test_a_decided_claim_keeps_its_status_whatever_happens_to_it(
        self, status: RelationshipStatus
    ) -> None:
        class Outdated:
            def is_current(self, basis: Basis) -> bool | None:
                return False

        rig = Rig(0, checks=(Outdated(),))
        victim = rig.add("a", "b", status=status)
        rig.lose("a")
        for _ in range(3):
            rig.clock.advance(days=30)
            rig.run()

        claim = rig.claim(victim)
        assert claim is not None
        assert claim.status is status
        assert claim.endpoint_missing_since is not None
        assert claim.outdated_since is not None

    def test_a_decision_made_while_the_job_is_looking_wins_over_the_purge(self) -> None:
        rig = Rig(0)
        victim = rig.add("a", "b")
        rig.lose("a")
        rig.run()
        rig.clock.advance(days=8)  # a proposal would now be purged
        rig.library.on_get_many = lambda _ids: rig.repository.update(
            rig.claim(victim).id,  # type: ignore[union-attr]
            lambda existing: _confirmed(existing),
        )

        outcome = rig.run()

        assert outcome.purged_undecided == outcome.purged_decided == 0
        assert rig.claim(victim).status is RelationshipStatus.CONFIRMED  # type: ignore[union-attr]
        assert rig.store.purge_log == []

    def test_a_documented_race_an_endpoint_back_after_the_lookup_is_still_purged(self) -> None:
        """The library and the relationship store are separate databases: no transaction spans them.

        An endpoint that reappears after the batch's lookup and before the purge commits is purged
        anyway; the next sweep can correct a marker on a claim that exists, not restore a purged
        one.
        """
        rig = Rig(0)
        victim = rig.add("a", "b")
        rig.lose("a")
        rig.run()
        rig.clock.advance(days=8)
        rig.library.on_get_many = lambda _ids: rig.library.add(original("a"))

        outcome = rig.run()

        assert outcome.purged_undecided == 1
        assert rig.claim(victim) is None
        rig.library.on_get_many = None
        later = rig.run(force=True)
        assert (later.restored, later.purged_undecided) == (0, 0)
        assert rig.claim(victim) is None


def _confirmed(existing: AssetRelationship | None) -> AssetRelationship | None:
    assert existing is not None
    existing.confirm(now=T0 + DAY)
    return existing


class TestFailures:
    def test_a_failing_library_is_retried_and_then_succeeds(self) -> None:
        rig = Rig(2)
        rig.library.fail_get_many = 2

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED
        assert outcome.retries == 2
        assert len(rig.library.get_many_calls) == 3

    def test_a_library_that_keeps_failing_ends_partial_with_nothing_written(self) -> None:
        rig = Rig(7)
        rig.lose("a0")
        rig.library.fail_get_many = 99

        outcome = rig.run()

        assert (outcome.status, outcome.reason) == (
            OutcomeStatus.PARTIAL,
            OutcomeReason.TRANSIENT_LIBRARY,
        )
        assert outcome.retries == 3
        assert len(rig.library.get_many_calls) == 4  # the attempt and three retries
        assert (outcome.batches, outcome.claims_checked, outcome.cursor) == (0, 0, None)
        assert rig.marked() == set()
        assert rig.store.load_state().claim_cursor is None

    def test_a_failure_is_never_taken_for_an_absence(self) -> None:
        rig = Rig(1)
        rig.lose("a0")
        rig.library.fail_get_many = 99

        rig.run()
        rig.clock.advance(days=30)
        rig.library.fail_get_many = 99
        rig.run(force=True)

        assert rig.marked() == set()
        assert rig.store.purge_log == []

    def test_the_failure_backoff_gates_the_next_attempt_even_when_forced(self) -> None:
        rig = Rig(4)
        rig.library.fail_get_many = 99
        failed = rig.run()
        state = rig.store.load_state()

        assert state.consecutive_failures == 1
        assert state.next_attempt_after == T0 + timedelta(minutes=30)
        assert failed.next_attempt_after == T0 + timedelta(minutes=30)
        rig.library.fail_get_many = 0

        blocked = rig.run(force=True)
        assert (blocked.status, blocked.reason) == (OutcomeStatus.NOT_DUE, OutcomeReason.BACKOFF)
        calls = len(rig.library.get_many_calls)
        rig.run(force=True)
        assert len(rig.library.get_many_calls) == calls

        rig.clock.advance(minutes=30)
        resumed = rig.run()

        assert resumed.status is OutcomeStatus.COMPLETED
        state = rig.store.load_state()
        assert (state.consecutive_failures, state.next_attempt_after) == (0, None)

    def test_repeated_failures_back_off_exponentially_up_to_the_cap(self) -> None:
        rig = Rig(2)
        waits: list[timedelta] = []
        for _ in range(8):
            rig.library.fail_get_many = 99
            outcome = rig.run(force=True)
            assert outcome.next_attempt_after is not None
            waits.append(outcome.next_attempt_after - outcome.finished_at)
            rig.clock.advance(days=2)

        assert waits[:3] == [timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2)]
        assert waits[-1] == timedelta(hours=24)
        assert max(waits) == timedelta(hours=24)

    def test_an_unexpected_exception_propagates_and_is_recorded_as_failed(self) -> None:
        rig = Rig(3)
        rig.library.defect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            rig.run()

        last = rig.job.last_run()
        assert last is not None
        assert (last.status, last.reason) == (OutcomeStatus.FAILED, OutcomeReason.UNEXPECTED)
        assert rig.store.load_state().consecutive_failures == 1

    def test_an_unreadable_claim_is_skipped_and_counted_and_the_rest_is_applied(self) -> None:
        rig = Rig(7)
        broken = rig.ids[3]
        rig.store.corrupt.add(broken)
        rig.lose(rig.endpoints_of(broken)[0])  # it needs attention, so it is looked at
        rig.lose(rig.endpoints_of(rig.ids[5])[0])

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED
        assert outcome.skipped_corrupt == 1
        assert rig.ids[5] in rig.marked()

    def test_a_healthy_unreadable_claim_is_not_noticed_until_it_needs_attention(self) -> None:
        rig = Rig(5)
        rig.store.corrupt.add(rig.ids[2])

        outcome = rig.run()

        assert (outcome.status, outcome.skipped_corrupt) == (OutcomeStatus.COMPLETED, 0)


class TestSuspiciousLibrary:
    def two_batches(self) -> tuple[Rig, list[str], list[str]]:
        rig = Rig(50, config=settings(batch_claims=25))
        ordered = [ref.relationship_id for ref in rig.store.claims_after(None, 100)]
        return rig, ordered[:25], ordered[25:]

    def test_a_batch_that_stays_suspicious_writes_nothing_and_keeps_the_cursor_behind(self) -> None:
        rig, first, second = self.two_batches()
        for relationship_id in first[:3]:  # an ordinary loss in the first batch
            rig.lose(rig.endpoints_of(relationship_id)[0])
        for relationship_id in second:  # the library looks emptied for the second
            for asset_id in rig.endpoints_of(relationship_id):
                rig.lose(asset_id)

        outcome = rig.run()

        assert (outcome.status, outcome.reason) == (
            OutcomeStatus.PARTIAL,
            OutcomeReason.SUSPICIOUS_LIBRARY,
        )
        assert (outcome.batches, outcome.claims_checked) == (1, 25)
        assert outcome.cursor == first[-1]
        assert rig.store.load_state().claim_cursor == first[-1]
        assert rig.marked() == set(first[:3])  # the earlier batch stays committed
        for relationship_id in second:
            untouched = rig.claim(relationship_id)
            assert untouched is not None
            assert (untouched.endpoint_missing_since, untouched.updated_at) == (None, T0)
        assert len(rig.library.get_many_calls) == 3  # batch 1, batch 2, and its recheck

    def test_the_next_attempt_resumes_from_the_suspicious_batch_after_the_backoff(self) -> None:
        rig, _first, second = self.two_batches()
        lost: list[str] = []
        for relationship_id in second:
            lost += list(rig.endpoints_of(relationship_id))
        for asset_id in lost:
            rig.lose(asset_id)
        rig.run()
        state = rig.store.load_state()
        assert (state.consecutive_failures, state.next_attempt_after) == (
            1,
            T0 + timedelta(minutes=30),
        )
        for asset_id in lost:  # the library is itself again
            rig.library.add(original(asset_id))

        blocked = rig.run()
        rig.clock.advance(minutes=30)
        resumed = rig.run()

        assert blocked.status is OutcomeStatus.NOT_DUE
        assert resumed.status is OutcomeStatus.COMPLETED
        assert resumed.claims_checked == 25  # only the second batch was left
        assert rig.marked() == set()

    def test_a_recheck_that_comes_back_normal_lets_the_batch_proceed(self) -> None:
        rig, _first, second = self.two_batches()
        lost: list[str] = []
        for relationship_id in second:
            lost += list(rig.endpoints_of(relationship_id))
        for asset_id in lost:
            rig.lose(asset_id)
        calls = {"n": 0}

        def flaky(_ids: list[str]) -> None:
            calls["n"] += 1
            if calls["n"] == 2:  # right after the second batch's first lookup
                for asset_id in lost:
                    rig.library.add(original(asset_id))

        rig.library.on_get_many = flaky

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED
        assert rig.marked() == set()

    def test_a_small_batch_is_never_suspicious(self) -> None:
        rig = Rig(5)
        for number in range(5):
            rig.lose(f"a{number}")
            rig.lose(f"b{number}")

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED
        assert outcome.marked_missing == 5


class TestBoundedAndResumable:
    def test_the_budget_ends_a_job_and_the_next_ones_finish_the_sweep(self) -> None:
        rig = Rig(53, config=settings(batch_claims=5, max_claims_per_job=20))

        jobs = [rig.run()]
        while jobs[-1].status is not OutcomeStatus.COMPLETED:
            jobs.append(rig.run())

        assert [(j.status, j.reason) for j in jobs[:-1]] == [
            (OutcomeStatus.PARTIAL, OutcomeReason.BUDGET)
        ] * 2
        assert [j.claims_checked for j in jobs] == [20, 20, 13]
        assert sum(j.claims_checked for j in jobs) == 53
        assert jobs[-1].sweep_generation == 1

    def test_a_job_never_handles_more_claims_than_its_budget(self) -> None:
        rig = Rig(40, config=settings(batch_claims=5, max_claims_per_job=12))

        outcome = rig.run()

        assert outcome.claims_checked <= 12 + 5 - 1  # whole batches only
        assert outcome.claims_checked == 15
        assert outcome.cursor is not None

    def test_a_large_store_costs_one_library_call_per_batch(self) -> None:
        rig = Rig(1000, config=ReconciliationSettings())

        outcome = rig.run()

        assert outcome.status is OutcomeStatus.COMPLETED
        assert len(rig.library.get_many_calls) == 4
        assert all(len(call) <= 500 for call in rig.library.get_many_calls)

    def test_a_cursor_in_progress_is_continued_without_waiting_for_the_interval(self) -> None:
        rig = Rig(9, config=settings(batch_claims=3, max_claims_per_job=3))
        finished = rig.run()  # one batch per job: sweep 1 takes four jobs
        while finished.status is not OutcomeStatus.COMPLETED:
            finished = rig.run()
        rig.clock.advance(hours=1)

        started = rig.run(force=True)  # forced: starts sweep 2 inside the interval
        continued = rig.run()  # not forced: the sweep in progress continues anyway

        assert started.reason is OutcomeReason.BUDGET
        assert continued.status is not OutcomeStatus.NOT_DUE

    def test_a_new_sweep_waits_for_the_interval_unless_forced(self) -> None:
        rig = Rig(3)
        rig.run()

        inside = rig.run()
        rig.clock.advance(hours=24)
        due = rig.run()

        assert (inside.status, inside.reason) == (OutcomeStatus.NOT_DUE, OutcomeReason.INTERVAL)
        assert due.status is OutcomeStatus.COMPLETED
        assert due.sweep_generation == 2

    def test_a_not_due_answer_is_not_recorded_as_the_last_run(self) -> None:
        rig = Rig(3)
        done = rig.run()
        rig.run()

        assert rig.job.last_run() == done

    def test_the_last_run_survives_a_new_instance(self) -> None:
        rig = Rig(3)
        done = rig.run()
        again = ReconcileRelationships(rig.store, rig.library, rig.clock, (), rig.config)

        assert again.last_run() == done


class TestCancellationAndRivals:
    def test_cancelling_records_the_interruption_and_a_later_job_resumes(self) -> None:
        rig = Rig(9)
        token = CancellationToken()
        calls = {"n": 0}

        def cancel_during_second_batch(_ids: list[str]) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                token.cancel()

        rig.library.on_get_many = cancel_during_second_batch

        with pytest.raises(OperationCancelledError):
            rig.run(ctx=context(token))

        last = rig.job.last_run()
        assert last is not None
        assert (last.status, last.reason) == (OutcomeStatus.INTERRUPTED, OutcomeReason.CANCELLED)
        assert (last.batches, last.claims_checked) == (2, 6)
        ordered = [ref.relationship_id for ref in rig.store.claims_after(None, 100)]
        assert rig.store.load_state().claim_cursor == ordered[5]

        rig.library.on_get_many = None
        resumed = rig.run()

        assert resumed.status is OutcomeStatus.COMPLETED
        assert resumed.claims_checked == 3  # only what was left

    def test_cancelling_does_not_count_as_a_failure(self) -> None:
        rig = Rig(6)
        token = CancellationToken()
        token.cancel()

        with pytest.raises(OperationCancelledError):
            rig.run(ctx=context(token))

        state = rig.store.load_state()
        assert (state.consecutive_failures, state.next_attempt_after) == (0, None)

    def test_a_rival_that_moved_the_cursor_first_supersedes_this_job(self) -> None:
        rig = Rig(7)
        rig.library.on_get_many = lambda _ids: rig.store.advance_cursor(None, "ffff", now=T0)

        outcome = rig.run()

        assert (outcome.status, outcome.reason) == (OutcomeStatus.SUPERSEDED, None)
        assert rig.store.load_state().claim_cursor == "ffff"  # the rival's, untouched
        assert rig.store.load_state().consecutive_failures == 0


class TestVersionInvalidation:
    class Versions:
        def __init__(self, current: dict[str, str]) -> None:
            self._current = current

        def is_current(self, basis: Basis) -> bool | None:
            known = self._current.get(basis.detector)
            return None if known is None else basis.version == known

    def test_an_out_of_date_proposal_becomes_stale_and_a_decided_claim_is_only_outdated(
        self,
    ) -> None:
        rig = Rig(0, checks=(self.Versions({"take-detector": "2"}),))
        proposal = rig.add("a1", "b1")
        confirmed = rig.add("a2", "b2", status=RelationshipStatus.CONFIRMED)
        rejected = rig.add("a3", "b3", status=RelationshipStatus.REJECTED)

        outcome = rig.run()

        assert (outcome.marked_stale, outcome.marked_outdated) == (1, 2)
        assert rig.claim(proposal).status is RelationshipStatus.STALE  # type: ignore[union-attr]
        for decided, status in (
            (confirmed, RelationshipStatus.CONFIRMED),
            (rejected, RelationshipStatus.REJECTED),
        ):
            claim = rig.claim(decided)
            assert claim is not None
            assert (claim.status, claim.outdated_since) == (status, T0)

    def test_a_manual_basis_an_unknown_detector_and_a_current_version_are_left_alone(self) -> None:
        rig = Rig(0, checks=(self.Versions({"take-detector": "1"}),))
        manual = rig.add("a1", "b1", basis=Basis.manual())
        other = rig.add("a2", "b2", basis=Basis("other-detector", "9"))
        current = rig.add("a3", "b3")

        outcome = rig.run()

        assert outcome.marked_stale == outcome.marked_outdated == 0
        for relationship_id in (manual, other, current):
            assert rig.claim(relationship_id).status is RelationshipStatus.PROPOSED  # type: ignore[union-attr]

    def test_the_first_check_that_knows_the_detector_decides(self) -> None:
        rig = Rig(
            0,
            checks=(
                self.Versions({}),
                self.Versions({"take-detector": "2"}),
                self.Versions({"take-detector": "1"}),
            ),
        )
        claim = rig.add("a1", "b1")

        rig.run()

        assert rig.claim(claim).status is RelationshipStatus.STALE  # type: ignore[union-attr]

    def test_without_any_check_nothing_is_invalidated(self) -> None:
        rig = Rig(0)
        claim = rig.add("a1", "b1")

        outcome = rig.run()

        assert outcome.marked_stale == 0
        assert rig.claim(claim).status is RelationshipStatus.PROPOSED  # type: ignore[union-attr]

    def test_a_re_assertion_by_the_new_version_returns_the_claim_to_proposed(self) -> None:
        rig = Rig(0, checks=(self.Versions({"take-detector": "2"}),))
        claim = rig.add("a1", "b1")
        rig.run()
        assert rig.claim(claim).status is RelationshipStatus.STALE  # type: ignore[union-attr]
        propose = ProposeRelationship(rig.repository, rig.library, rig.clock)

        result = propose.execute(
            ProposeRelationshipCommand(
                TAKE,
                "a1",
                "b1",
                RelationshipOrigin.DETECTOR,
                (
                    EvidenceSubmission(
                        EvidenceKind.PRODUCTION_TAG_MATCH,
                        "tag-detector",
                        "1",
                        {"scene_a": "12", "scene_b": "12", "take_a": "1", "take_b": "2"},
                    ),
                ),
                Basis("take-detector", "2"),
            )
        )
        rig.clock.advance(days=2)
        later = rig.run()

        assert isinstance(result, Ok)
        assert rig.claim(claim).status is RelationshipStatus.PROPOSED  # type: ignore[union-attr]
        assert later.marked_stale == 0  # the new version is the current one


class TestLineageStaysDerived:
    def test_reconciliation_never_reads_lineage_and_stores_none(self) -> None:
        rig = Rig(3)
        rig.library.add(derivative("out", "a0"))
        rig.library.add(proxy("prx", "a0"))
        rig.library.gets.clear()

        rig.run()

        assert rig.library.searches == []
        assert rig.library.checksum_lookups == []
        assert rig.library.gets == []
        assert len(rig.repository.find(RelationshipCriteria("a0"))) == 1


class TestOutcome:
    def test_the_outcome_survives_a_json_round_trip(self) -> None:
        rig = Rig(5)
        rig.lose("a1")
        outcome = rig.run()

        assert ReconcileOutcome.from_json(outcome.to_json()) == outcome
        assert json.loads(outcome.to_json())["status"] == "completed"

    def test_the_counters_describe_what_happened(self) -> None:
        rig = Rig(5)
        rig.lose("a1")
        rig.lose("b3")

        outcome = rig.run()

        assert (outcome.claims_checked, outcome.claims_changed, outcome.marked_missing) == (5, 2, 2)
        assert (outcome.restored, outcome.purged_undecided, outcome.purged_decided) == (0, 0, 0)
        assert outcome.started_at == T0
        assert outcome.finished_at >= outcome.started_at

    def test_no_library_path_or_evidence_is_in_the_recorded_outcome(self) -> None:
        rig = Rig(2)

        text = rig.run().to_json()

        assert "a0" not in text and "tag" not in text
