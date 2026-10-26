"""Evidence validation and the ordinal grade: deterministic, type-specific, never a probability."""

from datetime import datetime
from fractions import Fraction
from typing import Any

import pytest

from media_house.modules.asset_relationships.domain.assessment import assess
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    OffsetOrigin,
    RelationshipType,
)
from media_house.shared.errors import InvariantViolation
from tests.support.relationship_fakes import (
    T0,
    assertion,
    clock_offset,
    tag_match,
    wall_clock,
)

OVERLAP = RelationshipType.TEMPORALLY_OVERLAPS_WITH
TAKE = RelationshipType.ALTERNATE_TAKE_OF


def evidence(kind: EvidenceKind, detail: dict[str, Any], **rest: Any) -> RelationshipEvidence:
    return RelationshipEvidence(
        kind=kind,
        provider=rest.pop("provider", "p"),
        provider_version="1",
        observed_at=T0,
        detail=detail,
        **rest,
    )


def timecode(**rest: Any) -> RelationshipEvidence:
    return evidence(EvidenceKind.TIMECODE_OVERLAP, {"timecode_source": "video_stream"}, **rest)


class TestEvidenceValidation:
    @pytest.mark.parametrize(
        ("kind", "detail"),
        [
            (EvidenceKind.CHECKSUM_MATCH, {"sha256": "abc"}),
            (EvidenceKind.LIBRARY_DERIVATION, {"operation": "x"}),
            (EvidenceKind.LIBRARY_DERIVATION, {"operation": "x", "version": "1"}),
            (EvidenceKind.PROXY_PROVENANCE, {"profile": "analysis", "verdict": "valid"}),
            (EvidenceKind.WALL_CLOCK_OVERLAP, {}),
            (EvidenceKind.WALL_CLOCK_OVERLAP, {"clock_trust": "probably"}),
            (EvidenceKind.TIMECODE_OVERLAP, {"timecode_source": " "}),
            (EvidenceKind.PRODUCTION_TAG_MATCH, {"scene_a": "1", "scene_b": "1", "take_a": "1"}),
            (
                EvidenceKind.PRODUCTION_TAG_MATCH,
                {"scene_a": "1", "scene_b": "1", "take_a": "1", "take_b": "2", "same_reel": "yes"},
            ),
            (EvidenceKind.USER_ASSERTION, {"note": 5}),
        ],
    )
    def test_rejects_detail_that_does_not_fit_the_kind(
        self, kind: EvidenceKind, detail: dict[str, Any]
    ) -> None:
        with pytest.raises(InvariantViolation):
            evidence(kind, detail)

    def test_accepts_valid_detail_of_every_kind(self) -> None:
        valid = [
            evidence(EvidenceKind.CHECKSUM_MATCH, {"sha256": "a" * 64}),
            evidence(EvidenceKind.LIBRARY_DERIVATION, {"operation": "x", "version": 1}),
            evidence(
                EvidenceKind.PROXY_PROVENANCE,
                {"profile": "analysis", "verdict": "valid", "evidence": "packets"},
            ),
            wall_clock("same_device"),
            timecode(),
            tag_match(same_device=True),
            assertion("they look alike"),
        ]

        assert len(valid) == 7

    def test_detail_must_be_json_safe(self) -> None:
        with pytest.raises(InvariantViolation):
            evidence(EvidenceKind.USER_ASSERTION, {"note": "x", "when": datetime(2026, 1, 1)})  # noqa: DTZ001

    def test_needs_provider_and_an_aware_time(self) -> None:
        with pytest.raises(InvariantViolation):
            RelationshipEvidence(EvidenceKind.USER_ASSERTION, " ", "1", T0)
        with pytest.raises(InvariantViolation):
            RelationshipEvidence(EvidenceKind.USER_ASSERTION, "user", "1", datetime(2026, 1, 1))  # noqa: DTZ001

    def test_only_clock_evidence_can_carry_an_offset(self) -> None:
        with pytest.raises(InvariantViolation):
            evidence(EvidenceKind.USER_ASSERTION, {}, offset=clock_offset(1))
        assert wall_clock().offset is None

    def test_same_content_ignores_time_and_supersession(self) -> None:
        first = wall_clock("same_device")
        later = RelationshipEvidence(
            first.kind,
            first.provider,
            first.provider_version,
            datetime(2027, 1, 1, tzinfo=T0.tzinfo),
            first.detail,
        )

        assert first.same_content(later)
        assert first.same_content(first.as_superseded())
        assert not first.same_content(wall_clock("unverified"))

    def test_detail_is_copied(self) -> None:
        source = {"clock_trust": "unverified"}
        item = evidence(EvidenceKind.WALL_CLOCK_OVERLAP, source)
        source["clock_trust"] = "same_device"

        assert item.detail["clock_trust"] == "unverified"


class TestOverlapGrade:
    def test_wall_clock_without_a_verified_clock_is_only_suggestive(self) -> None:
        assert assess(OVERLAP, [wall_clock("unverified")]).grade is Grade.SUGGESTIVE

    @pytest.mark.parametrize("trust", ["same_device", "user_asserted"])
    def test_wall_clock_with_a_trusted_clock_is_indicated(self, trust: str) -> None:
        assert assess(OVERLAP, [wall_clock(trust)]).grade is Grade.INDICATED

    def test_timecode_alone_is_suggestive(self) -> None:
        assert assess(OVERLAP, [timecode()]).grade is Grade.SUGGESTIVE

    def test_a_persons_assertion_alone_does_not_raise_the_grade(self) -> None:
        assert assess(OVERLAP, [assertion()]).grade is Grade.SUGGESTIVE

    def test_a_persons_assertion_does_not_raise_a_graded_claim(self) -> None:
        graded = assess(OVERLAP, [wall_clock("unverified"), assertion()])

        assert graded.grade is Grade.SUGGESTIVE

    def test_agreeing_clocks_corroborate(self) -> None:
        result = assess(
            OVERLAP,
            [
                wall_clock(offset=clock_offset(10, Fraction(1, 10))),
                timecode(
                    offset=clock_offset(
                        Fraction(101, 10), Fraction(1, 10), OffsetOrigin.TIMECODE_DIFFERENCE
                    )
                ),
            ],
        )

        assert (result.grade, result.contradicted) == (Grade.CORROBORATED, False)

    def test_disagreeing_clocks_are_suggestive_and_flagged(self) -> None:
        result = assess(
            OVERLAP,
            [
                wall_clock("same_device", offset=clock_offset(10, 1)),
                timecode(offset=clock_offset(30, 1, OffsetOrigin.TIMECODE_DIFFERENCE)),
            ],
        )

        assert (result.grade, result.contradicted) == (Grade.SUGGESTIVE, True)

    @pytest.mark.parametrize(
        ("wall_offset", "tc_offset"),
        [
            (None, clock_offset(10, 1, OffsetOrigin.TIMECODE_DIFFERENCE)),
            (clock_offset(10, None), clock_offset(10, 1, OffsetOrigin.TIMECODE_DIFFERENCE)),
        ],
    )
    def test_agreement_that_cannot_be_established_does_not_corroborate(
        self, wall_offset: Any, tc_offset: Any
    ) -> None:
        result = assess(
            OVERLAP, [wall_clock("same_device", offset=wall_offset), timecode(offset=tc_offset)]
        )

        assert (result.grade, result.contradicted) == (Grade.INDICATED, False)

    def test_superseded_evidence_does_not_count(self) -> None:
        old = wall_clock("same_device").as_superseded()

        assert assess(OVERLAP, [old, timecode()]).grade is Grade.SUGGESTIVE


class TestTakeGrade:
    def test_same_scene_different_take_is_indicated(self) -> None:
        assert assess(TAKE, [tag_match()]).grade is Grade.INDICATED

    def test_comparison_ignores_case_and_spacing(self) -> None:
        assert assess(TAKE, [tag_match(scene_a="12A", scene_b=" 12a ")]).grade is Grade.INDICATED

    @pytest.mark.parametrize("flag", ["same_device", "same_reel"])
    def test_a_shared_source_corroborates(self, flag: str) -> None:
        item = tag_match(same_device=flag == "same_device", same_reel=flag == "same_reel")

        assert assess(TAKE, [item]).grade is Grade.CORROBORATED

    def test_a_false_shared_source_flag_does_not_corroborate(self) -> None:
        assert assess(TAKE, [tag_match(same_device=False)]).grade is Grade.INDICATED

    def test_different_scenes_contradict(self) -> None:
        result = assess(TAKE, [tag_match(scene_a="12", scene_b="13")])

        assert (result.grade, result.contradicted) == (Grade.SUGGESTIVE, True)

    def test_the_same_take_number_contradicts(self) -> None:
        result = assess(TAKE, [tag_match(take_a="2", take_b=" 2 ")])

        assert (result.grade, result.contradicted) == (Grade.SUGGESTIVE, True)

    def test_a_persons_assertion_alone_is_suggestive_and_not_contradicted(self) -> None:
        assert assess(TAKE, [assertion()]) == assess(TAKE, [assertion("same scene")])
        assert assess(TAKE, [assertion()]).contradicted is False


class TestAssessmentGuards:
    @pytest.mark.parametrize(
        "projected",
        [
            RelationshipType.EXACT_DUPLICATE_OF,
            RelationshipType.PROXY_OF,
            RelationshipType.DERIVED_FROM,
        ],
    )
    def test_projected_lineage_has_no_stored_grade(self, projected: RelationshipType) -> None:
        with pytest.raises(InvariantViolation):
            assess(projected, [assertion()])

    def test_a_claim_needs_current_evidence(self) -> None:
        with pytest.raises(InvariantViolation):
            assess(OVERLAP, [])
        with pytest.raises(InvariantViolation):
            assess(OVERLAP, [wall_clock().as_superseded()])

    def test_the_same_evidence_always_gives_the_same_grade(self) -> None:
        items = [wall_clock("same_device"), timecode()]

        assert {assess(OVERLAP, items).grade for _ in range(5)} == {Grade.INDICATED}

    def test_a_grade_is_never_a_number(self) -> None:
        assert not isinstance(assess(TAKE, [tag_match()]).grade, int | float)
