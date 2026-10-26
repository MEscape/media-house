"""Exact time on a named clock, relationship identity, and what each type admits."""

from fractions import Fraction

import pytest

from media_house.modules.asset_relationships.domain.intervals import (
    ClockOffset,
    TimeCoordinates,
    TimeInterval,
)
from media_house.modules.asset_relationships.domain.taxonomy import (
    SPECS,
    canonical_endpoints,
    spec_of,
)
from media_house.modules.asset_relationships.domain.values import (
    GRADE_ORDER,
    ClockDomain,
    EvidenceKind,
    Grade,
    OffsetOrigin,
    RelationshipId,
    RelationshipType,
    grades_at_least,
)
from media_house.shared.errors import InvariantViolation
from tests.support.relationship_fakes import CONTAINER, WALL, clock_offset, interval


class TestIntervals:
    def test_is_half_open(self) -> None:
        a, b = interval(0, 10), interval(10, 20)

        assert not a.overlaps(b)
        assert not b.overlaps(a)
        assert a.overlaps(interval(9, 11))

    @pytest.mark.parametrize(("start", "end"), [(5, 5), (6, 5)])
    def test_must_start_before_it_ends(self, start: int, end: int) -> None:
        with pytest.raises(InvariantViolation):
            interval(start, end)

    def test_negative_container_times_are_representable(self) -> None:
        pre_roll = interval(Fraction(-1, 48), Fraction(5), CONTAINER)

        assert pre_roll.start < 0

    @pytest.mark.parametrize("value", [1.5, 2])
    def test_refuses_anything_but_an_exact_fraction(self, value: float) -> None:
        with pytest.raises(InvariantViolation):
            TimeInterval(value, Fraction(9), WALL)  # type: ignore[arg-type]
        with pytest.raises(InvariantViolation):
            ClockOffset(value, None, OffsetOrigin.USER_ASSERTED)  # type: ignore[arg-type]

    def test_never_compares_across_clock_domains(self) -> None:
        with pytest.raises(InvariantViolation):
            interval(0, 10, WALL).overlaps(interval(0, 10, CONTAINER))

    def test_coordinates_must_say_what_zero_means(self) -> None:
        with pytest.raises(InvariantViolation):
            TimeCoordinates(ClockDomain.CONTAINER, " ")

    def test_shifting_moves_both_ends_into_the_target_coordinates(self) -> None:
        moved = interval(10, 20, WALL).shifted(Fraction(-3, 2), CONTAINER)

        assert (moved.start, moved.end, moved.coordinates) == (
            Fraction(17, 2),
            Fraction(37, 2),
            CONTAINER,
        )

    def test_does_not_drift_like_floats(self) -> None:
        third = Fraction(1, 3)

        assert interval(0, 1).shifted(third * 3, WALL).start == 1


class TestOffsets:
    def test_unknown_uncertainty_is_not_zero(self) -> None:
        assert clock_offset(5, None).uncertainty is None
        assert clock_offset(5, 0).uncertainty == 0

    def test_uncertainty_cannot_be_negative(self) -> None:
        with pytest.raises(InvariantViolation):
            clock_offset(1, -1)

    def test_inverting_flips_the_direction_and_keeps_the_rest(self) -> None:
        flipped = clock_offset(
            Fraction(3, 2), Fraction(1, 10), OffsetOrigin.USER_ASSERTED
        ).inverted()

        assert flipped.seconds == Fraction(-3, 2)
        assert flipped.uncertainty == Fraction(1, 10)
        assert flipped.origin is OffsetOrigin.USER_ASSERTED


class TestGrades:
    def test_the_order_is_strongest_first(self) -> None:
        assert GRADE_ORDER[0] is Grade.RECORDED
        assert GRADE_ORDER[-1] is Grade.SUGGESTIVE

    def test_at_least_includes_the_minimum_and_everything_stronger(self) -> None:
        assert grades_at_least(Grade.INDICATED) == {
            Grade.RECORDED,
            Grade.CORROBORATED,
            Grade.INDICATED,
        }
        assert grades_at_least(Grade.SUGGESTIVE) == set(Grade)


class TestTaxonomy:
    def test_only_two_types_are_stored_claims(self) -> None:
        stored = {t for t, spec in SPECS.items() if spec.storable}

        assert stored == {
            RelationshipType.TEMPORALLY_OVERLAPS_WITH,
            RelationshipType.ALTERNATE_TAKE_OF,
        }

    def test_every_type_has_a_spec(self) -> None:
        assert set(SPECS) == set(RelationshipType)

    def test_lineage_is_directed_and_claims_are_symmetric(self) -> None:
        directed = {
            RelationshipType.EXACT_DUPLICATE_OF,
            RelationshipType.PROXY_OF,
            RelationshipType.DERIVED_FROM,
        }

        assert {t for t, spec in SPECS.items() if not spec.symmetric} == directed

    def test_no_type_admits_weak_signals_as_evidence(self) -> None:
        admitted = {k for spec in SPECS.values() for k in spec.admissible}

        assert admitted == {
            EvidenceKind.CHECKSUM_MATCH,
            EvidenceKind.LIBRARY_DERIVATION,
            EvidenceKind.PROXY_PROVENANCE,
            EvidenceKind.WALL_CLOCK_OVERLAP,
            EvidenceKind.TIMECODE_OVERLAP,
            EvidenceKind.PRODUCTION_TAG_MATCH,
            EvidenceKind.USER_ASSERTION,
        }

    def test_only_overlap_has_time_ranges(self) -> None:
        assert {t for t, spec in SPECS.items() if spec.intervals} == {
            RelationshipType.TEMPORALLY_OVERLAPS_WITH
        }

    def test_a_symmetric_pair_is_stored_lower_id_first(self) -> None:
        kind = RelationshipType.ALTERNATE_TAKE_OF

        assert canonical_endpoints(kind, "b", "a") == ("a", "b", True)
        assert canonical_endpoints(kind, "a", "b") == ("a", "b", False)

    def test_a_directed_pair_keeps_its_direction(self) -> None:
        assert canonical_endpoints(RelationshipType.PROXY_OF, "z", "a") == ("z", "a", False)

    @pytest.mark.parametrize("pair", [("a", "a"), ("", "b"), ("a", "")])
    def test_rejects_self_relations_and_empty_ids(self, pair: tuple[str, str]) -> None:
        with pytest.raises(InvariantViolation):
            canonical_endpoints(RelationshipType.ALTERNATE_TAKE_OF, *pair)

    def test_spec_lookup(self) -> None:
        assert spec_of(RelationshipType.TEMPORALLY_OVERLAPS_WITH).symmetric


class TestRelationshipId:
    def test_is_deterministic(self) -> None:
        kind = RelationshipType.ALTERNATE_TAKE_OF

        assert RelationshipId.of(kind, "a", "b") == RelationshipId.of(kind, "a", "b")
        assert len(RelationshipId.of(kind, "a", "b").value) == 32

    def test_differs_by_type_and_by_direction(self) -> None:
        take, overlap = (
            RelationshipType.ALTERNATE_TAKE_OF,
            RelationshipType.TEMPORALLY_OVERLAPS_WITH,
        )

        assert RelationshipId.of(take, "a", "b") != RelationshipId.of(overlap, "a", "b")
        assert RelationshipId.of(take, "a", "b") != RelationshipId.of(take, "b", "a")

    def test_cannot_be_empty(self) -> None:
        with pytest.raises(InvariantViolation):
            RelationshipId("")
