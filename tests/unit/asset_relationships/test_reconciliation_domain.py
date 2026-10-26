"""The reconciliation rules: pure functions over a claim, and the claim's new health methods."""

from datetime import timedelta

import pytest

from media_house.modules.asset_relationships.application.reconciliation import (
    ReconciliationSettings,
)
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.errors import IllegalTransition
from media_house.modules.asset_relationships.domain.reconciliation import (
    ClaimVerdict,
    PurgeReason,
    ReconcilePolicy,
    ScanState,
    looks_emptied,
    reconcile_claim,
)
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.values import (
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.shared.errors import InvariantViolation
from tests.support.relationship_fakes import T0, tag_match

POLICY = ReconcilePolicy(timedelta(days=7), timedelta(days=365))
LATER = T0 + timedelta(hours=1)
DETECTOR = Basis("take-detector", "1")


def claim(status: RelationshipStatus = RelationshipStatus.PROPOSED) -> AssetRelationship:
    made = AssetRelationship.create(
        ClaimInput.of(
            RelationshipType.ALTERNATE_TAKE_OF,
            "a",
            "b",
            origin=RelationshipOrigin.DETECTOR,
            basis=DETECTOR,
            evidence=(tag_match(),),
        ),
        now=T0,
    )
    if status is RelationshipStatus.CONFIRMED:
        made.confirm(now=T0)
    elif status is RelationshipStatus.REJECTED:
        made.reject(now=T0)
    elif status is RelationshipStatus.STALE:
        made.mark_stale(now=T0)
    return made


def judge(
    relationship: AssetRelationship,
    *,
    absent: set[str] | None = None,
    basis_current: bool | None = None,
    after: timedelta = timedelta(0),
) -> ClaimVerdict:
    return reconcile_claim(
        relationship,
        absent=absent or set(),
        basis_current=basis_current,
        now=LATER + after,
        policy=POLICY,
    )


UNDECIDED = [RelationshipStatus.PROPOSED, RelationshipStatus.STALE]
DECIDED = [RelationshipStatus.CONFIRMED, RelationshipStatus.REJECTED]


class TestClaimHealthMethods:
    def test_a_new_claim_is_healthy(self) -> None:
        assert claim().endpoint_missing_since is None

    def test_marking_keeps_the_first_moment(self) -> None:
        relationship = claim()

        assert relationship.mark_endpoint_missing(now=LATER) is True
        assert relationship.mark_endpoint_missing(now=LATER + timedelta(days=1)) is False

        assert relationship.endpoint_missing_since == LATER
        assert relationship.updated_at == LATER

    def test_clearing_is_idempotent(self) -> None:
        relationship = claim()
        relationship.mark_endpoint_missing(now=LATER)

        assert relationship.clear_endpoint_missing(now=LATER + timedelta(hours=1)) is True
        assert relationship.clear_endpoint_missing(now=LATER + timedelta(hours=2)) is False
        assert relationship.endpoint_missing_since is None

    def test_an_unchanged_marker_does_not_move_updated_at(self) -> None:
        relationship = claim()

        assert relationship.clear_endpoint_missing(now=LATER) is False
        assert relationship.updated_at == T0

    @pytest.mark.parametrize("status", DECIDED)
    def test_a_decided_claim_can_be_outdated_once_and_keeps_its_status(
        self, status: RelationshipStatus
    ) -> None:
        relationship = claim(status)

        assert relationship.mark_outdated(now=LATER) is True
        assert relationship.mark_outdated(now=LATER + timedelta(days=1)) is False

        assert (relationship.status, relationship.outdated_since) == (status, LATER)

    @pytest.mark.parametrize("status", UNDECIDED)
    def test_a_proposal_cannot_be_outdated(self, status: RelationshipStatus) -> None:
        with pytest.raises(IllegalTransition):
            claim(status).mark_outdated(now=LATER)

    def test_the_marker_is_part_of_what_a_refresh_compares(self) -> None:
        relationship = claim()
        relationship.mark_endpoint_missing(now=LATER)
        same = ClaimInput.of(
            RelationshipType.ALTERNATE_TAKE_OF,
            "a",
            "b",
            origin=RelationshipOrigin.DETECTOR,
            basis=DETECTOR,
            evidence=(tag_match(),),
        )

        assert relationship.refresh(same, now=LATER + timedelta(days=1)) is False
        assert relationship.endpoint_missing_since == LATER


class TestMissingEndpoints:
    @pytest.mark.parametrize("absent", [{"a"}, {"b"}, {"a", "b"}])
    def test_any_missing_endpoint_marks_the_claim_the_first_time(self, absent: set[str]) -> None:
        relationship = claim()

        verdict = judge(relationship, absent=absent)

        assert verdict == ClaimVerdict(marked_missing=True)
        assert relationship.endpoint_missing_since == LATER

    @pytest.mark.parametrize("status", list(RelationshipStatus))
    def test_the_sweep_that_first_sees_an_absence_never_purges(
        self, status: RelationshipStatus
    ) -> None:
        zero = ReconcilePolicy(timedelta(0), timedelta(0))
        relationship = claim(status)

        verdict = reconcile_claim(
            relationship, absent={"a"}, basis_current=None, now=LATER, policy=zero
        )

        assert verdict.purge is None
        assert verdict.marked_missing is True

    def test_a_marked_claim_is_not_marked_again(self) -> None:
        relationship = claim()
        judge(relationship, absent={"a"})

        verdict = judge(relationship, absent={"a"}, after=timedelta(days=1))

        assert verdict == ClaimVerdict()
        assert relationship.endpoint_missing_since == LATER

    @pytest.mark.parametrize("status", UNDECIDED)
    def test_an_undecided_claim_is_purged_after_the_grace_not_before(
        self, status: RelationshipStatus
    ) -> None:
        relationship = claim(status)
        judge(relationship, absent={"a"})

        early = judge(relationship, absent={"a"}, after=timedelta(days=7) - timedelta(seconds=1))
        due = judge(relationship, absent={"a"}, after=timedelta(days=7))

        assert early.purge is None
        assert due.purge is PurgeReason.UNDECIDED_ORPHAN

    @pytest.mark.parametrize("status", DECIDED)
    def test_a_decided_claim_is_kept_through_the_grace_and_purged_after_the_retention(
        self, status: RelationshipStatus
    ) -> None:
        relationship = claim(status)
        judge(relationship, absent={"a"})

        through_grace = judge(relationship, absent={"a"}, after=timedelta(days=30))
        before_retention = judge(relationship, absent={"a"}, after=timedelta(days=365, seconds=-1))
        due = judge(relationship, absent={"a"}, after=timedelta(days=365))

        assert through_grace.purge is None
        assert before_retention.purge is None
        assert due.purge is PurgeReason.RETENTION_EXPIRED
        assert relationship.status is status

    def test_the_rules_follow_the_claim_as_it_is_now(self) -> None:
        """A proposal due for purge is a decision when the rules look: the retention applies."""
        relationship = claim()
        judge(relationship, absent={"a"})
        relationship.confirm(now=LATER + timedelta(days=1))

        verdict = judge(relationship, absent={"a"}, after=timedelta(days=8))

        assert verdict.purge is None

    def test_a_purge_changes_nothing_on_the_claim(self) -> None:
        relationship = claim()
        judge(relationship, absent={"a"})
        before = (relationship.status, relationship.updated_at, relationship.endpoint_missing_since)

        judge(relationship, absent={"a"}, after=timedelta(days=8))

        assert (
            relationship.status,
            relationship.updated_at,
            relationship.endpoint_missing_since,
        ) == before


class TestRestoration:
    @pytest.mark.parametrize("status", list(RelationshipStatus))
    def test_when_every_endpoint_is_present_again_the_marker_is_cleared(
        self, status: RelationshipStatus
    ) -> None:
        relationship = claim(status)
        judge(relationship, absent={"a"})

        verdict = judge(relationship, after=timedelta(days=2))

        assert verdict == ClaimVerdict(restored=True)
        assert relationship.endpoint_missing_since is None

    def test_present_again_wins_even_after_the_grace_has_passed(self) -> None:
        relationship = claim()
        judge(relationship, absent={"a"})

        verdict = judge(relationship, after=timedelta(days=30))

        assert verdict.purge is None
        assert verdict.restored is True

    def test_one_endpoint_back_is_not_enough(self) -> None:
        relationship = claim()
        judge(relationship, absent={"a", "b"})

        verdict = judge(relationship, absent={"b"}, after=timedelta(days=1))

        assert verdict == ClaimVerdict()
        assert relationship.endpoint_missing_since == LATER

    def test_a_healthy_claim_with_present_endpoints_is_left_alone(self) -> None:
        relationship = claim()

        assert judge(relationship) == ClaimVerdict()
        assert relationship.updated_at == T0


class TestBasis:
    def test_a_proposal_with_an_out_of_date_basis_becomes_stale(self) -> None:
        relationship = claim()

        verdict = judge(relationship, basis_current=False)

        assert verdict == ClaimVerdict(marked_stale=True)
        assert relationship.status is RelationshipStatus.STALE

    @pytest.mark.parametrize("status", DECIDED)
    def test_a_decided_claim_is_only_marked_outdated(self, status: RelationshipStatus) -> None:
        relationship = claim(status)

        verdict = judge(relationship, basis_current=False)

        assert verdict == ClaimVerdict(marked_outdated=True)
        assert (relationship.status, relationship.outdated_since) == (status, LATER)

    def test_an_already_stale_claim_stays_stale_without_a_change(self) -> None:
        relationship = claim(RelationshipStatus.STALE)

        assert judge(relationship, basis_current=False) == ClaimVerdict()
        assert relationship.status is RelationshipStatus.STALE

    @pytest.mark.parametrize("current", [True, None])
    @pytest.mark.parametrize("status", list(RelationshipStatus))
    def test_a_current_or_unknown_basis_changes_nothing(
        self, status: RelationshipStatus, current: bool | None
    ) -> None:
        relationship = claim(status)

        assert judge(relationship, basis_current=current) == ClaimVerdict()
        assert relationship.status is status

    def test_an_outdated_decided_claim_is_not_marked_twice(self) -> None:
        relationship = claim(RelationshipStatus.CONFIRMED)
        judge(relationship, basis_current=False)

        assert judge(relationship, basis_current=False, after=timedelta(days=1)) == ClaimVerdict()
        assert relationship.outdated_since == LATER

    def test_a_missing_marker_and_a_stale_basis_are_both_applied(self) -> None:
        relationship = claim()

        verdict = judge(relationship, absent={"a"}, basis_current=False)

        assert verdict == ClaimVerdict(marked_missing=True, marked_stale=True)


class TestDecisionsAreNeverChanged:
    @pytest.mark.parametrize("status", DECIDED)
    @pytest.mark.parametrize("absent", [set(), {"a"}, {"a", "b"}])
    @pytest.mark.parametrize("basis_current", [True, False, None])
    def test_no_combination_changes_a_decided_status(
        self, status: RelationshipStatus, absent: set[str], basis_current: bool | None
    ) -> None:
        relationship = claim(status)
        history = relationship.history

        for days in (0, 1, 30, 400):
            judge(
                relationship, absent=absent, basis_current=basis_current, after=timedelta(days=days)
            )

        assert relationship.status is status
        assert relationship.history == history


class TestSuspicion:
    def test_most_of_a_big_batch_absent_looks_emptied(self) -> None:
        assert looks_emptied(claims=25, absent_endpoints=30, endpoints=50, minimum=20, ratio=0.5)

    def test_a_few_deleted_assets_do_not(self) -> None:
        assert not looks_emptied(claims=25, absent_endpoints=3, endpoints=50, minimum=20, ratio=0.5)

    def test_a_small_batch_never_does(self) -> None:
        assert not looks_emptied(
            claims=19, absent_endpoints=38, endpoints=38, minimum=20, ratio=0.5
        )

    def test_the_threshold_is_inclusive(self) -> None:
        assert looks_emptied(claims=20, absent_endpoints=20, endpoints=40, minimum=20, ratio=0.5)


class TestSettings:
    def test_the_defaults_are_the_approved_provisional_values(self) -> None:
        settings = ReconciliationSettings()

        assert settings.batch_claims == 250
        assert settings.max_claims_per_job == 10_000
        assert settings.min_sweep_interval == timedelta(hours=24)
        assert settings.undecided_orphan_grace == timedelta(days=7)
        assert settings.decided_retention == timedelta(days=365)
        assert settings.retry_delays_seconds == (1.0, 4.0, 16.0)
        assert settings.failure_backoff_base == timedelta(minutes=30)
        assert settings.failure_backoff_cap == timedelta(hours=24)
        assert (settings.suspicious_min_claims, settings.suspicious_ratio) == (20, 0.5)
        assert settings.suspicious_recheck_seconds == 5.0
        assert settings.policy == ReconcilePolicy(timedelta(days=7), timedelta(days=365))

    @pytest.mark.parametrize(
        "changes",
        [
            {"batch_claims": 0},
            {"max_claims_per_job": 0},
            {"suspicious_ratio": 0.0},
            {"suspicious_ratio": 1.5},
        ],
    )
    def test_nonsense_is_refused(self, changes: dict[str, float]) -> None:
        with pytest.raises(InvariantViolation):
            ReconciliationSettings(**changes)  # type: ignore[arg-type]

    def test_a_fresh_scan_state_is_empty(self) -> None:
        state = ScanState()

        assert (state.claim_cursor, state.sweep_generation, state.consecutive_failures) == (
            None,
            0,
            0,
        )
        assert (state.next_attempt_after, state.last_run) == (None, None)
