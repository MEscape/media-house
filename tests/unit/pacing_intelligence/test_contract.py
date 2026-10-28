"""Strategies, targets, profiles, degradation, validation and the stored form."""

import json
from dataclasses import replace

import pytest

from media_house.core.domain import Rational
from media_house.modules.pacing_intelligence.domain.errors import (
    InvalidPacingConfig,
    InvalidPacingDocument,
    NothingToAnalyse,
)
from media_house.modules.pacing_intelligence.domain.facts import AssetEvidence
from media_house.modules.pacing_intelligence.domain.model import Issue, PacingAnalysis
from media_house.modules.pacing_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    STRATEGIES,
    PacingConfig,
    TargetDuration,
)
from media_house.modules.pacing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.pacing_intelligence.domain.validation import validate
from media_house.modules.pacing_intelligence.domain.values import (
    IssueKind,
    PauseVerdict,
    RecommendationType,
    ResultState,
    Severity,
    Strategy,
)
from tests.support.pace_fakes import Sec, T, analyse, shots, speak, tell

SENTENCE = "this phone has a great screen and a really long lasting battery"


def dead_air_video(count: int = 6, gap: float = 2.5):  # type: ignore[no-untyped-def]
    return speak([T(SENTENCE, gap=gap if i else 0.3) for i in range(count)])


# --- strategies -----------------------------------------------------------------------------
def test_the_five_strategies_recommend_different_things_not_a_speed_factor() -> None:
    evidence = speak([T(SENTENCE, gap=1.0 if i else 0.2) for i in range(5)])

    results = {s: analyse(evidence, PacingConfig(strategy=s)) for s in Strategy}

    long_pauses = {s: len(r.pauses_of(PauseVerdict.LONG)) for s, r in results.items()}
    assert long_pauses[Strategy.CONCISE] > long_pauses[Strategy.DETAILED] == 0
    assert (
        results[Strategy.CONCISE].duration.contraction_seconds
        > results[Strategy.DETAILED].duration.contraction_seconds
    )
    assert (
        results[Strategy.HIGH_ENERGY].duration.contraction_seconds
        > results[Strategy.CINEMATIC].duration.contraction_seconds
    )
    assert all(r.strategy is s for s, r in results.items())


def test_every_result_projects_all_five_strategies_consistently() -> None:
    evidence = speak([T(SENTENCE, gap=1.0 if i else 0.2) for i in range(5)])

    balanced = analyse(evidence)

    assert [p.strategy for p in balanced.strategy_projections] == list(Strategy)
    own = next(p for p in balanced.strategy_projections if p.strategy is Strategy.BALANCED)
    assert own.recommendations == len(balanced.recommendations)
    assert own.contraction_seconds == pytest.approx(balanced.duration.contraction_seconds)
    concise = analyse(evidence, PacingConfig(strategy=Strategy.CONCISE))
    projected = next(p for p in balanced.strategy_projections if p.strategy is Strategy.CONCISE)
    assert projected.contraction_seconds == pytest.approx(concise.duration.contraction_seconds)
    assert STRATEGIES[Strategy.BALANCED].pause == 1.0


def test_a_recommendation_lists_every_strategy_under_which_it_applies() -> None:
    result = analyse(dead_air_video(), PacingConfig(profile="talking_head"))

    tighten = result.recommendations_of(RecommendationType.TIGHTEN_PAUSE)
    assert tighten and all(
        set(r.strategies) >= {Strategy.BALANCED, Strategy.CONCISE} for r in tighten
    )


# --- target length --------------------------------------------------------------------------
def test_a_target_the_safe_changes_can_meet_marks_the_recommendations_that_get_there() -> None:
    evidence = dead_air_video()
    base = analyse(evidence, PacingConfig(profile="talking_head"))
    current = base.duration.current_seconds
    assert current is not None
    wanted = current - base.duration.contraction_seconds / 2

    result = analyse(
        evidence, PacingConfig(profile="talking_head", target=TargetDuration(maximum=wanted))
    )

    assert result.duration.target is not None and result.duration.target.status == "within"
    assert (
        result.duration.proposed_seconds is not None and result.duration.proposed_seconds <= wanted
    )
    marked = [r for r in result.recommendations if r.contributes_to_target]
    assert marked and sum(-(r.delta_seconds or 0) for r in marked) >= current - wanted
    assert "shortening doubled" in result.recommendations[0].rank_basis


def test_a_target_that_cannot_be_met_reports_the_shortfall_and_does_not_speed_up_speech() -> None:
    evidence = dead_air_video()
    base = analyse(evidence, PacingConfig(profile="talking_head"))
    impossible = (base.duration.current_seconds or 0) / 4

    result = analyse(
        evidence, PacingConfig(profile="talking_head", target=TargetDuration(maximum=impossible))
    )

    target = result.duration.target
    assert target is not None and target.status == "over" and target.shortfall_seconds > 0
    assert "removing content" in target.note and "faster speech" in target.note
    assert result.duration.proposed_seconds == pytest.approx(
        base.duration.proposed_seconds
    )  # nothing forced
    assert any(i.kind is IssueKind.TARGET and i.severity is Severity.WARNING for i in result.issues)
    assert all(
        r.type is not RecommendationType.REDUCE_REPETITION or r.safety.value != "safe"
        for r in result.recommendations
    )


def test_a_target_longer_than_the_material_is_not_filled_with_padding() -> None:
    evidence = dead_air_video()
    base = analyse(evidence, PacingConfig(profile="talking_head"))

    result = analyse(
        evidence, PacingConfig(profile="talking_head", target=TargetDuration(minimum=1000.0))
    )

    target = result.duration.target
    assert (
        target is not None
        and target.status == "under"
        and "nothing is added just to fill" in target.note
    )
    assert result.duration.expansion_seconds == pytest.approx(base.duration.expansion_seconds)


def test_the_story_lengths_set_the_current_runtime_and_the_source_is_kept_apart() -> None:
    evidence = speak([T(SENTENCE) for _ in range(3)])
    story = tell(
        evidence,
        [Sec("a", (0, 1), "essential", seconds=30.0), Sec("b", (2,), "supporting", seconds=12.0)],
    )

    result = analyse(evidence, story=story)

    assert result.duration.current_seconds == pytest.approx(42.0)
    assert result.duration.current_basis == "story plan section lengths"
    assert result.duration.source_seconds == pytest.approx(evidence.source.duration)
    assert "recordings are added" in " ".join(result.duration.assumptions)


# --- priorities -----------------------------------------------------------------------------
def test_priorities_are_unique_ordered_by_a_stated_basis_and_not_a_certainty() -> None:
    result = analyse(dead_air_video(), PacingConfig(profile="talking_head"))

    ranks = [r.priority for r in result.recommendations]
    assert ranks == list(range(1, len(ranks) + 1))
    assert "x confidence" in result.recommendations[0].rank_basis
    weights = [abs(r.delta_seconds or 2.0) * (r.confidence or 0.3) for r in result.recommendations]
    assert weights == sorted(weights, reverse=True)


# --- degraded evidence ----------------------------------------------------------------------
def test_estimated_word_times_lower_the_confidence_and_say_so() -> None:
    forced = analyse(dead_air_video(), PacingConfig(profile="talking_head"))
    estimated = analyse(
        speak([T(SENTENCE, gap=2.5 if i else 0.3) for i in range(6)], forced=False),
        PacingConfig(profile="talking_head"),
    )

    f, e = forced.recommendations[0], estimated.recommendations[0]
    assert f.confidence is not None and e.confidence is not None and e.confidence < f.confidence
    assert any("estimated within segments" in limit for limit in e.confidence_limits)
    assert any(i.kind is IssueKind.TIMING for i in estimated.issues)


def test_without_content_analysis_density_load_and_repetition_are_unknown() -> None:
    result = analyse(speak([T(SENTENCE) for _ in range(3)], with_content=False))

    assert result.passages and all(p.information_density.value is None for p in result.passages)
    assert all(
        p.cognitive_load.value is None and p.repetition.value is None for p in result.passages
    )
    assert any("no content analysis" in i.detail for i in result.issues)
    assert next(s for s in result.stages if s.stage == "information").state.value == "not_available"


def test_without_audio_the_result_is_partial_and_speech_findings_are_absent() -> None:
    evidence = shots(speak([T(SENTENCE)], with_audio=False), [(0.0, 8.0), (8.0, 12.0)])

    result = analyse(evidence)

    assert result.state is ResultState.PARTIAL
    assert result.pauses == () and result.shots
    assert any("no audio analysis" in i.detail for i in result.issues)


def test_with_neither_speech_nor_shots_there_is_nothing_to_analyse() -> None:
    with pytest.raises(NothingToAnalyse):
        analyse(speak([T(SENTENCE)], with_audio=False, with_content=False))


def test_two_recordings_are_analysed_apart_and_never_placed_on_one_clock() -> None:
    one, two = (
        speak([T(SENTENCE), T(SENTENCE, gap=2.5)], asset_id="a1"),
        speak([T(SENTENCE), T(SENTENCE, gap=2.5)], asset_id="a2"),
    )

    result = analyse({"a1": one, "a2": two}, PacingConfig(profile="talking_head"))

    assert {p.span.asset_id for p in result.passages} == {"a1", "a2"}
    assert [t.asset_id for t in result.timeline] == ["a1", "a2"]
    assert {p.pause_id for p in result.pauses} == {"pz:0:0", "pz:1:0"}
    assert all(r.target.asset_id in {"a1", "a2"} for r in result.recommendations)
    assert result.duration.source_seconds == pytest.approx(
        (one.source.duration or 0.0) + (two.source.duration or 0.0)
    )
    ids = [r.recommendation_id for r in result.recommendations]
    assert len(set(ids)) == len(ids)


def test_the_analysis_is_deterministic() -> None:
    assert analyse(dead_air_video()) == analyse(dead_air_video())


# --- profiles -------------------------------------------------------------------------------
def test_all_nine_profiles_and_the_neutral_default_exist_and_are_valid() -> None:
    required = {
        "top_10_ranking", "product_review", "unboxing", "talking_head", "tutorial",
        "interview_podcast", "documentary", "shorts_reels", "cinematic_montage",
    }  # fmt: skip
    assert required <= set(PROFILES) and DEFAULT_PROFILE in PROFILES
    assert all(p.problems() == [] for p in PROFILES.values())
    assert PROFILES["cinematic_montage"].prefer_beats and not PROFILES["talking_head"].prefer_beats
    assert PROFILES["shorts_reels"].premise_deadline == 4.0


def test_a_profile_is_chosen_from_the_story_format_only_when_the_name_is_exact() -> None:
    config = PacingConfig()

    assert config.resolve("tutorial").name == "tutorial"
    assert config.resolve("short").name == "shorts_reels"
    assert config.resolve("something_unclear").name == DEFAULT_PROFILE
    assert config.resolve(None).name == DEFAULT_PROFILE
    assert PacingConfig(profile="documentary").resolve("tutorial").name == "documentary"


def test_a_bad_configuration_is_refused_with_a_reason() -> None:
    with pytest.raises(InvalidPacingConfig, match="Unknown profile"):
        PacingConfig(profile="vaporwave").resolve()
    bad = replace(PROFILES["generic"], wpm_low=300.0)
    with pytest.raises(InvalidPacingConfig, match="wpm_low"):
        PacingConfig(profile=bad).resolve()
    with pytest.raises(InvalidPacingConfig, match="minimum <= preferred"):
        PacingConfig(target=TargetDuration(minimum=10, maximum=5)).resolve()


def test_a_profile_built_for_one_run_changes_the_result_and_the_fingerprint() -> None:
    strict = replace(PROFILES["generic"], name="strict", dead_air=0.9, natural_pause=0.4)
    evidence = speak([T(SENTENCE), T(SENTENCE, gap=1.0)])

    custom = analyse(evidence, PacingConfig(profile=strict))

    assert custom.pauses[0].verdict is PauseVerdict.DEAD_AIR
    assert PacingConfig(profile=strict).fingerprint(strict) != PacingConfig().fingerprint(
        PROFILES["generic"]
    )


# --- validation -----------------------------------------------------------------------------
def checked(result: PacingAnalysis, evidence: AssetEvidence) -> list[Issue]:
    return [
        i
        for i in validate(result, {evidence.source.asset_id: evidence})
        if i.severity is Severity.ERROR
    ]


def test_a_good_analysis_has_no_errors() -> None:
    evidence = dead_air_video()

    assert checked(analyse(evidence, PacingConfig(profile="talking_head")), evidence) == []


def test_a_range_past_the_end_of_the_recording_is_an_error() -> None:
    evidence = dead_air_video()
    result = analyse(evidence, PacingConfig(profile="talking_head"))
    bad = replace(
        result.recommendations[0],
        target=replace(result.recommendations[0].target, end=Rational(10_000, 1)),
    )

    errors = checked(replace(result, recommendations=(bad, *result.recommendations[1:])), evidence)

    assert any(
        e.kind is IssueKind.INVALID_RANGE and "after the recording ends" in e.detail for e in errors
    )


def test_a_recommendation_without_evidence_or_a_runtime_that_does_not_add_up_is_an_error() -> None:
    evidence = dead_air_video()
    result = analyse(evidence, PacingConfig(profile="talking_head"))
    bare = replace(result.recommendations[0], evidence=())
    wrong = replace(result.duration, proposed_seconds=1.0)

    assert any(
        "without evidence" in e.detail
        for e in checked(
            replace(result, recommendations=(bare, *result.recommendations[1:])), evidence
        )
    )
    assert any(
        e.kind is IssueKind.DURATION for e in checked(replace(result, duration=wrong), evidence)
    )


def test_a_pause_that_is_both_preserved_and_tightened_is_an_error() -> None:
    evidence = dead_air_video()
    result = analyse(evidence, PacingConfig(profile="talking_head"))
    tighten = result.recommendations_of(RecommendationType.TIGHTEN_PAUSE)[0]
    twin = replace(
        tighten,
        type=RecommendationType.PRESERVE_PAUSE,
        recommendation_id="rc:twin",
        priority=len(result.recommendations) + 1,
    )

    errors = checked(replace(result, recommendations=(*result.recommendations, twin)), evidence)

    assert any(e.kind is IssueKind.CONFLICT for e in errors)


# --- stored form ----------------------------------------------------------------------------
def test_an_analysis_survives_its_stored_form_unchanged() -> None:
    evidence = shots(dead_air_video(), [(0.0, 12.0), (12.0, 40.0)])
    result = analyse(evidence, PacingConfig(profile="talking_head"))

    assert result.recommendations and result.shots and result.timeline[0].attention
    assert analysis_from_json(analysis_to_json(result)) == result


def test_times_are_stored_as_numerator_and_denominator_and_the_reader_is_strict() -> None:
    result = analyse(dead_air_video())
    document = json.loads(analysis_to_json(result))

    assert set(document["passages"][0]["span"]["start"]) == {"numerator", "denominator"}
    assert document["document_type"] == "pacing_analysis"
    with pytest.raises(InvalidPacingDocument, match="not valid JSON"):
        analysis_from_json("{")
    with pytest.raises(InvalidPacingDocument, match="not a pacing analysis"):
        analysis_from_json(json.dumps({**document, "document_type": "edit_plan"}))
    with pytest.raises(InvalidPacingDocument, match="schema version"):
        analysis_from_json(json.dumps({**document, "schema_version": 99}))
    with pytest.raises(InvalidPacingDocument, match="unknown fields"):
        analysis_from_json(json.dumps({**document, "surprise": 1}))
    with pytest.raises(InvalidPacingDocument, match="unknown ResultState"):
        analysis_from_json(json.dumps({**document, "state": "great"}))


def test_silence_before_the_first_word_and_after_the_last_is_not_a_pause_in_speech() -> None:
    padded = speak([T(SENTENCE, gap=3.0)])  # three seconds of nothing, then one sentence

    result = analyse(padded, PacingConfig(profile="talking_head"))

    assert result.pauses == ()


def test_savings_in_material_the_story_left_out_do_not_change_the_story_runtime() -> None:
    evidence = speak([T(SENTENCE), T(SENTENCE), T(SENTENCE, gap=9.0)])
    story = tell(evidence, [Sec("only", (0,), "essential", seconds=4.0)])

    result = analyse(evidence, PacingConfig(profile="talking_head"), story=story)

    assert result.pauses_of(PauseVerdict.LONG) or result.pauses_of(PauseVerdict.DEAD_AIR)
    assert result.duration.current_seconds == 4.0
    assert result.duration.proposed_seconds == 4.0  # the nine seconds are not in the story
