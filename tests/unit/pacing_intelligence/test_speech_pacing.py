"""Speech pacing: rates, pauses, density and load, repetition. Each case states its situation."""

import pytest

from media_house.modules.pacing_intelligence.domain.model import PacingAnalysis
from media_house.modules.pacing_intelligence.domain.profile import PacingConfig
from media_house.modules.pacing_intelligence.domain.values import (
    Basis,
    PaceClass,
    PauseVerdict,
    ReasonCode,
    RecommendationType,
    Safety,
)
from tests.support.pace_fakes import T, analyse, speak

TALKING_HEAD = PacingConfig(profile="talking_head")
SENTENCE = "this phone has a great screen and a really long lasting battery"


# --- pauses ---------------------------------------------------------------------------------
def test_dead_air_in_a_talking_head_is_tightened_and_a_natural_gap_is_left_alone() -> None:
    result = analyse(
        speak([T(SENTENCE), T(SENTENCE, gap=0.4), T(SENTENCE, gap=2.2), T(SENTENCE, gap=0.3)]),
        TALKING_HEAD,
    )

    verdicts = [p.verdict for p in result.pauses]
    assert verdicts == [PauseVerdict.NATURAL, PauseVerdict.DEAD_AIR, PauseVerdict.NATURAL]
    dead = result.pauses_of(PauseVerdict.DEAD_AIR)[0]
    assert dead.recommended_keep == pytest.approx(0.3)
    (tighten,) = result.recommendations_of(RecommendationType.TIGHTEN_PAUSE)
    assert tighten.delta_seconds == pytest.approx(-(2.2 - 0.3))
    assert tighten.safety is Safety.SAFE and ReasonCode.DEAD_AIR in tighten.reason_codes
    assert tighten.evidence and tighten.evidence[0].value == 0.97
    assert tighten.confidence is not None and tighten.confidence > 0.8


def test_a_pause_before_an_emphasised_word_is_meaningful_and_protected() -> None:
    result = analyse(speak([T(SENTENCE), T(SENTENCE, gap=2.0, emphasis=0.9)]), TALKING_HEAD)

    (pause,) = result.pauses
    assert pause.verdict is PauseVerdict.MEANINGFUL
    assert ReasonCode.PAUSE_BEFORE_EMPHASIS in pause.reason_codes and pause.recommended_keep is None
    assert result.recommendations_of(RecommendationType.TIGHTEN_PAUSE) == ()
    (keep,) = result.recommendations_of(RecommendationType.PRESERVE_PAUSE)
    assert keep.delta_seconds == 0.0 and keep.safety is Safety.SAFE


def test_a_pause_after_a_question_and_a_pause_with_a_reaction_are_meaningful() -> None:
    result = analyse(
        speak(
            [
                T("why does the battery last so long", question=True),
                T(SENTENCE, gap=1.6),
                T(SENTENCE, gap=2.0, event="laughter"),
            ]
        ),
        TALKING_HEAD,
    )

    first, second = result.pauses
    assert ReasonCode.PAUSE_AFTER_QUESTION in first.reason_codes
    assert ReasonCode.PAUSE_WITH_REACTION in second.reason_codes
    assert all(p.verdict is PauseVerdict.MEANINGFUL for p in result.pauses)
    assert result.recommendations_of(RecommendationType.TIGHTEN_PAUSE) == ()


def test_the_same_silence_is_dead_air_for_one_profile_and_natural_for_another() -> None:
    evidence = speak([T(SENTENCE), T(SENTENCE, gap=1.25)])

    talking = analyse(evidence, TALKING_HEAD)
    documentary = analyse(evidence, PacingConfig(profile="documentary"))

    assert talking.pauses[0].verdict is PauseVerdict.DEAD_AIR
    assert documentary.pauses[0].verdict is PauseVerdict.NATURAL


def test_a_long_pause_that_is_not_confirmed_silent_is_long_not_dead_air() -> None:
    evidence = speak([T(SENTENCE), T(SENTENCE, gap=2.5)], silence=0.3)

    result = analyse(evidence, TALKING_HEAD)

    (pause,) = result.pauses
    assert pause.verdict is PauseVerdict.LONG and ReasonCode.LONG_PAUSE in pause.reason_codes
    (rec,) = result.recommendations_of(RecommendationType.TIGHTEN_PAUSE)
    assert rec.safety is Safety.NEEDS_REVIEW


def test_a_gap_between_speakers_is_a_turn_not_a_hesitation() -> None:
    result = analyse(
        speak([T(SENTENCE, speaker="A"), T(SENTENCE, gap=1.0, speaker="B")]),
        PacingConfig(profile="interview_podcast"),
    )

    (pause,) = result.pauses
    assert (
        pause.verdict is PauseVerdict.NATURAL and ReasonCode.SPEAKER_TURN_GAP in pause.reason_codes
    )


# --- rate: speech-active versus elapsed --------------------------------------------------------
def test_speech_rate_leaves_the_pauses_out_and_elapsed_rate_keeps_them() -> None:
    evidence = speak([T("one two three four five six seven eight nine ten", inner=((4, 2.0),))])

    (passage,) = analyse(evidence).passages

    assert passage.words == 10
    assert passage.pause_seconds == pytest.approx(2.0)
    assert passage.speech_rate_wpm == pytest.approx(200.0, rel=0.02)  # 10 words in 3 s of speech
    assert passage.elapsed_rate_wpm == pytest.approx(10 / 5.0 * 60, rel=0.02)  # 10 words in 5 s
    assert passage.pause_ratio == pytest.approx(0.4, abs=0.01)
    assert passage.longest_pause == pytest.approx(2.0)


def test_deliberately_slow_delivery_is_not_penalised_when_the_content_is_dense() -> None:
    slow_dense = T(" ".join(["token"] * 12), seconds_per_word=0.6, density=0.85, requires=2)
    result = analyse(speak([slow_dense]), PacingConfig(profile="tutorial"))

    (passage,) = result.passages
    assert passage.pace is PaceClass.SLOW and ReasonCode.SLOW_DELIVERY in passage.reason_codes
    assert result.recommendations_of(RecommendationType.TIGHTEN_DELIVERY) == ()


def test_slow_and_empty_delivery_is_tightened_as_a_suggestion() -> None:
    result = analyse(speak([T(" ".join(["token"] * 10), seconds_per_word=0.9, density=0.1)]))

    (rec,) = result.recommendations_of(RecommendationType.TIGHTEN_DELIVERY)
    assert rec.safety is Safety.NEEDS_REVIEW and (rec.delta_seconds or 0) < -1.0
    assert {ReasonCode.SLOW_DELIVERY, ReasonCode.LOW_DENSITY} <= set(rec.reason_codes)
    assert rec.speed_up_score.value is not None and rec.cut_tightening_score.value is not None


def extra_time(analysis: PacingAnalysis) -> float:
    return sum(
        r.delta_seconds or 0
        for r in analysis.recommendations_of(RecommendationType.ALLOW_PROCESSING_TIME)
    )


# --- density is not speed -------------------------------------------------------------------
def long(density: float | None, **kw: object) -> T:
    return T(" ".join(["token"] * 22), density=density, **kw)  # type: ignore[arg-type]


def test_fast_dense_speech_is_given_time_and_fast_simple_speech_is_not() -> None:
    dense = analyse(speak([long(0.85, requires=2)]))
    simple = analyse(speak([long(0.2)]))

    (rec,) = dense.recommendations_of(RecommendationType.ALLOW_PROCESSING_TIME)
    assert (rec.delta_seconds or 0) > 0 and rec.safety is Safety.EXPANDS
    assert ReasonCode.DENSE_PASSAGE in rec.reason_codes
    assert rec.slow_down_score.value is not None and rec.slow_down_score.value > 0
    (fast_simple,) = simple.passages
    assert fast_simple.pace is PaceClass.FAST  # the rate is the same ...
    assert (
        simple.recommendations_of(RecommendationType.ALLOW_PROCESSING_TIME) == ()
    )  # ... the need is not


def test_unknown_density_makes_the_load_unknown_never_low() -> None:
    (passage,) = analyse(speak([long(None)])).passages

    assert passage.cognitive_load.value is None
    assert "speech rate alone says nothing" in passage.cognitive_load.note
    assert (
        passage.cognitive_load.basis is Basis.HEURISTIC
        and passage.information_density.value is None
    )


def test_the_tutorial_profile_gives_dense_steps_more_time_than_the_generic_one() -> None:
    evidence = speak([long(0.8, requires=1)])

    tutorial = analyse(evidence, PacingConfig(profile="tutorial"))
    generic = analyse(evidence)

    assert extra_time(tutorial) > extra_time(generic)


# --- repetition -----------------------------------------------------------------------------
def test_a_restatement_that_adds_nothing_is_flagged_and_one_that_recaps_is_not() -> None:
    plain = analyse(speak([long(0.5), long(0.5, repeats="nothing_new", gap=0.6)]))
    recap = analyse(speak([long(0.5), long(0.5, repeats="recap", gap=0.6)]))

    (rec,) = plain.recommendations_of(RecommendationType.REDUCE_REPETITION)
    assert rec.safety is Safety.NEEDS_REVIEW and (rec.delta_seconds or 0) < 0
    assert any("rely on the restatement" in t for t in rec.tradeoffs)
    assert recap.recommendations_of(RecommendationType.REDUCE_REPETITION) == ()
    assert ReasonCode.REPEAT_SERVES_PURPOSE in recap.passages[1].reason_codes


def test_a_repeat_that_the_passage_needs_for_context_is_left_alone() -> None:
    result = analyse(speak([long(0.5), long(0.5, repeats="nothing_new", requires=1, gap=0.6)]))

    assert result.recommendations_of(RecommendationType.REDUCE_REPETITION) == ()


# --- conversation ---------------------------------------------------------------------------------
def test_a_conversation_keeps_its_turns_and_its_reactions() -> None:
    result = analyse(
        speak(
            [
                T(SENTENCE, speaker="A"),
                T(SENTENCE, gap=0.9, speaker="B"),
                T("ha that is funny", gap=1.4, speaker="A", event="laughter"),
                T(SENTENCE, gap=0.7, speaker="B"),
            ]
        ),
        PacingConfig(profile="interview_podcast"),
    )

    verdicts = [p.verdict for p in result.pauses]
    assert PauseVerdict.DEAD_AIR not in verdicts
    assert ReasonCode.SPEAKER_TURN_GAP in result.pauses[0].reason_codes
    assert ReasonCode.PAUSE_WITH_REACTION in result.pauses[1].reason_codes
    assert result.recommendations_of(RecommendationType.TIGHTEN_PAUSE) == ()
    assert all(p.speaker in {"A", "B"} for p in result.passages)
