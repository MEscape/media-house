"""Visual rhythm, the pulse in the sound, demonstrations and narrative pacing."""

import pytest

from media_house.modules.pacing_intelligence.domain.facts import AssetEvidence, StoryFacts
from media_house.modules.pacing_intelligence.domain.profile import PacingConfig
from media_house.modules.pacing_intelligence.domain.values import (
    Level,
    Precision,
    ReasonCode,
    RecommendationType,
    Safety,
)
from tests.support.pace_fakes import Sec, T, analyse, pulse, shots, speak, tell

LINE = "this phone has a great screen and a really long lasting battery today"


def talk(count: int = 4, gap: float = 0.4) -> list[T]:
    return [T(LINE, gap=gap) for _ in range(count)]


# --- shots ----------------------------------------------------------------------------------
def test_a_long_static_shot_under_speech_is_reported_with_its_measurement() -> None:
    evidence = shots(speak(talk()), [(0.0, 12.0), (12.0, 14.0)], static=0.95)

    result = analyse(evidence)

    (rec,) = result.recommendations_of(RecommendationType.ADD_VISUAL_CHANGE)
    assert rec.level is Level.SHOT and ReasonCode.STATIC_PICTURE in rec.reason_codes
    assert rec.target.precision is Precision.FRAME_EXACT
    assert (rec.target.start_frame, rec.target.end_frame) == (0, 300)
    assert rec.target.frame_rate is not None and rec.evidence[0].value == pytest.approx(0.05)
    assert any("long static shot can be right" in limit for limit in rec.confidence_limits)


def test_the_same_shot_is_normal_for_a_talking_head_and_long_for_a_generic_video() -> None:
    evidence = shots(speak(talk()), [(0.0, 12.0), (12.0, 14.0)], static=0.95)

    generic = analyse(evidence)
    head = analyse(evidence, PacingConfig(profile="talking_head"))

    assert generic.recommendations_of(RecommendationType.ADD_VISUAL_CHANGE)
    assert head.recommendations_of(RecommendationType.ADD_VISUAL_CHANGE) == ()


def test_a_shot_with_motion_or_without_a_measurement_is_never_called_static() -> None:
    moving = analyse(shots(speak(talk()), [(0.0, 14.0)], static=0.2))
    unknown = analyse(shots(speak(talk()), [(0.0, 14.0)], static=None))

    for result in (moving, unknown):
        assert result.recommendations_of(RecommendationType.ADD_VISUAL_CHANGE) == ()
    assert unknown.shots[0].visual_activity.value is None
    assert "no motion measurement" in unknown.shots[0].visual_activity.note


def test_frequent_cuts_are_judged_against_the_profile_not_a_universal_number() -> None:
    cuts = [(float(i * 3), float(i * 3 + 3)) for i in range(7)]
    evidence = shots(speak(talk(count=3, gap=0.2)), cuts, static=0.3)

    generic = analyse(evidence)
    montage = analyse(evidence, PacingConfig(profile="cinematic_montage"))

    (rec,) = generic.recommendations_of(RecommendationType.HOLD_SHOT)
    assert ReasonCode.FREQUENT_CUTS in rec.reason_codes and rec.safety is Safety.NEEDS_REVIEW
    assert montage.recommendations_of(RecommendationType.HOLD_SHOT) == ()


def test_shot_length_is_described_relative_to_its_neighbours() -> None:
    evidence = shots(speak(talk()), [(0.0, 2.0), (2.0, 4.0), (4.0, 12.0), (12.0, 14.0)], static=0.3)

    result = analyse(evidence)

    by_id = {s.shot_id: s for s in result.shots}
    assert by_id["shot_2"].relative_duration == pytest.approx(4.0)
    assert by_id["shot_0"].relative_duration == pytest.approx(1.0)
    assert result.timeline[0].shot_duration_variation is not None


# --- the pulse in the sound and the cuts ----------------------------------------------------
def montage_evidence(cut_times: list[float]) -> AssetEvidence:
    base = speak([T(LINE)], tail=20.0)
    cuts = [(a, b) for a, b in zip([0.0, *cut_times], [*cut_times, 24.0], strict=True)]
    return pulse(shots(base, cuts, static=0.2), 6.0, 20.0, bpm=120.0)


def test_cuts_that_ignore_the_pulse_get_a_cautious_recommendation_in_a_montage() -> None:
    off = montage_evidence([6.2, 7.3, 8.4, 9.55, 10.7])

    result = analyse(off, PacingConfig(profile="cinematic_montage"))

    (rec,) = result.recommendations_of(RecommendationType.ALIGN_CUTS_TO_BEATS)
    assert ReasonCode.CUTS_OFF_PULSE in rec.reason_codes
    assert rec.confidence is not None and rec.confidence <= 0.6
    assert any("not from music analysis" in limit for limit in rec.confidence_limits)
    assert result.rhythm[0].cuts_on_beat is not None and result.rhythm[0].cuts_on_beat < 0.6


def test_cuts_on_the_pulse_need_no_recommendation_and_other_formats_never_get_one() -> None:
    on = montage_evidence([6.5, 7.0, 7.5, 8.0, 8.5])
    off = montage_evidence([6.2, 7.3, 8.4, 9.55, 10.7])

    on_result = analyse(on, PacingConfig(profile="cinematic_montage"))
    other = analyse(off, PacingConfig(profile="talking_head"))

    assert on_result.recommendations_of(RecommendationType.ALIGN_CUTS_TO_BEATS) == ()
    assert on_result.rhythm[0].cuts_on_beat == 1.0
    assert other.recommendations_of(RecommendationType.ALIGN_CUTS_TO_BEATS) == ()
    assert other.rhythm  # the pulse is still reported


def test_a_weak_pulse_is_not_reported_at_all() -> None:
    evidence = pulse(
        shots(speak([T(LINE)], tail=20.0), [(0.0, 24.0)]), 6.0, 20.0, 120.0, strength=0.1
    )

    assert analyse(evidence, PacingConfig(profile="cinematic_montage")).rhythm == ()


# --- demonstrations -------------------------------------------------------------------------
def tutorial_evidence(cut_after: float) -> AssetEvidence:
    base = speak(
        [T("now watch what happens when I press this button on the device", gap=0.5), *talk(2)],
        tail=8,
    )
    return shots(base, [(0.0, cut_after), (cut_after, 14.0)], static=0.5)


def test_a_demonstration_cut_away_too_soon_is_asked_to_stay_longer() -> None:
    evidence = tutorial_evidence(cut_after=2.5)
    story = tell(
        evidence,
        [Sec("demo", (0, 1, 2), "essential")],
        fmt="tutorial",
        visuals=[(0, "demonstration", "essential", "the button")],
    )

    result = analyse(evidence, PacingConfig(profile="tutorial"), story=story)

    (rec,) = result.recommendations_of(RecommendationType.HOLD_DEMONSTRATION)
    assert ReasonCode.DEMONSTRATION_TOO_SHORT in rec.reason_codes and rec.safety is Safety.EXPANDS
    assert rec.delta_seconds == pytest.approx(
        6.0 - 2.0, abs=0.01
    )  # wants 6 s, 2.0 s remain after 0.5 s
    assert rec.evidence[0].ref == "vis0"


def test_a_demonstration_that_stays_long_enough_or_a_story_without_one_needs_nothing() -> None:
    evidence = tutorial_evidence(cut_after=9.0)
    story = tell(
        evidence,
        [Sec("demo", (0, 1, 2), "essential")],
        visuals=[(0, "demonstration", "essential", "the button")],
    )

    assert (
        analyse(evidence, PacingConfig(profile="tutorial"), story=story).recommendations_of(
            RecommendationType.HOLD_DEMONSTRATION
        )
        == ()
    )
    assert (
        analyse(evidence, PacingConfig(profile="tutorial")).recommendations_of(
            RecommendationType.HOLD_DEMONSTRATION
        )
        == ()
    )


# --- narrative ------------------------------------------------------------------------------
def narrative_story(setup_seconds: float) -> tuple[AssetEvidence, StoryFacts]:
    evidence = speak([T(LINE) for _ in range(5)], tail=1)
    story = tell(
        evidence,
        [
            Sec("intro", (0, 1), "supporting", ("setup",), seconds=setup_seconds),
            Sec("point", (2,), "essential", ("claim",), seconds=20.0),
            Sec("reveal", (3,), "essential", ("payoff",), seconds=15.0),
            Sec("outro", (4,), "supporting", ("context",), seconds=10.0),
        ],
        fmt="review",
    )
    return evidence, story


def test_a_point_that_arrives_late_after_too_much_setup_is_flagged() -> None:
    evidence, story = narrative_story(setup_seconds=40.0)

    result = analyse(evidence, story=story)  # product_review: 30 s deadline, setup share 35%

    section = result.section("sec1")
    assert section is not None and section.first_main_point_at == pytest.approx(40.0)
    assert ReasonCode.PREMISE_LATE in section.reason_codes
    assert result.recommendations_of(RecommendationType.ESTABLISH_PREMISE_EARLIER)
    (setup,) = result.recommendations_of(RecommendationType.REDUCE_SETUP)
    assert (setup.delta_seconds or 0) < 0 and setup.section_id == "sec0"


def test_a_prompt_point_raises_no_narrative_finding() -> None:
    evidence, story = narrative_story(setup_seconds=8.0)

    result = analyse(evidence, story=story)

    assert result.recommendations_of(RecommendationType.ESTABLISH_PREMISE_EARLIER) == ()
    assert result.recommendations_of(RecommendationType.REDUCE_SETUP) == ()
    assert result.section("sec1").first_main_point_at == pytest.approx(8.0)  # type: ignore[union-attr]


def test_a_reveal_followed_at_once_by_more_speech_is_given_room() -> None:
    evidence = speak(
        [
            T(LINE),
            T("and that is the surprising result of the whole test", gap=0.1),
            T(LINE, gap=0.1),
        ]
    )
    story = tell(
        evidence,
        [
            Sec("body", (0,), "essential"),
            Sec("reveal", (1,), "essential", ("payoff",)),
            Sec("after", (2,), "supporting"),
        ],
    )

    result = analyse(evidence, PacingConfig(profile="documentary"), story=story)

    (rec,) = result.recommendations_of(RecommendationType.HOLD_REVEAL)
    assert ReasonCode.REVEAL_RUSHED in rec.reason_codes and rec.safety is Safety.EXPANDS
    assert rec.delta_seconds == pytest.approx(1.8 - 0.1, abs=0.05)
    breaths = result.recommendations_of(RecommendationType.ADD_SECTION_BREATH)
    assert {r.section_id for r in breaths} == {"sec1", "sec2"}


def test_without_a_story_plan_narrative_analysis_is_unavailable_and_never_invented() -> None:
    result = analyse(speak(talk()))

    assert result.sections == () and result.story is None
    assert any(i.kind.value == "narrative_limited" for i in result.issues)
    stage = next(s for s in result.stages if s.stage == "narrative")
    assert stage.state.value == "not_available"
    narrative = {
        RecommendationType.ESTABLISH_PREMISE_EARLIER,
        RecommendationType.REDUCE_SETUP,
        RecommendationType.HOLD_REVEAL,
        RecommendationType.ADD_SECTION_BREATH,
    }
    assert not narrative & {r.type for r in result.recommendations}


# --- attention ------------------------------------------------------------------------------
def test_a_long_stretch_with_nothing_new_is_a_labelled_prediction() -> None:
    evidence = shots(
        speak(talk(count=6)), [(0.0, 40.0)], static=0.5
    )  # moving enough to skip the static rule

    result = analyse(evidence, PacingConfig(profile="shorts_reels"))

    recs = result.recommendations_of(RecommendationType.RESTORE_ATTENTION)
    assert recs and all(r.confidence is not None and r.confidence <= 0.4 for r in recs)
    assert all("not audience data" in r.observed for r in recs)
    bins = result.timeline[0].attention
    assert bins and all(0.0 <= b.estimate <= 1.0 for b in bins)
    assert any(b.estimate == 0.0 for b in bins)
