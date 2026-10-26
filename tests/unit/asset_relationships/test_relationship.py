"""The stored claim: creation, canonical order, idempotent refresh, and protected decisions."""

from datetime import timedelta
from fractions import Fraction

import pytest

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.errors import IllegalTransition
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    OffsetOrigin,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.shared.errors import InvariantViolation
from tests.support.relationship_fakes import (
    CONTAINER,
    T0,
    WALL,
    assertion,
    clock_offset,
    interval,
    tag_match,
    wall_clock,
)

OVERLAP = RelationshipType.TEMPORALLY_OVERLAPS_WITH
TAKE = RelationshipType.ALTERNATE_TAKE_OF
LATER = T0 + timedelta(hours=1)
LATEST = T0 + timedelta(hours=2)
DETECTOR = Basis("take-detector", "1", {"inspection:a": "v1"})


def take_claim(
    a: str = "a", b: str = "b", *evidence: object, basis: Basis = DETECTOR
) -> ClaimInput:
    return ClaimInput.of(
        TAKE,
        a,
        b,
        origin=RelationshipOrigin.DETECTOR,
        basis=basis,
        evidence=tuple(evidence) or (tag_match(),),  # type: ignore[arg-type]
    )


def overlap_claim(**changes: object) -> ClaimInput:
    arguments: dict[str, object] = {
        "origin": RelationshipOrigin.DETECTOR,
        "basis": Basis("overlap", "1"),
        "evidence": (wall_clock("same_device"),),
    }
    return ClaimInput.of(OVERLAP, "a", "b", **(arguments | changes))  # type: ignore[arg-type]


def status_of(relationship: AssetRelationship) -> RelationshipStatus:
    """Reads the status afresh (a type checker would narrow a repeated attribute access)."""
    return relationship.status


def created(claim: ClaimInput | None = None) -> AssetRelationship:
    return AssetRelationship.create(claim or take_claim(), now=T0)


class TestClaimValidation:
    @pytest.mark.parametrize(
        "projected",
        [
            RelationshipType.EXACT_DUPLICATE_OF,
            RelationshipType.PROXY_OF,
            RelationshipType.DERIVED_FROM,
        ],
    )
    def test_recorded_lineage_cannot_be_stored(self, projected: RelationshipType) -> None:
        with pytest.raises(InvariantViolation):
            ClaimInput.of(
                projected,
                "a",
                "b",
                origin=RelationshipOrigin.USER,
                basis=Basis.manual(),
                evidence=(assertion(),),
            )

    @pytest.mark.parametrize(
        "origin", [RelationshipOrigin.LIBRARY_RECORD, RelationshipOrigin.PROXY_RECORD]
    )
    def test_a_claim_cannot_pretend_to_be_a_library_record(
        self, origin: RelationshipOrigin
    ) -> None:
        with pytest.raises(InvariantViolation):
            ClaimInput.of(
                TAKE, "a", "b", origin=origin, basis=Basis.manual(), evidence=(tag_match(),)
            )

    def test_needs_evidence(self) -> None:
        with pytest.raises(InvariantViolation):
            ClaimInput.of(
                TAKE, "a", "b", origin=RelationshipOrigin.USER, basis=Basis.manual(), evidence=()
            )

    def test_rejects_evidence_the_type_does_not_admit(self) -> None:
        with pytest.raises(InvariantViolation, match="not admissible"):
            ClaimInput.of(
                TAKE,
                "a",
                "b",
                origin=RelationshipOrigin.DETECTOR,
                basis=DETECTOR,
                evidence=(wall_clock(),),
            )

    def test_rejects_self_relations(self) -> None:
        with pytest.raises(InvariantViolation):
            take_claim("a", "a")

    def test_take_claims_have_no_time_ranges(self) -> None:
        with pytest.raises(InvariantViolation, match="no time ranges"):
            ClaimInput.of(
                TAKE,
                "a",
                "b",
                origin=RelationshipOrigin.DETECTOR,
                basis=DETECTOR,
                evidence=(tag_match(),),
                first_interval=interval(0, 5),
            )

    def test_overlap_intervals_must_overlap(self) -> None:
        with pytest.raises(InvariantViolation, match="do not overlap"):
            overlap_claim(first_interval=interval(0, 10), second_interval=interval(10, 20))

    def test_an_offset_moves_one_interval_before_the_comparison(self) -> None:
        claim = overlap_claim(
            first_interval=interval(0, 10, WALL),
            second_interval=interval(100, 110, CONTAINER),
            offset=clock_offset(100, 0),
        )

        assert claim.a_interval is not None

    def test_a_known_uncertainty_widens_the_shifted_interval(self) -> None:
        inside = overlap_claim(
            first_interval=interval(0, 10, WALL),
            second_interval=interval(11, 20, CONTAINER),
            offset=clock_offset(0, 2),
        )
        assert inside.b_interval is not None
        with pytest.raises(InvariantViolation):
            overlap_claim(
                first_interval=interval(0, 10, WALL),
                second_interval=interval(11, 20, CONTAINER),
                offset=clock_offset(0, Fraction(1, 2)),
            )

    def test_an_unknown_uncertainty_cannot_refute_a_claim(self) -> None:
        claim = overlap_claim(
            first_interval=interval(0, 10, WALL),
            second_interval=interval(500, 510, CONTAINER),
            offset=clock_offset(0, None),
        )

        assert claim.offset is not None

    def test_different_clocks_without_an_offset_are_not_compared(self) -> None:
        claim = overlap_claim(
            first_interval=interval(0, 10, WALL), second_interval=interval(500, 510, CONTAINER)
        )

        assert claim.a_interval is not None


class TestCanonicalOrder:
    def test_a_symmetric_pair_gets_one_id_whichever_way_it_is_asserted(self) -> None:
        assert take_claim("a", "b").relationship_id == take_claim("b", "a").relationship_id
        assert take_claim("b", "a").asset_a == "a"

    def test_intervals_follow_their_asset_and_the_offset_flips(self) -> None:
        one = interval(0, 10, WALL)
        two = interval(5, 15, CONTAINER)

        swapped = overlap_claim(first_interval=one, second_interval=two, offset=clock_offset(5, 1))
        flipped = ClaimInput.of(
            OVERLAP,
            "b",
            "a",
            origin=RelationshipOrigin.DETECTOR,
            basis=Basis("overlap", "1"),
            evidence=(wall_clock("same_device"),),
            first_interval=two,
            second_interval=one,
            offset=clock_offset(-5, 1),
        )

        assert flipped == swapped

    def test_an_id_that_does_not_match_the_endpoints_is_refused(self) -> None:
        claim = take_claim()

        with pytest.raises(InvariantViolation):
            AssetRelationship(
                id=RelationshipId("wrong"),
                type=claim.type,
                asset_a="a",
                asset_b="b",
                status=RelationshipStatus.PROPOSED,
                grade=Grade.INDICATED,
                origin=claim.origin,
                basis=claim.basis,
                evidence=claim.evidence,
                history=(),
                created_at=T0,
                updated_at=T0,
            )


class TestCreation:
    def test_a_new_claim_is_a_proposal_with_a_grade_and_a_history(self) -> None:
        relationship = created()

        assert relationship.status is RelationshipStatus.PROPOSED
        assert relationship.grade is Grade.INDICATED
        assert relationship.contradicted is False
        assert (relationship.created_at, relationship.updated_at) == (T0, T0)
        assert [(h.status_from, h.status_to) for h in relationship.history] == [
            (None, RelationshipStatus.PROPOSED)
        ]
        assert relationship.history[0].evidence_snapshot == ("production_tag_match|tag-detector|1",)

    def test_nothing_is_confirmed_by_automation_whatever_the_grade(self) -> None:
        strongest = take_claim("a", "b", tag_match(same_device=True))

        assert created(strongest).grade is Grade.CORROBORATED
        assert created(strongest).status is RelationshipStatus.PROPOSED

    def test_contradicting_evidence_is_kept_and_flagged(self) -> None:
        relationship = created(take_claim("a", "b", tag_match(scene_b="99")))

        assert (relationship.grade, relationship.contradicted) == (Grade.SUGGESTIVE, True)


class TestRefreshOfAProposal:
    def test_the_same_assertion_changes_nothing(self) -> None:
        relationship = created()
        before = (relationship.evidence, relationship.updated_at, len(relationship.history))

        assert relationship.refresh(take_claim(), now=LATER) is False

        assert (relationship.evidence, relationship.updated_at, len(relationship.history)) == before

    def test_new_evidence_from_the_same_provider_replaces_the_old(self) -> None:
        relationship = created()

        changed = relationship.refresh(take_claim("a", "b", tag_match(same_device=True)), now=LATER)

        assert changed is True
        assert relationship.updated_at == LATER
        assert len(relationship.current_evidence) == 1
        assert relationship.grade is Grade.CORROBORATED

    def test_evidence_from_another_provider_is_added(self) -> None:
        relationship = created()

        relationship.refresh(take_claim("a", "b", tag_match(), assertion("same scene")), now=LATER)

        assert {e.kind for e in relationship.current_evidence} == {
            EvidenceKind.PRODUCTION_TAG_MATCH,
            EvidenceKind.USER_ASSERTION,
        }

    def test_a_new_basis_is_recorded(self) -> None:
        relationship = created()

        relationship.refresh(take_claim(basis=Basis("take-detector", "2")), now=LATER)

        assert relationship.basis.version == "2"

    def test_a_stale_proposal_is_proposed_again_when_re_detected(self) -> None:
        relationship = created()
        relationship.mark_stale(now=LATER)

        relationship.refresh(take_claim(basis=Basis("take-detector", "2")), now=LATEST)

        assert relationship.status is RelationshipStatus.PROPOSED
        assert [h.status_to for h in relationship.history] == [
            RelationshipStatus.PROPOSED,
            RelationshipStatus.STALE,
            RelationshipStatus.PROPOSED,
        ]

    def test_refuses_a_claim_about_another_pair(self) -> None:
        with pytest.raises(InvariantViolation):
            created().refresh(take_claim("a", "c"), now=LATER)

    def test_intervals_and_offset_follow_the_latest_assertion(self) -> None:
        relationship = created(overlap_claim(first_interval=interval(0, 10)))

        relationship.refresh(overlap_claim(first_interval=interval(0, 20)), now=LATER)

        assert relationship.a_interval == interval(0, 20)


class TestDecisionsAreProtected:
    @pytest.mark.parametrize("decide", ["confirm", "reject"])
    def test_a_refresh_never_changes_a_decision(self, decide: str) -> None:
        relationship = created()
        getattr(relationship, decide)(now=LATER, note="checked")
        status = relationship.status

        relationship.refresh(take_claim("a", "b", tag_match(same_device=True)), now=LATEST)

        assert relationship.status is status

    def test_new_evidence_is_added_and_the_old_is_superseded_not_deleted(self) -> None:
        relationship = created()
        relationship.confirm(now=LATER)

        relationship.refresh(take_claim("a", "b", tag_match(same_device=True)), now=LATEST)

        assert [e.superseded for e in relationship.evidence] == [False, True]
        assert relationship.grade is Grade.CORROBORATED
        assert relationship.outdated_since == LATEST
        assert relationship.updated_at == LATEST

    def test_a_decided_claim_keeps_its_intervals(self) -> None:
        relationship = created(overlap_claim(first_interval=interval(0, 10)))
        relationship.confirm(now=LATER)

        relationship.refresh(overlap_claim(first_interval=interval(0, 20)), now=LATEST)

        assert relationship.a_interval == interval(0, 10)
        assert relationship.outdated_since == LATEST

    def test_a_new_basis_alone_marks_it_outdated(self) -> None:
        relationship = created()
        relationship.reject(now=LATER)

        relationship.refresh(take_claim(basis=Basis("take-detector", "2")), now=LATEST)

        assert relationship.outdated_since == LATEST
        assert relationship.status is RelationshipStatus.REJECTED

    def test_an_identical_refresh_of_a_decided_claim_changes_nothing(self) -> None:
        relationship = created()
        relationship.confirm(now=LATER)

        assert relationship.refresh(take_claim(), now=LATEST) is False
        assert relationship.outdated_since is None
        assert relationship.updated_at == LATER

    def test_a_person_deciding_again_clears_the_outdated_mark(self) -> None:
        relationship = created()
        relationship.confirm(now=LATER)
        relationship.refresh(take_claim(basis=Basis("take-detector", "2")), now=LATEST)

        relationship.reject(now=LATEST + timedelta(hours=1))

        assert relationship.outdated_since is None

    def test_the_first_outdated_moment_is_kept(self) -> None:
        relationship = created()
        relationship.confirm(now=LATER)
        relationship.refresh(take_claim(basis=Basis("take-detector", "2")), now=LATEST)

        relationship.refresh(
            take_claim(basis=Basis("take-detector", "3")), now=LATEST + timedelta(days=1)
        )

        assert relationship.outdated_since == LATEST


class TestTransitions:
    def test_confirm_and_reject_record_note_and_evidence_that_stood(self) -> None:
        relationship = created()

        relationship.confirm(now=LATER, note="  same scene  ")

        last = relationship.history[-1]
        assert (last.status_from, last.status_to) == (
            RelationshipStatus.PROPOSED,
            RelationshipStatus.CONFIRMED,
        )
        assert (last.at, last.note) == (LATER, "same scene")
        assert last.evidence_snapshot == ("production_tag_match|tag-detector|1",)
        assert relationship.updated_at == LATER

    @pytest.mark.parametrize(
        ("start", "decision", "allowed"),
        [
            (RelationshipStatus.PROPOSED, "confirm", True),
            (RelationshipStatus.PROPOSED, "reject", True),
            (RelationshipStatus.CONFIRMED, "confirm", False),
            (RelationshipStatus.CONFIRMED, "reject", True),
            (RelationshipStatus.REJECTED, "confirm", True),
            (RelationshipStatus.REJECTED, "reject", False),
            (RelationshipStatus.STALE, "confirm", False),
            (RelationshipStatus.STALE, "reject", True),
        ],
    )
    def test_the_decision_matrix(
        self, start: RelationshipStatus, decision: str, allowed: bool
    ) -> None:
        relationship = created()
        if start is RelationshipStatus.CONFIRMED:
            relationship.confirm(now=LATER)
        elif start is RelationshipStatus.REJECTED:
            relationship.reject(now=LATER)
        elif start is RelationshipStatus.STALE:
            relationship.mark_stale(now=LATER)

        action = getattr(relationship, decision)
        if allowed:
            action(now=LATEST)
        else:
            with pytest.raises(IllegalTransition):
                action(now=LATEST)
            assert relationship.status is start

    @pytest.mark.parametrize("decide", ["confirm", "reject"])
    def test_only_a_proposal_can_become_stale(self, decide: str) -> None:
        relationship = created()
        getattr(relationship, decide)(now=LATER)

        with pytest.raises(IllegalTransition):
            relationship.mark_stale(now=LATEST)

    def test_a_stale_claim_cannot_become_stale_again(self) -> None:
        relationship = created()
        relationship.mark_stale(now=LATER)

        with pytest.raises(IllegalTransition):
            relationship.mark_stale(now=LATEST)

    def test_a_rejection_is_reversible_only_by_a_person(self) -> None:
        relationship = created()
        relationship.reject(now=LATER)
        relationship.refresh(take_claim("a", "b", tag_match(same_device=True)), now=LATEST)
        assert status_of(relationship) is RelationshipStatus.REJECTED

        relationship.confirm(now=LATEST + timedelta(hours=1))

        assert status_of(relationship) is RelationshipStatus.CONFIRMED
        assert [h.status_to for h in relationship.history] == [
            RelationshipStatus.PROPOSED,
            RelationshipStatus.REJECTED,
            RelationshipStatus.CONFIRMED,
        ]


def test_the_manual_basis_is_stable() -> None:
    assert Basis.manual().canonical() == Basis.manual().canonical()
    assert Basis.from_canonical(Basis("d", "2", {"x": "y"}).canonical()) == Basis(
        "d", "2", {"x": "y"}
    )
    with pytest.raises(InvariantViolation):
        Basis.from_canonical("not json")


def test_offset_origin_is_kept_on_evidence() -> None:
    item = wall_clock("same_device", offset=clock_offset(1, 0, OffsetOrigin.USER_ASSERTED))

    assert item.offset is not None
    assert item.offset.origin is OffsetOrigin.USER_ASSERTED
