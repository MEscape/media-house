"""Relations between recordings: used in proportion to how far they can be relied on."""

import json

from media_house.modules.story_architecture.domain.facts import AssetFacts
from media_house.modules.story_architecture.domain.model import StoryPlan
from media_house.modules.story_architecture.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.story_architecture.domain.validation import validate_plan
from media_house.modules.story_architecture.domain.values import (
    Certainty,
    IssueKind,
    Priority,
    Severity,
    Treatment,
)
from tests.support.story_fakes import asset, link, plan_of, seg, topic


def two() -> tuple[AssetFacts, AssetFacts]:
    """Two recordings of two sections each, nothing marked redundant on its own."""

    def one(asset_id: str) -> AssetFacts:
        return asset(
            [seg(1, "Alpha."), seg(2, "Beta.")],
            [topic(1, 1, label="first"), topic(2, 2, label="second")],
            asset_id=asset_id,
        )

    return one("orig"), one("copy")


def priorities(plan: StoryPlan, asset_id: str) -> set[Priority]:
    return {s.priority for s in plan.sections if s.sources[0].asset_id == asset_id}


def only(plan: StoryPlan):  # type: ignore[no-untyped-def]
    [relation] = plan.asset_relations
    return relation


def test_without_relations_nothing_changes() -> None:
    plan = plan_of(two())

    assert plan.asset_relations == ()
    assert not [i for i in plan.completeness_issues if i.kind is IssueKind.RELATED_RECORDINGS]


def test_a_recorded_exact_copy_makes_the_copy_redundant_and_the_original_not() -> None:
    plan = plan_of(
        two(), links=[link("exact_duplicate_of", "copy", "orig", status=None, grade="recorded")]
    )

    relation = only(plan)
    assert relation.certainty is Certainty.RECORDED
    assert relation.treatment is Treatment.REDUNDANT_COPY
    assert priorities(plan, "copy") == {Priority.REDUNDANT_CANDIDATE}
    assert Priority.REDUNDANT_CANDIDATE not in priorities(plan, "orig")
    assert set(relation.affected_sections) == {
        s.section_id for s in plan.sections if s.sources[0].asset_id == "copy"
    }
    reasons = [r for s in plan.sections for r in s.priority_reasons if "exact copy" in r]
    assert reasons  # the plan says WHY, in terms a reader can check
    assert validate_plan(plan) == []


def test_a_derived_recording_counts_as_the_same_content() -> None:
    plan = plan_of(
        two(), links=[link("derived_from", "copy", "orig", status=None, grade="recorded")]
    )

    assert only(plan).treatment is Treatment.SAME_RECORDING
    assert priorities(plan, "copy") == {Priority.REDUNDANT_CANDIDATE}


def test_confirmed_alternate_takes_are_flagged_but_nothing_is_dropped() -> None:
    plain = plan_of(two())

    plan = plan_of(
        two(),
        links=[link("alternate_take_of", "copy", "orig", status="confirmed", grade="indicated")],
    )

    assert only(plan).treatment is Treatment.ALTERNATE_TAKES
    assert only(plan).certainty is Certainty.CONFIRMED
    [issue] = [i for i in plan.completeness_issues if i.kind is IssueKind.RELATED_RECORDINGS]
    assert issue.severity is Severity.WARNING and "use one" in issue.detail
    assert [s.priority for s in plan.sections] == [s.priority for s in plain.sections]
    assert plan.narrative_pattern.recommended_order == plain.narrative_pattern.recommended_order


def test_confirmed_parallel_coverage_is_reported_without_changing_the_plan() -> None:
    plain = plan_of(two())

    plan = plan_of(
        two(), links=[link("temporally_overlaps_with", "copy", "orig", status="confirmed")]
    )

    assert only(plan).treatment is Treatment.PARALLEL_COVERAGE
    assert [s.priority for s in plan.sections] == [s.priority for s in plain.sections]


def test_a_detected_claim_is_a_possibility_not_a_fact() -> None:
    plain = plan_of(two())

    plan = plan_of(
        two(),
        links=[link("alternate_take_of", "copy", "orig", status="proposed", grade="corroborated")],
    )

    relation = only(plan)
    assert relation.certainty is Certainty.EVIDENCE_SUPPORTED  # still not CONFIRMED
    assert relation.treatment is Treatment.NOTED and relation.affected_sections == ()
    assert "not confirmed" in relation.effect
    assert [s.priority for s in plan.sections] == [s.priority for s in plain.sections]
    assert plan.narrative_pattern == plain.narrative_pattern


def test_a_circumstantial_claim_is_an_unverified_hypothesis() -> None:
    plan = plan_of(
        two(),
        links=[
            link("temporally_overlaps_with", "copy", "orig", grade="suggestive", contradicted=True)
        ],
    )

    relation = only(plan)
    assert relation.certainty is Certainty.UNVERIFIED and relation.treatment is Treatment.NOTED
    assert relation.contradicted and "disagrees" in relation.effect  # the conflict is kept


def test_a_rejected_or_stale_claim_is_listed_and_never_used() -> None:
    for status in ("rejected", "stale"):
        plan = plan_of(
            two(),
            links=[link("alternate_take_of", "copy", "orig", status=status, grade="indicated")],
        )

        assert only(plan).certainty is Certainty.NOT_USABLE
        assert only(plan).treatment is Treatment.NOT_USED
        assert not [i for i in plan.completeness_issues if i.kind is IssueKind.RELATED_RECORDINGS]


def test_a_change_since_it_was_confirmed_is_not_hidden() -> None:
    plan = plan_of(
        two(), links=[link("alternate_take_of", "copy", "orig", status="confirmed", outdated=True)]
    )

    assert only(plan).outdated and "changed since" in only(plan).effect


def test_a_relation_to_a_recording_outside_the_story_is_ignored() -> None:
    plan = plan_of(
        two(), links=[link("exact_duplicate_of", "stranger", "orig", status=None, grade="recorded")]
    )

    assert plan.asset_relations == ()
    assert priorities(plan, "orig") == priorities(plan_of(two()), "orig")


def test_a_plan_with_relations_round_trips_and_an_older_document_still_reads() -> None:
    plan = plan_of(
        two(), links=[link("exact_duplicate_of", "copy", "orig", status=None, grade="recorded")]
    )

    assert plan_from_json(plan_to_json(plan)) == plan
    older = json.loads(plan_to_json(plan_of(two())))
    del older["asset_relations"]  # written before relations existed
    assert plan_from_json(json.dumps(older)).asset_relations == ()
