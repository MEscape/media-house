"""Factual integrity, openings, pacing, visuals and alternatives, on hand-built facts."""

from dataclasses import replace

from media_house.modules.story_architecture.domain.facts import (
    ShotMatchFacts,
    VisualFacts,
)
from media_house.modules.story_architecture.domain.model import StoryPlan
from media_house.modules.story_architecture.domain.profile import StoryConfig
from media_house.modules.story_architecture.domain.validation import validate_plan
from media_house.modules.story_architecture.domain.values import (
    BeatFunction,
    DurationBasis,
    IssueKind,
    Necessity,
    OpeningStrategy,
    PacingAction,
    PacingKind,
    Priority,
    Severity,
    VisualPurpose,
)
from tests.support.story_fakes import asset, item, plan_of, rel, seg, topic


def kinds(plan: StoryPlan) -> list[IssueKind]:
    return [i.kind for i in plan.completeness_issues]


# --- factual integrity ---------------------------------------------------------------------
def test_a_claim_with_no_supporting_passage_is_flagged_but_never_called_false() -> None:
    facts = asset(
        [seg(1, "The battery lasts ten years.", importance=0.9)],
        [topic(1, 1)],
        items=[item("k1", "claim", 1, "The battery lasts ten years.")],
    )

    plan = plan_of(facts)

    [issue] = [i for i in plan.completeness_issues if i.kind is IssueKind.UNSUPPORTED_CLAIM]
    assert issue.severity is Severity.WARNING
    assert "No supporting passage was found" in issue.detail
    assert plan.key_information[0].verification == "unverified"


def test_a_claim_with_a_supporting_passage_is_linked_to_it() -> None:
    facts = asset(
        [
            seg(1, "It lasts ten years.", importance=0.9),
            seg(2, "Tests ran for ten years.", roles=[("supporting_evidence", 0.8)]),
        ],
        [topic(1, 1, 2)],
        relations=[rel("supports", 2, 1)],
        items=[item("k1", "claim", 1)],
    )

    plan = plan_of(facts)

    [info] = plan.key_information
    assert info.support_ids and IssueKind.UNSUPPORTED_CLAIM not in kinds(plan)
    [link] = plan.supporting_evidence
    assert link.basis.value == "direct"
    assert link.support.segment_ids == ("s00002",)


def test_evidence_in_the_same_section_is_an_inferred_link_and_says_so() -> None:
    facts = asset(
        [seg(1, "It lasts ten years."), seg(2, "See?", roles=[("demonstration", 0.8)])],
        [topic(1, 1, 2)],
        items=[item("k1", "claim", 1)],
    )

    plan = plan_of(facts)

    assert {link.basis.value for link in plan.supporting_evidence} == {"inferred"}


def test_a_contradiction_keeps_both_sides_and_judges_neither() -> None:
    facts = asset(
        [seg(1, "It is waterproof."), seg(2, "Actually it is not waterproof.")],
        [topic(1, 1, label="first"), topic(2, 2, label="second")],
        relations=[rel("contradicts", 2, 1)],
    )

    plan = plan_of(facts)

    [issue] = [i for i in plan.completeness_issues if i.kind is IssueKind.CONTRADICTION]
    assert issue.severity is Severity.HIGH
    assert "neither is judged correct" in issue.detail
    assert len(plan.sections) == 2


def test_repeated_material_is_reported_with_what_it_adds() -> None:
    facts = asset(
        [seg(1), seg(2, removal=0.9, importance=0.1)],
        [topic(1, 1), topic(2, 2)],
        relations=[rel("repeats", 2, 1)],
    )

    plan = plan_of(facts)

    assert any(i.kind is IssueKind.REDUNDANT_MATERIAL for i in plan.completeness_issues)
    assert plan.sections[1].priority is Priority.REDUNDANT_CANDIDATE
    assert any(n.kind is PacingKind.REPETITIVE for n in plan.pacing_notes)


def test_a_repeat_that_adds_something_is_not_a_redundant_candidate() -> None:
    facts = asset(
        [seg(1), seg(2, removal=0.9, importance=0.4)],
        [topic(1, 1), topic(2, 2)],
        relations=[rel("repeats", 2, 1, adds="new_evidence")],
    )

    plan = plan_of(facts)

    assert plan.sections[1].priority is not Priority.REDUNDANT_CANDIDATE


# --- openings ------------------------------------------------------------------------------
def test_the_natural_start_is_always_a_candidate() -> None:
    plan = plan_of(asset([seg(1), seg(2, hook=0.9)], [topic(1, 1), topic(2, 2)]))

    strategies = {o.strategy for o in plan.candidate_openings}

    assert OpeningStrategy.NATURAL_START in strategies
    assert OpeningStrategy.STRONGEST_HOOK in strategies
    assert [o.rank for o in plan.candidate_openings] == [1, 2]


def test_every_opening_is_a_real_passage_with_its_own_criteria() -> None:
    plan = plan_of(asset([seg(1), seg(2, hook=0.9)], [topic(1, 1), topic(2, 2)]))

    for opening in plan.candidate_openings:
        assert opening.excerpt and opening.source.segment_ids
        assert {c.name for c in opening.criteria} == {
            "clarity",
            "relevance",
            "promise_alignment",
            "credibility",
            "curiosity",
        }


def test_a_missing_score_is_none_with_a_reason_not_zero() -> None:
    facts = asset([seg(1, standalone=None)], [topic(1, 1)])

    [opening] = plan_of(facts).candidate_openings

    clarity = next(c for c in opening.criteria if c.name == "clarity")
    assert clarity.value is None and "no standalone score" in clarity.reason


def test_a_hook_that_needs_earlier_sentences_is_not_recommended() -> None:
    needy = seg(2, hook=0.95, standalone=0.6, requires_context=("u000001",))
    facts = asset([seg(1), needy], [topic(1, 1), topic(2, 2)])

    plan = plan_of(facts)

    hook = next(o for o in plan.candidate_openings if o.strategy is OpeningStrategy.STRONGEST_HOOK)
    assert hook.risks and hook.context_required is not None
    assert hook.context_required.segment_ids == ("s00001",)
    recommended = plan.opening(plan.recommended_opening or "")
    assert recommended is not None and not recommended.risks


def test_a_late_premise_is_reported() -> None:
    segments = [
        seg(1, "Intro."),
        seg(2, "More intro."),
        seg(3, "The point is this.", roles=[("main_point", 0.9)]),
    ]
    facts = asset(segments, [topic(1, 1, 2, 3)])

    plan = plan_of(facts, StoryConfig("short"))

    assert plan.premise is not None
    assert IssueKind.DELAYED_PREMISE in kinds(plan)


def test_a_format_expecting_a_step_reports_when_it_is_missing() -> None:
    plan = plan_of(asset([seg(1)], [topic(1, 1)]), StoryConfig("review"))

    missing = [
        i.detail for i in plan.completeness_issues if i.kind is IssueKind.MISSING_EXPECTED_ROLE
    ]
    assert any("caveat" in d for d in missing)


# --- pacing and duration -------------------------------------------------------------------
def test_untimed_material_is_estimated_from_words_and_said_to_be() -> None:
    facts = asset([seg(1, "word " * 150, timed=False)], [topic(1, 1)])

    plan = plan_of(facts)

    duration = plan.sections[0].duration
    assert duration.basis is DurationBasis.ESTIMATED_FROM_WORDS
    assert duration.seconds == 60.0  # 150 words at 150 wpm
    assert duration.confidence is not None and duration.confidence < 0.5
    assert IssueKind.UNTIMED_MATERIAL in kinds(plan)


def test_a_short_that_runs_long_says_what_the_essential_part_alone_would_be() -> None:
    segments = [seg(i, importance=0.9 if i == 1 else 0.1) for i in range(1, 9)]
    facts = asset(segments, [topic(i, i) for i in range(1, 9)])

    plan = plan_of(facts, StoryConfig("short"))

    [issue] = [i for i in plan.completeness_issues if i.kind is IssueKind.OVER_TARGET_DURATION]
    assert "essential" in issue.detail
    assert plan.estimated_duration.within_target is False
    assert any(a.label == "Essential sections only" for a in plan.alternative_structures)


def test_a_dense_section_with_nothing_to_ground_it_gets_narrative_advice() -> None:
    facts = asset([seg(1, density=0.95)], [topic(1, 1)])

    plan = plan_of(facts)

    assert any(
        n.kind is PacingKind.DENSE and n.action is PacingAction.ADD_EXAMPLE
        for n in plan.pacing_notes
    )


def test_a_long_run_of_one_kind_of_section_is_noticed() -> None:
    segments = [seg(i, roles=[("explanation", 0.8)]) for i in range(1, 6)]
    facts = asset(segments, [topic(i, i) for i in range(1, 6)])

    plan = plan_of(facts)

    [note] = [n for n in plan.pacing_notes if n.kind is PacingKind.MONOTONE_RUN]
    assert note.action is PacingAction.ADD_DEMONSTRATION


# --- visuals -------------------------------------------------------------------------------
def visual(segment: int, kind: str = "broll", category: str = "process_footage") -> VisualFacts:
    return VisualFacts(
        opportunity_id=f"o{segment}",
        kind=kind,
        category=category,
        concept="the hinge closing",
        segment_id=f"s{segment:05d}",
        strength=0.8,
        already_covered=False,
        rationale="the speaker describes a movement",
    )


def test_a_demonstration_needs_its_picture_and_known_shots_are_attached() -> None:
    match = ShotMatchFacts(
        passage_id="s00001", shot_id="shot-7", start=1.0, end=3.0, grade="indicated", rank=1
    )
    facts = asset(
        [seg(1, "Watch the hinge close.", roles=[("demonstration", 0.9)])],
        [topic(1, 1)],
        visuals=(visual(1),),
        matches=(match,),
    )

    plan = plan_of(facts)

    [opportunity] = plan.visual_opportunities
    assert opportunity.necessity is Necessity.ESSENTIAL
    assert opportunity.purpose is VisualPurpose.DEMONSTRATION
    assert opportunity.concept == "the hinge closing"  # verbatim, never invented
    assert [v.shot_id for v in opportunity.available] == ["shot-7"]
    assert plan.sections[0].visual_ids == (opportunity.visual_id,)


def test_a_passing_visual_idea_is_optional() -> None:
    facts = asset([seg(1, "It is red.")], [topic(1, 1)], visuals=(visual(1, category="location"),))

    [opportunity] = plan_of(facts).visual_opportunities

    assert opportunity.necessity is Necessity.OPTIONAL
    assert opportunity.available == ()


def test_a_demonstration_with_no_suggestion_still_gets_an_opportunity() -> None:
    facts = asset([seg(1, "Look here.", roles=[("demonstration", 0.7)])], [topic(1, 1)])

    [opportunity] = plan_of(facts).visual_opportunities

    assert opportunity.origin_opportunity == "role:demonstration"
    assert opportunity.necessity is Necessity.ESSENTIAL


# --- alternatives --------------------------------------------------------------------------
def test_an_alternative_exists_only_when_it_differs() -> None:
    plan = plan_of(asset([seg(1)], [topic(1, 1)]))

    assert plan.alternative_structures == ()


def test_the_recorded_order_is_offered_when_the_plan_moved_something() -> None:
    facts = asset(
        [seg(1, "Alpha."), seg(2, "Beta."), seg(3, "Gamma.")],
        [topic(1, 1, label="A"), topic(2, 2, label="B"), topic(3, 3, label="C")],
        relations=[rel("depends_on", 1, 3)],
    )

    plan = plan_of(facts)

    [alt] = [a for a in plan.alternative_structures if a.label == "As recorded"]
    assert alt.section_order == plan.narrative_pattern.source_order
    clarity = next(c for c in alt.criteria if c.name == "clarity")
    assert clarity.value == 0.0  # it breaks the one hard dependency, and says so
    assert validate_plan(plan) == []


def test_an_alternative_never_edits_the_recommended_plan() -> None:
    facts = asset(
        [seg(1), seg(2, hook=0.9, roles=[("hook", 0.9)])],
        [topic(1, 1), topic(2, 2)],
    )
    plan = plan_of(facts)

    again = replace(plan, alternative_structures=())

    assert again.narrative_pattern == plan.narrative_pattern
    assert again.recommended_opening == plan.recommended_opening
    assert BeatFunction.HOOK in plan.narrative_pattern.observed
