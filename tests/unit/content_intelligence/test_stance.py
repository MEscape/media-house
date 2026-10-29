"""Negation, doubt and supposition cues: footage must not prove what the narration denies."""

import pytest

from media_house.modules.content_intelligence.domain.stance import stance_of


class TestStance:
    @pytest.mark.parametrize(
        "text",
        [
            "The phone survived the drop.",
            "Sand the board until it is smooth.",
            "Das Gerät hat den Test bestanden.",
        ],
    )
    def test_plain_statements_assert(self, text: str) -> None:
        assert stance_of(text).asserts and stance_of(text).cues == ()

    @pytest.mark.parametrize(
        ("text", "cue"),
        [
            ("The phone did not survive the drop.", "not"),
            ("It never worked.", "never"),
            ("It doesn't scratch.", "doesn't"),
            ("Das Gerät hat keine Kratzer.", "keine"),
            ("Nothing broke.", "nothing"),
        ],
    )
    def test_denial_is_negated(self, text: str, cue: str) -> None:
        found = stance_of(text)

        assert found.negated and not found.asserts and cue in found.cues

    @pytest.mark.parametrize(
        "text",
        [
            "Imagine the phone surviving the drop.",
            "It would survive the drop.",
            "Supposedly it survives the drop.",
            "Stell dir vor, es überlebt den Sturz.",
        ],
    )
    def test_supposition_is_hypothetical(self, text: str) -> None:
        found = stance_of(text)

        assert found.hypothetical and not found.negated and not found.asserts

    def test_cues_come_in_reading_order(self) -> None:
        assert stance_of("It would not survive.").cues == ("would", "not")
