"""The planner on a real content analysis: coverage, traceability, determinism, storage form."""

from dataclasses import replace
from itertools import pairwise

import pytest

from media_house.modules.story_architecture.domain.errors import (
    InvalidStoryConfig,
    InvalidStoryDocument,
    NothingToPlan,
)
from media_house.modules.story_architecture.domain.facts import AssetFacts
from media_house.modules.story_architecture.domain.profile import FORMATS, StoryConfig, Thresholds
from media_house.modules.story_architecture.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.story_architecture.domain.validation import validate_plan
from media_house.modules.story_architecture.domain.values import DurationBasis, Priority
from tests.support.ci_texts import BATTERIES, THREE_TOPICS
from tests.support.story_fakes import asset, facts_for, plan_of, seg, topic


def known(facts: AssetFacts) -> dict[str, frozenset[str]]:
    return {facts.asset_id: frozenset(s.segment_id for s in facts.segments)}


def test_every_passage_lands_in_a_section_and_every_beat_cites_a_passage() -> None:
    facts = facts_for(THREE_TOPICS)

    plan = plan_of(facts)

    cited = {sid for s in plan.sections for ref in s.sources for sid in ref.segment_ids}
    assert cited == {s.segment_id for s in facts.segments}
    assert plan.beats
    assert all(b.source.segment_ids and b.source.unit_ids for b in plan.beats)
    assert validate_plan(plan, known(facts)) == []


def test_the_same_analysis_always_gives_the_same_plan() -> None:
    facts = facts_for(THREE_TOPICS)

    assert plan_of(facts) == plan_of(facts)
    assert plan_to_json(plan_of(facts)) == plan_to_json(plan_of(facts))


def test_the_stored_form_round_trips_exactly() -> None:
    plan = plan_of(facts_for(THREE_TOPICS))

    assert plan_from_json(plan_to_json(plan)) == plan


def test_a_reader_refuses_what_it_does_not_know() -> None:
    plan = plan_of(facts_for(BATTERIES))
    text = plan_to_json(plan)

    for broken in (
        "not json",
        text.replace('"story_plan"', '"something_else"', 1),
        '"schema_version": 99'.join(text.rsplit('"schema_version": 1', 1)),
        text.replace(f'"state": "{plan.state.value}"', '"state": "invented"', 1),
        text.replace('"story_plan_id"', '"surprise": 1, "story_plan_id"', 1),
    ):
        assert broken != text
        with pytest.raises(InvalidStoryDocument):
            plan_from_json(broken)


def test_sections_carry_scores_priorities_and_durations_as_observed() -> None:
    plan = plan_of(facts_for(THREE_TOPICS))

    assert {s.priority for s in plan.sections} <= set(Priority)
    assert all(s.priority_reasons for s in plan.sections)
    assert all(s.duration.basis is DurationBasis.OBSERVED for s in plan.sections)
    total = plan.estimated_duration.total.seconds
    assert total is not None
    assert total == pytest.approx(sum(s.duration.seconds or 0 for s in plan.sections), abs=1e-3)


def test_the_recommended_order_and_neighbours_agree() -> None:
    plan = plan_of(facts_for(THREE_TOPICS))

    ordered = plan.ordered_sections()

    assert [s.section_id for s in ordered] == list(plan.narrative_pattern.recommended_order)
    assert ordered[0].before is None and ordered[-1].after is None
    assert all(a.after == b.section_id for a, b in pairwise(ordered))


def test_the_plan_states_what_it_cannot_say() -> None:
    plan = plan_of(facts_for(BATTERIES))

    assert any("verified" in text for text in plan.limitations)
    assert all(k.verification == "unverified" for k in plan.key_information)
    assert plan.audience_intent.audience is None  # never inferred from the recording


def test_the_audience_is_only_what_the_caller_said() -> None:
    plan = plan_of(facts_for(BATTERIES), StoryConfig("tutorial", audience="new owners"))

    assert plan.audience_intent.audience == "new owners"
    assert plan.target_format == "tutorial"
    assert plan.narrative_pattern.pattern == FORMATS["tutorial"].pattern


def test_an_empty_analysis_has_nothing_to_plan() -> None:
    with pytest.raises(NothingToPlan):
        plan_of(asset([], []))


def test_a_bad_configuration_is_refused_before_any_work() -> None:
    with pytest.raises(InvalidStoryConfig):
        StoryConfig("no-such-format").resolve()
    with pytest.raises(InvalidStoryConfig):
        StoryConfig(
            thresholds=Thresholds(essential_importance=0.2, optional_importance=0.5)
        ).resolve()


def test_passages_upstream_put_in_no_topic_stay_in_the_story() -> None:
    segments = [seg(1), seg(2), seg(3), seg(4)]
    facts = asset(segments, [topic(1, 1, 2), topic(2, 4)])

    plan = plan_of(facts)

    covered = [sid for s in plan.sections for ref in s.sources for sid in ref.segment_ids]
    assert sorted(covered) == ["s00001", "s00002", "s00003", "s00004"]
    assert validate_plan(plan, known(facts)) == []


def test_a_recording_without_topics_is_one_section() -> None:
    facts = asset([seg(1), seg(2)], [])

    plan = plan_of(facts)

    assert len(plan.sections) == 1
    assert plan.sections[0].sources[0].segment_ids == ("s00001", "s00002")


def test_several_recordings_follow_the_order_given() -> None:
    first = asset([seg(1)], [topic(1, 1)], asset_id="a1")
    second = asset([seg(1)], [topic(1, 1)], asset_id="a2")

    plan = plan_of([first, second])

    assert [s.sources[0].asset_id for s in plan.ordered_sections()] == ["a1", "a2"]
    assert len({s.section_id for s in plan.sections}) == 2
    assert validate_plan(plan, {**known(first), **known(second)}) == []


def test_validation_names_what_is_wrong() -> None:
    facts = facts_for(THREE_TOPICS)
    plan = plan_of(facts)

    dangling = replace(plan, beats=(replace(plan.beats[0], section_id="nope"), *plan.beats[1:]))
    duplicate = replace(plan, beats=(plan.beats[0], plan.beats[0]))
    wrong_total = replace(
        plan,
        estimated_duration=replace(
            plan.estimated_duration,
            total=replace(plan.estimated_duration.total, seconds=1.0),
        ),
    )

    assert any("unknown section" in p for p in validate_plan(dangling))
    assert any("duplicate" in p for p in validate_plan(duplicate))
    assert any("sum" in p for p in validate_plan(wrong_total))
    assert any("unknown segment" in p for p in validate_plan(plan, {"a1": frozenset({"x"})}))
    assert any("not a source" in p for p in validate_plan(replace(plan, source_assets=())))
