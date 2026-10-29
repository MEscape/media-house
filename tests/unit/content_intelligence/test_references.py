"""Named products and the phrases that point back at them ("this model", "the previous version")."""

import pytest

from media_house.modules.content_intelligence.domain.references import (
    Reference,
    ReferenceKind,
    ReferenceState,
    designations,
    resolve_in_texts,
)


def keys(text: str) -> list[str]:
    return [d.key for d in designations(text)]


def resolve(*sentences: str) -> tuple[Reference, ...]:
    return resolve_in_texts([(f"u{i}", s) for i, s in enumerate(sentences)])


class TestDesignations:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("The Pixel 9 is new.", ["pixel 9"]),
            ("I bought an iPhone 15 Pro last week.", ["iphone 15 pro"]),
            ("The Fujifilm X100V is small.", ["fujifilm x100v"]),
            ("Sony A7IV versus Canon R5", ["a7iv", "canon r5"]),
            ("The Pixel 9 replaces the Pixel 8.", ["pixel 9", "pixel 8"]),
        ],
    )
    def test_product_names_are_found_in_reading_order(self, text: str, expected: list[str]) -> None:
        assert keys(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "Step 3 is where it gets hard.",
            "It took around 5 minutes.",
            "Chapter 2 starts here.",
            "Export as MP4 or H264.",
            "There is nothing named here.",
        ],
    )
    def test_numbers_that_do_not_name_a_product_are_not_designations(self, text: str) -> None:
        assert designations(text) == ()

    def test_two_values_of_one_family_are_different_things(self) -> None:
        nine, eight = designations("Pixel 9 and Pixel 8")

        assert nine.family == eight.family and nine.key != eight.key

    def test_a_suffix_makes_a_different_designation(self) -> None:
        assert keys("the iPhone 15 and the iPhone 15 Pro") == ["iphone 15", "iphone 15 pro"]

    def test_a_name_repeated_is_one_designation(self) -> None:
        assert keys("The Pixel 9 ... the Pixel 9 again") == ["pixel 9"]


class TestPointingPhrases:
    def test_this_model_is_the_one_the_previous_statement_names(self) -> None:
        refs = resolve("The Pixel 9 is the newest phone.", "This model has a brighter display.")

        (ref,) = refs
        assert ref.state is ReferenceState.RESOLVED
        assert ref.designation is not None and ref.designation.key == "pixel 9"
        assert ref.antecedent_unit_id == "u0" and ref.kind is ReferenceKind.POINTS_BACK

    def test_a_name_earlier_in_the_same_statement_counts(self) -> None:
        (ref,) = resolve("The Pixel 9 is quick and this phone is also light.")

        assert ref.state is ReferenceState.RESOLVED and ref.antecedent_unit_id == "u0"

    def test_several_names_in_the_nearest_statement_stay_ambiguous(self) -> None:
        refs = resolve("The Pixel 9 replaces the Pixel 8.", "This model has a brighter display.")

        (ref,) = refs
        assert ref.state is ReferenceState.AMBIGUOUS and ref.designation is None
        assert {c.key for c in ref.candidates} == {"pixel 9", "pixel 8"}

    def test_a_nearer_unambiguous_statement_wins_over_an_older_ambiguous_one(self) -> None:
        refs = resolve(
            "The Pixel 9 replaces the Pixel 8.",
            "Let us look at the Pixel 9 first.",
            "This device feels solid.",
        )

        (ref,) = refs
        assert ref.state is ReferenceState.RESOLVED and ref.antecedent_unit_id == "u1"

    def test_nothing_named_nearby_is_unresolved_not_guessed(self) -> None:
        refs = resolve("Welcome back.", "This model has a brighter display.")

        assert refs[0].state is ReferenceState.UNRESOLVED and refs[0].designation is None

    def test_a_name_beyond_the_window_is_not_used(self) -> None:
        filler = [f"Sentence number {n} talks about nothing." for n in range(8)]
        refs = resolve("The Pixel 9 is new.", *filler, "This phone is good.")

        assert refs[0].state is ReferenceState.UNRESOLVED

    def test_a_process_has_no_name_to_resolve_to(self) -> None:
        refs = resolve("First you sand the board.", "The same process works on the shelf.")

        assert refs[0].state is ReferenceState.UNRESOLVED
        assert "activity" in refs[0].reason

    def test_the_previous_version_is_named_only_when_the_context_names_it(self) -> None:
        refs = resolve(
            "The Pixel 9 replaces the Pixel 8.", "The previous version had a dimmer screen."
        )

        (ref,) = refs
        assert ref.kind is ReferenceKind.PREVIOUS and ref.state is ReferenceState.RESOLVED
        assert ref.designation is not None and ref.designation.key == "pixel 8"

    def test_the_previous_version_is_not_inferred_from_a_number(self) -> None:
        refs = resolve(
            "The Pixel 9 is the newest phone.", "The previous version had a dimmer screen."
        )

        (ref,) = refs
        assert ref.state is ReferenceState.UNRESOLVED and ref.designation is None
        assert "no older" in ref.reason

    def test_last_years_model_is_a_previous_version_phrase(self) -> None:
        refs = resolve("The Pixel 9 replaces the Pixel 8.", "It beats last year's model easily.")

        assert refs[0].kind is ReferenceKind.PREVIOUS

    def test_german_phrases_resolve_too(self) -> None:
        refs = resolve("Das Pixel 9 ist neu.", "Dieses Modell hat ein helleres Display.")

        assert refs[0].state is ReferenceState.RESOLVED

    def test_a_statement_filter_limits_the_answer(self) -> None:
        statements = [
            ("a", "The Pixel 9 is new."),
            ("b", "This model is fast."),
            ("c", "That phone is light."),
        ]

        refs = resolve_in_texts(statements, {"c"})

        assert [r.unit_id for r in refs] == ["c"]
