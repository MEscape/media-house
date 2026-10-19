"""Scores mean different things, say what they rest on, and never turn into decisions."""

from dataclasses import replace

import pytest

from media_house.modules.content_intelligence.domain.config import (
    ContentConfig,
    Profile,
    Thresholds,
)
from media_house.modules.content_intelligence.domain.context import (
    AudioCues,
    VisualContext,
    VisualShot,
)
from media_house.modules.content_intelligence.domain.model import ContentAnalysis, SemanticSegment
from media_house.modules.content_intelligence.domain.query import (
    evidence_for,
    highlights,
    opportunities,
    ranked,
    removal_candidates,
    segments_between,
    standalone_candidates,
)
from media_house.modules.content_intelligence.domain.validation import validate_analysis
from media_house.modules.content_intelligence.domain.values import (
    GraphicKind,
    OpportunityKind,
    Role,
    ScoreName,
)
from tests.support.ci_texts import BATTERIES, BUDGET, GARDEN, analysis_of, lines_to_transcript

#: Segments of every transcript below are forced to be short so each sentence group stands alone.
EAGER = ContentConfig(
    profile=Profile.FAST,
    thresholds=Thresholds(min_segment_units=2, leaf_depth_sigma=-10.0),
)

COFFEE = (
    "Brewing coffee starts with fresh beans and clean water.",
    "The coffee grind size decides how fast the water flows through the coffee.",
    "A finer coffee grind slows the water and makes stronger coffee.",
    "The most important thing in coffee brewing is the water temperature.",
    "Coffee brewing with hot water extracts the flavour from the grind.",
)
TANGENT = (
    "Quantum chromodynamics describes gluon confinement inside hadrons.",
    "Asymptotic freedom weakens the colour coupling at short distances.",
    "Lattice simulations estimate the string tension numerically.",
    "Chiral symmetry breaking generates most of the visible hadron mass.",
)
#: A tangent that opens by pointing back ("That is why ...") although it changes the subject.
POINTING = (
    "That is why gluon confinement inside hadrons matters.",
    *TANGENT[1:],
)
FILLER = ("Um yeah okay.", "Uh you know like.", "Yeah yeah okay then.", "Um okay so yeah.")


def segment_starting(a: ContentAnalysis, prefix: str) -> SemanticSegment:
    for s in a.segments:
        unit = a.unit(s.unit_ids[0])
        if unit and unit.text.startswith(prefix):
            return s
    firsts = [u.text for s in a.segments if (u := a.unit(s.unit_ids[0]))]
    raise AssertionError(f"no segment starts with {prefix!r}: {firsts}")


def value(s: SemanticSegment, name: ScoreName) -> float:
    v = s.value(name)
    assert v is not None, f"{name.value} is missing"
    return v


class TestScoreSemantics:
    def analysed(self) -> ContentAnalysis:
        return analysis_of(
            lines_to_transcript(
                (*COFFEE, *TANGENT, *COFFEE, *FILLER), pauses={5: 2.5, 9: 2.5, 14: 2.5}
            ),
            EAGER,
        )

    def test_every_score_is_in_range_has_a_reason_and_never_zero_for_missing(self) -> None:
        a = analysis_of(lines_to_transcript((*BATTERIES, *GARDEN, *BUDGET)), EAGER)

        validate_analysis(a)
        for s in a.segments:
            for score in s.scores:
                assert score.value is None or 0.0 <= score.value <= 1.0
                assert score.value is not None or score.reason

    def test_a_dense_tangent_is_dense_but_not_important(self) -> None:
        a = self.analysed()
        tangent = segment_starting(a, "Quantum chromodynamics")
        main = segment_starting(a, "Brewing coffee")
        filler = segment_starting(a, "Um yeah")

        assert value(tangent, ScoreName.INFORMATION_DENSITY) > value(
            filler, ScoreName.INFORMATION_DENSITY
        )
        assert value(tangent, ScoreName.IMPORTANCE) < value(main, ScoreName.IMPORTANCE)

    def test_importance_ignores_how_the_words_were_delivered(self) -> None:
        source = lines_to_transcript((*COFFEE, *TANGENT, *COFFEE), pauses={5: 2.5, 9: 2.5})
        loud = AudioCues(
            asset_id="a",
            fingerprint="f",
            emphasis=tuple((i * 0.4, i * 0.4 + 0.3, 1.0) for i in range(200)),
        )
        plain = analysis_of(source, EAGER)
        emphatic = analysis_of(source, EAGER, audio=loud)

        for before, after in zip(plain.segments, emphatic.segments, strict=True):
            assert value(before, ScoreName.IMPORTANCE) == value(after, ScoreName.IMPORTANCE)

    def test_delivery_adds_at_most_a_small_corroboration_to_a_highlight(self) -> None:
        source = lines_to_transcript((*COFFEE, *TANGENT, *COFFEE), pauses={5: 2.5, 9: 2.5})
        loud = AudioCues(
            asset_id="a",
            fingerprint="f",
            emphasis=tuple((i * 0.4, i * 0.4 + 0.3, 1.0) for i in range(200)),
            laughter=((0.0, 90.0),),
        )
        plain = analysis_of(source, EAGER)
        emphatic = analysis_of(source, EAGER, audio=loud)

        for before, after in zip(plain.segments, emphatic.segments, strict=True):
            assert (
                0 <= value(after, ScoreName.HIGHLIGHT) - value(before, ScoreName.HIGHLIGHT) <= 0.081
            )

    def test_a_missing_input_gives_none_with_a_reason_not_zero(self) -> None:
        a = analysis_of(lines_to_transcript(["Ok."]))

        density = a.segments[0].score(ScoreName.INFORMATION_DENSITY)

        assert density is not None and density.value is None
        assert "too short" in density.reason

    def test_uniqueness_drops_for_a_passage_said_before(self) -> None:
        a = analysis_of(
            lines_to_transcript((*COFFEE, *TANGENT, *COFFEE), pauses={5: 2.5, 9: 2.5}), EAGER
        )

        first, again = a.segments[0], a.segments[-1]

        assert value(again, ScoreName.UNIQUENESS) < value(a.segments[1], ScoreName.UNIQUENESS)
        assert value(first, ScoreName.UNIQUENESS) < 1.0
        assert "this recording only" in first.score(ScoreName.UNIQUENESS).evidence[0].detail  # type: ignore[union-attr]


class TestStandalone:
    def test_a_passage_that_opens_by_pointing_back_names_what_it_needs(self) -> None:
        a = analysis_of(lines_to_transcript((*COFFEE, *POINTING), pauses={5: 2.5}), EAGER)

        score = segment_starting(a, "That is why").score(ScoreName.STANDALONE)

        assert score is not None and score.value is not None and score.value < 1.0
        assert any(e.kind == "opens_with_reference" for e in score.evidence)

    def test_a_self_contained_passage_scores_higher_than_a_dependent_one(self) -> None:
        a = analysis_of(lines_to_transcript((*COFFEE, *POINTING), pauses={5: 2.5}), EAGER)
        dependent = segment_starting(a, "That is why")

        assert value(dependent, ScoreName.STANDALONE) < value(a.segments[0], ScoreName.STANDALONE)

    def test_a_short_duration_is_not_taken_for_standalone_suitability(self) -> None:
        a = analysis_of(lines_to_transcript(("Yes it is.", "No.")))

        assert value(a.segments[0], ScoreName.STANDALONE) < 0.8


class TestAdvisoryOnly:
    def test_a_pure_repeat_scores_higher_for_removal_than_one_that_adds_evidence(self) -> None:
        def build(repeat: str) -> ContentAnalysis:
            lines = [*COFFEE, *TANGENT, repeat, "Coffee brewing needs hot water and a fine grind."]
            return analysis_of(lines_to_transcript(lines, pauses={5: 2.5, 9: 2.5}), EAGER)

        pure = build(COFFEE[3])
        richer = build(COFFEE[3] + " Studies measured 92 degrees as ideal for brewing.")
        last_pure, last_richer = pure.segments[-2], richer.segments[-2]

        assert value(last_pure, ScoreName.REMOVAL_CANDIDATE) >= value(
            last_richer, ScoreName.REMOVAL_CANDIDATE
        )

    def test_removal_candidates_carry_the_reasons_to_keep_them(self) -> None:
        a = analysis_of(
            lines_to_transcript(
                (*COFFEE, *TANGENT, *COFFEE, "In short, coffee needs hot water."),
                pauses={5: 2.5, 9: 2.5, 14: 2.5},
            ),
            EAGER,
        )

        for s in a.segments:
            score = s.score(ScoreName.REMOVAL_CANDIDATE)
            assert score is not None and "unprotected" in score.components
            assert score.components["protection"] >= 0

        recap = [s for s in a.segments if any(r.role is Role.RECAP for r in s.roles)]
        assert all(s.score(ScoreName.REMOVAL_CANDIDATE).counter_evidence for s in recap)  # type: ignore[union-attr]

    def test_pauses_and_hesitation_never_raise_a_removal_score(self) -> None:
        quick = analysis_of(lines_to_transcript((*COFFEE, *TANGENT), pauses={5: 0.3}), EAGER)
        slow = analysis_of(
            lines_to_transcript((*COFFEE, *TANGENT), pauses={5: 12.0}, seconds_per_word=1.2), EAGER
        )

        for a, b in zip(quick.segments, slow.segments, strict=True):
            assert value(a, ScoreName.REMOVAL_CANDIDATE) == pytest.approx(
                value(b, ScoreName.REMOVAL_CANDIDATE), abs=1e-6
            )

    def test_nothing_in_the_result_is_an_edit_instruction(self) -> None:
        a = analysis_of(lines_to_transcript((*BATTERIES, *GARDEN, *BUDGET)), EAGER)
        fields = {f for s in a.segments for f in s.__dataclass_fields__}

        assert not {"cut", "delete", "remove", "keep", "action", "decision"} & fields

    def test_a_highlight_names_the_context_it_needs(self) -> None:
        a = analysis_of(lines_to_transcript((*COFFEE, *POINTING), pauses={5: 2.5}), EAGER)
        dependent = segment_starting(a, "That is why")

        highlight = dependent.score(ScoreName.HIGHLIGHT)

        assert highlight is not None and highlight.counter_evidence


class TestQueries:
    def test_queries_read_the_stored_result_in_a_stable_order(self) -> None:
        a = analysis_of(
            lines_to_transcript((*COFFEE, *TANGENT, *COFFEE), pauses={5: 2.5, 9: 2.5}), EAGER
        )

        top = ranked(a, ScoreName.IMPORTANCE)

        assert [value(s, ScoreName.IMPORTANCE) for s in top] == sorted(
            (value(s, ScoreName.IMPORTANCE) for s in top), reverse=True
        )
        assert highlights(a, 0.0) and standalone_candidates(a, 0.0) and removal_candidates(a, 0.0)
        first = a.segments[0]
        window = segments_between(a, first.span.start or 0.0, first.span.end or 0.0)
        assert first in window

    def test_evidence_for_a_segment_gathers_text_items_and_context(self) -> None:
        a = analysis_of(lines_to_transcript((*COFFEE, *POINTING), pauses={5: 2.5}), EAGER)
        dependent = segment_starting(a, "That is why")

        bundle = evidence_for(a, dependent.segment_id)

        assert bundle is not None and bundle.text.startswith("That is why")
        assert bundle.context and bundle.context[0][1] == COFFEE[-1]
        assert evidence_for(a, "missing") is None


class TestOpportunities:
    def lines(self) -> list[str]:
        return [
            "Welcome to the show.",
            "The yearly revenue grew by 40 percent and reached 3.5 million euros.",
            "In the city of Hamburg the old harbor is located near the station.",
            "First you boil the water, then you add the beans, after that you stir for a minute.",
            "This device is smaller than the old one and faster than the previous model.",
            "Subscribe to the channel and click the link below.",
        ]

    def test_graphics_quote_the_transcript_and_never_invent_figures(self) -> None:
        a = analysis_of(lines_to_transcript(self.lines()))

        graphics = opportunities(a, OpportunityKind.GRAPHIC)
        kinds = {g.category for g in graphics}
        number = next(g for g in graphics if g.category == GraphicKind.KEY_NUMBER.value)

        validate_analysis(a)
        assert {GraphicKind.KEY_NUMBER.value, GraphicKind.CALL_TO_ACTION.value} <= kinds
        assert "40 percent" in number.concept and number.limitations

    def test_broll_ideas_are_semantic_and_claim_no_footage(self) -> None:
        a = analysis_of(lines_to_transcript(self.lines()))

        broll = opportunities(a, OpportunityKind.BROLL)

        assert broll
        for o in broll:
            assert any("no suitable footage" in lim for lim in o.limitations)
            assert not o.already_covered and not o.visual_evidence

    def numbers(self, screen_text: tuple[str, ...]) -> tuple[ContentAnalysis, list[str]]:
        shots = (VisualShot(shot_id="sh1", start=0.0, end=60.0, screen_text=screen_text),)
        visual = VisualContext(asset_id="v", fingerprint="f", duration=60.0, shots=shots)
        a = analysis_of(lines_to_transcript(self.lines()), visual=visual)
        graphics = opportunities(a, OpportunityKind.GRAPHIC)
        return a, [g.concept for g in graphics if g.category == GraphicKind.KEY_NUMBER.value]

    def test_text_already_on_screen_suppresses_the_graphic(self) -> None:
        a, concepts = self.numbers(("Revenue +40 percent", "EUR 3.5 million"))

        assert concepts == []
        assert any(o.kind == "graphics_already_on_screen" for o in a.global_observations)

    def test_only_the_figures_not_yet_on_screen_are_suggested(self) -> None:
        _, concepts = self.numbers(("Revenue +40 percent",))

        assert concepts == ["3.5 million"]

    def test_a_shot_that_already_shows_the_subject_marks_the_broll_as_covered(self) -> None:
        source = lines_to_transcript(
            [
                "Welcome to the show.",
                "In the city of Hamburg the old harbor is located near the station.",
                "Thanks for watching today.",
            ]
        )
        shots = (
            VisualShot(
                shot_id="sh1",
                start=0.0,
                end=80.0,
                description="old harbor with boats near the station",
                objects=("harbor", "boats"),
            ),
        )
        visual = VisualContext(asset_id="v", fingerprint="f", duration=80.0, shots=shots)

        a = analysis_of(source, visual=visual)

        covered = [o for s in a.segments for o in s.broll if o.already_covered]
        assert covered and all(o.visual_evidence == ("sh1",) for o in covered)
        assert covered[0] not in opportunities(a, OpportunityKind.BROLL)
        assert covered[0] in opportunities(a, OpportunityKind.BROLL, include_covered=True)

    def test_without_visual_analysis_the_limit_is_stated(self) -> None:
        a = analysis_of(lines_to_transcript(self.lines()))

        assert "No visual analysis was used." in a.limitations
        assert replace(a, segments=a.segments) == a
