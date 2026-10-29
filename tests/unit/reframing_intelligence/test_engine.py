"""The whole analysis: identity, reuse, neighbours, alternatives, storage form, validation."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from media_house.modules.reframing_intelligence.domain.engine import cache_key, plan_reframing
from media_house.modules.reframing_intelligence.domain.facts import (
    ContinuityFacts,
    CutHint,
    Inputs,
    ShotFacts,
    StoryFacts,
)
from media_house.modules.reframing_intelligence.domain.geometry import Crop, TargetFormat
from media_house.modules.reframing_intelligence.domain.profile import (
    PROFILES,
    ReframingConfig,
    SafeArea,
)
from media_house.modules.reframing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.reframing_intelligence.domain.validation import validate_analysis
from media_house.modules.reframing_intelligence.domain.values import (
    Decision,
    Feasibility,
    IssueKind,
    ResultState,
    ScoreName,
    Severity,
    Strategy,
)
from media_house.shared.errors import DomainError
from tests.support.refr_fakes import (
    ASSET,
    T11,
    T169,
    T916,
    analyse,
    beat,
    f,
    geom,
    profile,
    shot,
    source,
    still,
    walk,
)

PERSON = (0.40, 0.15, 0.60, 0.90)


def key_of(
    *shots: ShotFacts,
    target: TargetFormat = T916,
    config: ReframingConfig | None = None,
    story: StoryFacts | None = None,
    continuity: ContinuityFacts | None = None,
) -> dict[str, object]:
    config = config or ReframingConfig()
    inputs = Inputs((source(*shots),), story, continuity)
    return cache_key(inputs, target, config, config.resolve(None))


def two_shots(second_x: float = 0.40):  # type: ignore[no-untyped-def]
    return (
        shot("s0", 0, 4, (still("p", PERSON, cluster="person_A"),), usability=0.9),
        shot("s1", 4, 8, (still("p", (second_x, 0.15, second_x + 0.2, 0.9), start=4, end=8,
                                cluster="person_A"),)),
    )  # fmt: skip


def test_the_same_facts_give_the_same_analysis_down_to_the_ids() -> None:
    assert analyse(*two_shots()) == analyse(*two_shots())
    a = analyse(*two_shots())
    assert a.reframing_analysis_id.startswith("fr_")
    assert [r.segment_key for r in a.recommendations] == [f"{ASSET}:s0", f"{ASSET}:s1"]


def test_the_cache_key_moves_with_everything_that_can_change_the_answer() -> None:
    base = key_of(*two_shots())
    moved = (shot("s1", 4, 8, (still("p", (0.5, 0.15, 0.7, 0.9), start=4, end=8),)),)

    assert key_of(*two_shots()) == base
    assert key_of(*two_shots(), target=T11) != base  # target format
    assert key_of(*two_shots(), target=TargetFormat.of("9:16", width=720, height=1280)) != base
    assert key_of(*two_shots(), config=ReframingConfig(profile="shorts_reels")) != base  # profile
    areas = (SafeArea("captions", 0.1, 0.7, 0.9, 0.95),)
    assert key_of(*two_shots(), config=ReframingConfig(safe_areas=areas)) != base  # safe area
    assert key_of(two_shots()[0], *moved) != base  # a track moved
    story = StoryFacts("sp1", 1, "review", (beat("b1", "claim", 0, 4),), ())
    assert key_of(*two_shots(), story=story) != base  # the story
    cont = ContinuityFacts("ca1", (), "x")
    assert key_of(*two_shots(), continuity=cont) != base  # a continuity analysis


def test_a_changed_shot_is_planned_again_and_the_rest_is_carried_over() -> None:
    first = analyse(*two_shots())
    moved = (two_shots()[0], two_shots(second_x=0.50)[1])
    second = analyse(*moved, previous=first)

    assert (second.metrics.evaluated, second.metrics.reused) == (1, 1)
    assert second.recommendations[0] == first.recommendations[0]  # unchanged: same id, same crop
    assert second.recommendations[1].recommendation_id != first.recommendations[1].recommendation_id
    assert analyse(*two_shots(), previous=first).metrics.reused == 2


def test_a_different_target_never_reuses_another_targets_recommendations() -> None:
    first = analyse(*two_shots(), target=T916)
    second = analyse(*two_shots(), target=T11, previous=first)

    assert second.metrics.reused == 0
    assert {r.recommendation_id for r in first.recommendations}.isdisjoint(
        r.recommendation_id for r in second.recommendations
    )


def test_a_persons_decision_follows_the_segment_through_reanalysis() -> None:
    first = analyse(*two_shots())
    decided = {f"{ASSET}:s0": (Decision.REJECTED.value, "I want it wide")}
    inputs = Inputs((source(*two_shots()),), decisions=decided)
    now = datetime(2026, 10, 29, tzinfo=UTC)
    again = plan_reframing(inputs, T916, ReframingConfig(), now=now, previous=first)

    rejected = again.recommendations[0]
    assert rejected.decision is Decision.REJECTED and rejected.decision_note == "I want it wide"
    assert (
        rejected.strategy == first.recommendations[0].strategy
    )  # the proposal itself is untouched
    assert again.recommendations[1].decision is None


def test_adjacent_shots_with_conflicting_crops_are_flagged_with_their_size_step() -> None:
    near = shot("s0", 0, 4, (still("p", (0.35, 0.10, 0.65, 0.95), cluster="A"),))
    far = shot("s1", 4, 8, (still("p", (0.45, 0.35, 0.55, 0.65), start=4, end=8, cluster="A"),))
    a = analyse(near, far, target=T169, geometry=geom(3840, 2160))
    pair = a.pairs[0]

    assert pair.origin == "recording_order"
    assert pair.scale_ratio == pytest.approx(0.85 / 0.30, rel=0.01)
    second = a.recommendations[1]
    assert any(i.kind is IssueKind.ADJACENT_CROP and i.severity is Severity.WARNING
               for i in second.issues)  # fmt: skip
    for r in (second, a.recommendations[0]):
        compat = r.score(ScoreName.CONTINUITY_COMPATIBILITY)
        assert compat is not None and compat < 0.5


def test_matching_neighbours_are_compatible_and_different_people_are_not_compared() -> None:
    same = analyse(*two_shots())
    assert same.recommendations[0].score(ScoreName.CONTINUITY_COMPATIBILITY) == pytest.approx(1.0)
    other = (
        shot("s0", 0, 4, (still("p", PERSON, cluster="person_A"),)),
        shot("s1", 4, 8, (still("p", (0.2, 0.3, 0.4, 0.9), start=4, end=8, cluster="person_B"),)),
    )
    different = analyse(*other)
    assert different.pairs[0].score is None and "different people" in different.pairs[0].notes[0]


def test_a_size_step_hides_a_jump_cut_the_continuity_analysis_found() -> None:
    near = shot("s0", 0, 4, (still("p", (0.35, 0.10, 0.65, 0.95), cluster="A"),))
    far = shot("s1", 4, 8, (still("p", (0.45, 0.35, 0.55, 0.65), start=4, end=8, cluster="A"),))
    cut = CutHint(ASSET, f(4), ASSET, f(4), risk=0.85, jump_cut=True)
    a = analyse(near, far, target=T169, geometry=geom(3840, 2160),
                continuity=ContinuityFacts("ca1", (cut,), "ca1"))  # fmt: skip

    assert a.pairs[0].origin == "continuity:ca1" and a.pairs[0].jump_cut_effect == "hides"
    assert not any(i.severity is Severity.WARNING and i.kind is IssueKind.ADJACENT_CROP
                   for i in a.recommendations[1].issues)  # fmt: skip


def test_a_jump_cut_the_crops_do_not_change_is_reported_as_a_continuity_risk() -> None:
    cut = CutHint(ASSET, f(4), ASSET, f(4), risk=0.85, jump_cut=True)
    a = analyse(*two_shots(), continuity=ContinuityFacts("ca1", (cut,), "ca1"))

    assert a.pairs[0].jump_cut_effect == "does_not_change"
    assert any(i.kind is IssueKind.CONTINUITY_RISK for i in a.recommendations[1].issues)


def test_repeated_push_ins_are_flagged() -> None:
    pushed = PROFILES["talking_head"]
    shots = (
        shot("s0", 0, 8, (still("p", PERSON, end=8),)),
        shot("s1", 8, 16, (still("p", PERSON, start=8, end=16),)),
    )
    story = StoryFacts("sp", 1, None, (beat("b1", "claim", 0, 8), beat("b2", "claim", 8, 16)), ())
    a = plan_reframing(
        Inputs((source(*shots, geometry=geom(3840, 2160)),), story),
        T169,
        ReframingConfig(custom=pushed),
        now=datetime(2026, 10, 29, tzinfo=UTC),
    )

    assert all(r.strategy in (Strategy.PUSH_IN, Strategy.DYNAMIC_CROP) for r in a.recommendations)
    assert any("two push-ins" in i.detail for r in a.recommendations for i in r.issues)


def test_an_unreframeable_shot_lists_what_else_could_serve_and_what_each_needs() -> None:
    both = (still("a", (0.05, 0.2, 0.22, 0.9)), still("b", (0.75, 0.2, 0.92, 0.9)))
    tight = shot("s0", 0, 4, both, framing="close_up", scene_id="sc0", usability=0.9)
    wide = shot("s1", 4, 8, (still("a", (0.40, 0.4, 0.46, 0.6), start=4, end=8),), framing="wide",
                scene_id="sc0")  # fmt: skip
    a = analyse(tight, wide, target=T916)
    rec = a.recommendations[0]

    assert rec.strategy is Strategy.UNREFRAMEABLE and a.metrics.unreframeable == 1
    kinds = {x.strategy for x in rec.alternatives}
    assert {Strategy.WIDER_SOURCE, Strategy.FILL_TREATMENT} <= kinds
    assert Strategy.ALTERNATING_CROPS in kinds  # each person fits a window alone
    assert all(not x.executable and x.requires for x in rec.alternatives)
    wider = next(x for x in rec.alternatives if x.strategy is Strategy.WIDER_SOURCE)
    assert wider.shot_id == "s1" and wider.feasibility in (Feasibility.FEASIBLE,
                                                           Feasibility.TRADEOFFS)  # fmt: skip
    only_main = [x for x in rec.alternatives if x.omits]
    assert only_main and "required" in (only_main[0].requires or "")
    assert rec.preferred_over and "silently" in rec.preferred_over[0]


def test_uncertain_shots_make_the_analysis_partial_and_nothing_executable() -> None:
    a = analyse(shot("s0", 0, 4, ()), target=T916)

    assert a.state is ResultState.PARTIAL and a.metrics.uncertain == 1
    assert not a.recommendations[0].executable
    assert any("No hands" in lim for lim in a.limitations)


def test_the_analysis_survives_its_stored_form_exactly() -> None:
    track = walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=6)
    a = analyse(shot("s0", 0, 6, (track,)), shot("s1", 6, 9, ()), target=T916)

    assert analysis_from_json(analysis_to_json(a)) == a


def test_a_stored_document_that_is_unreadable_is_refused_not_reinterpreted() -> None:
    text = analysis_to_json(analyse(*two_shots()))

    with pytest.raises(DomainError):
        analysis_from_json(text.replace('"schema_version": 1', '"schema_version": 99'))
    with pytest.raises(DomainError):
        analysis_from_json(text.replace("static_crop", "teleport"))
    with pytest.raises(DomainError):
        analysis_from_json("{}")


def test_a_crop_of_the_wrong_shape_cannot_get_past_validation() -> None:
    a = analyse(*two_shots())
    assert [i for i in validate_analysis(a) if i.severity is Severity.ERROR] == []
    rec = a.recommendations[0]
    broken_key = replace(rec.keyframes[0], crop=Crop(0.0, 0.0, 0.5, 0.5))
    broken = replace(
        a, recommendations=(replace(rec, keyframes=(broken_key,)), *a.recommendations[1:])
    )

    errors = [i for i in validate_analysis(broken) if i.severity is Severity.ERROR]
    assert errors and "aspect" in errors[0].detail


def test_validation_catches_keyframes_outside_the_range_and_false_executability() -> None:
    a = analyse(*two_shots())
    rec = a.recommendations[0]
    late = replace(rec.keyframes[0], time=replace(rec.keyframes[0].time, numerator=99))
    bad_time = replace(a, recommendations=(replace(rec, keyframes=(rec.keyframes[0], late)),))
    bad_claim = replace(
        a, recommendations=(replace(rec, feasibility=Feasibility.IMPOSSIBLE, executable=True),)
    )

    assert any("outside" in i.detail for i in validate_analysis(bad_time))
    assert any("impossible" in i.detail for i in validate_analysis(bad_claim))


def test_recommendations_are_found_by_source_range_and_keep_source_time_only() -> None:
    a = analyse(*two_shots())
    from media_house.core.domain import Rational

    found = a.covering(ASSET, Rational(3, 1), Rational(5, 1))

    assert [r.shot_id for r in found] == ["s0", "s1"]
    assert a.covering(ASSET, Rational(8, 1), Rational(9, 1)) == []
    assert a.covering("other", Rational(0, 1), Rational(9, 1)) == []


def test_profiles_change_the_recommendation_but_not_the_measurements() -> None:
    track = (still("p", PERSON),)
    calm = analyse(shot("s0", 0, 4, track), config=ReframingConfig(profile="talking_head"))
    lively = analyse(shot("s0", 0, 4, track), config=ReframingConfig(profile="shorts_reels"))

    assert calm.recommendations[0].resolution == lively.recommendations[0].resolution
    assert profile("cinematic").composition.anchor_x != profile("generic").composition.anchor_x


def test_a_shot_without_duration_is_reported_not_planned() -> None:
    a = analyse(shot("s0", 0, 4, (still("p", PERSON),)), shot("s1", 4, 4, ()))

    assert [r.shot_id for r in a.recommendations] == ["s0"]
    assert a.state is ResultState.PARTIAL
    assert any("no duration" in i.detail for i in a.issues)


def test_one_shot_that_cannot_be_planned_does_not_cost_the_whole_analysis(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from media_house.modules.reframing_intelligence.domain import engine
    from media_house.shared.errors import InvariantViolation

    real = engine.plan_shot  # type: ignore[attr-defined]

    def flaky(one, *args):  # type: ignore[no-untyped-def]
        if one.shot_id == "s1":
            raise InvariantViolation("a box broke", user_message="a box broke")
        return real(one, *args)

    monkeypatch.setattr(engine, "plan_shot", flaky)

    a = analyse(*two_shots())

    good, bad = a.recommendations
    assert good.executable and not bad.executable
    assert bad.feasibility is Feasibility.UNCERTAIN
    assert any("could not be planned" in i.detail for i in bad.issues)
    assert a.state is ResultState.PARTIAL
