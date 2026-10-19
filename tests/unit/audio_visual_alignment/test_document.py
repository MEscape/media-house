"""Scoring rules, profile identity, stored form, validation and determinism."""

import json
from dataclasses import replace
from typing import Any

import pytest

from media_house.modules.audio_visual_alignment.domain.config import (
    PROFILES,
    AlignmentProfile,
    get_profile,
)
from media_house.modules.audio_visual_alignment.domain.errors import (
    InvalidAlignmentConfig,
    InvalidAlignmentDocument,
)
from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument, Scores
from media_house.modules.audio_visual_alignment.domain.scoring import (
    combine,
    coverage,
    gap,
    overlap,
    pearson,
    spread,
)
from media_house.modules.audio_visual_alignment.domain.serialization import (
    alignment_from_json,
    alignment_to_json,
)
from media_house.modules.audio_visual_alignment.domain.validation import validate_document
from media_house.modules.audio_visual_alignment.domain.values import SCHEMA_VERSION
from media_house.shared.errors import InvariantViolation
from tests.support.avl_facts import audio, content, inputs, segment, shot, talking, video
from tests.unit.audio_visual_alignment.helpers import run


class TestScoring:
    def test_a_missing_signal_is_left_out_not_counted_as_zero(self) -> None:
        assert combine([0.8, None, 0.8]) == combine([0.8, 0.8])
        assert combine([None, None]) is None

    def test_one_signal_alone_is_discounted(self) -> None:
        assert combine([1.0]) == 0.6

    def test_the_weakest_signal_limits_the_result(self) -> None:
        # a plain mean is 0.55; the weakest present signal allows at most +0.25
        assert combine([1.0, 0.1]) == pytest.approx(0.35)

    def test_contradiction_scales_the_result_down(self) -> None:
        assert combine([0.8, 0.8], contradiction=0.5) == pytest.approx(0.4)
        assert combine([0.8, 0.8], contradiction=1.0) == 0.0

    def test_the_result_stays_in_range(self) -> None:
        assert 0.0 <= combine([5.0, 5.0]) <= 1.0  # type: ignore[operator]

    def test_interval_arithmetic(self) -> None:
        assert overlap(0, 5, 3, 9) == 2 and overlap(0, 5, 5, 9) == 0
        assert gap(0, 5, 8, 9) == 3 and gap(0, 5, 3, 9) == 0
        assert coverage(2, 8) == 0.25 and coverage(1, 0) is None

    def test_correlation_needs_variation_and_enough_points(self) -> None:
        assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
        assert pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
        assert pearson([1, 1, 1], [1, 2, 3]) is None
        assert pearson([1, 2], [1, 2]) is None
        assert spread([1.0]) is None

    def test_scores_outside_their_range_are_refused(self) -> None:
        with pytest.raises(InvariantViolation):
            Scores(strength=1.2)


class TestProfile:
    def test_every_value_is_part_of_the_identity(self) -> None:
        base = AlignmentProfile().fingerprint()

        assert base != replace(AlignmentProfile(), speaking_spread=0.04).fingerprint()
        assert base != replace(AlignmentProfile(), stages=("speech",)).fingerprint()
        assert base == AlignmentProfile().fingerprint()

    def test_the_stage_order_does_not_change_the_identity(self) -> None:
        a = AlignmentProfile(stages=("speech", "text"))
        b = AlignmentProfile(stages=("text", "speech"))

        assert a.fingerprint() == b.fingerprint()

    @pytest.mark.parametrize(
        "bad",
        [
            {"stages": ("speech", "magic")},
            {"quiet_spread": 0.5},
            {"probe_hop_seconds": 0.0},
            {"speaking_spread": float("nan")},
            {"link_conflict_share": 0.9},
        ],
    )
    def test_nonsense_is_refused_before_any_work(self, bad: dict[str, Any]) -> None:
        with pytest.raises(InvalidAlignmentConfig):
            AlignmentProfile(**bad)

    def test_named_profiles(self) -> None:
        assert get_profile("fast") is PROFILES["fast"]
        with pytest.raises(InvalidAlignmentConfig):
            get_profile("nope")


def document() -> AlignmentDocument:
    return run(
        inputs(
            audio([segment("1", 10, 20, "S1", "battery pack")]),
            video([shot("a", 0, 60, text=("battery",))], talking(0, 60)),
            content_facts=content([("p1", 10.0, 20.0, "battery pack")]),
        )
    )


class TestStoredForm:
    def test_a_document_survives_storage_unchanged(self) -> None:
        doc = document()

        assert alignment_from_json(alignment_to_json(doc)) == doc

    def test_the_stored_text_is_deterministic_and_declares_its_kind(self) -> None:
        raw = json.loads(alignment_to_json(document()))

        assert alignment_to_json(document()) == alignment_to_json(document())
        assert raw["document_type"] == "audio_visual_alignment.document"
        assert raw["schema_version"] == SCHEMA_VERSION

    def test_the_reader_refuses_what_it_does_not_understand(self) -> None:
        good = json.loads(alignment_to_json(document()))
        wrong_schema = {**good, "schema_version": 99}
        wrong_kind = {**good, "document_type": "something.else"}
        extra = json.loads(alignment_to_json(document()))
        extra["payload"]["surprise"] = 1
        unknown_member = json.loads(alignment_to_json(document()))
        unknown_member["payload"]["speech_sources"][0]["visibility"] = "teleporting"

        for text in (
            "not json",
            json.dumps(wrong_schema),
            json.dumps(wrong_kind),
            json.dumps(extra),
            json.dumps(unknown_member),
        ):
            with pytest.raises(InvalidAlignmentDocument):
                alignment_from_json(text)

    def test_a_missing_required_field_is_refused(self) -> None:
        raw = json.loads(alignment_to_json(document()))
        del raw["payload"]["sync"]

        with pytest.raises(InvalidAlignmentDocument):
            alignment_from_json(json.dumps(raw))


class TestValidationAndDeterminism:
    def test_a_consistent_document_has_no_findings(self) -> None:
        assert validate_document(document()) == []

    def test_duplicate_ids_and_bad_intervals_are_reported(self) -> None:
        doc = document()
        broken = replace(
            doc,
            speech_sources=(doc.speech_sources[0], replace(doc.speech_sources[0], end=1.0)),
        )

        problems = validate_document(broken)

        assert any("duplicate id" in p for p in problems)
        assert any("invalid interval" in p for p in problems)

    def test_the_same_facts_give_the_same_document(self) -> None:
        assert document() == document()

    def test_ids_are_derived_from_what_they_name(self) -> None:
        doc = document()

        assert doc.speech_sources[0].source_id == "ss_1"
        assert doc.text_matches[0].match_id == "tm_p1_a"
        assert doc.matches_for("p1") == doc.text_matches
        assert doc.speech_source("1") is doc.speech_sources[0]

    def test_no_field_or_member_names_an_editing_action(self) -> None:
        import dataclasses
        from enum import Enum

        from media_house.modules.audio_visual_alignment.domain import model, values

        banned = ("cut_here", "use_this", "select", "recommend", "should", "replace_with", "insert")
        names: list[str] = []
        for module in (model, values):
            for obj in vars(module).values():
                if dataclasses.is_dataclass(obj):
                    names += [f.name for f in dataclasses.fields(obj)]
                elif isinstance(obj, type) and issubclass(obj, Enum) and obj is not Enum:
                    names += [m.value for m in obj]
        assert not [n for n in names if any(b in n for b in banned)]
