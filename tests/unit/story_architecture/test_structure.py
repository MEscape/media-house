"""Dependencies, ordering and the issues they raise, on hand-built facts."""

from media_house.modules.story_architecture.domain.facts import AssetFacts, RelationFacts
from media_house.modules.story_architecture.domain.model import StoryPlan
from media_house.modules.story_architecture.domain.profile import FORMATS, StoryConfig
from media_house.modules.story_architecture.domain.validation import validate_plan
from media_house.modules.story_architecture.domain.values import (
    ConstraintKind,
    IssueKind,
    LinkKind,
    LinkState,
    Strength,
)
from tests.support.story_fakes import asset, item, plan_of, rel, seg, topic


def three(*relations: RelationFacts) -> AssetFacts:
    """Sections A, B, C of one passage each, with the given relations between them."""
    return asset(
        [seg(1, "Alpha."), seg(2, "Beta."), seg(3, "Gamma.")],
        [topic(1, 1, label="A"), topic(2, 2, label="B"), topic(3, 3, label="C")],
        relations=relations,
    )


def label(plan: StoryPlan, section_id: str) -> str:
    section = plan.section(section_id)
    assert section is not None
    return section.label


def order(plan: StoryPlan) -> list[str]:
    return [label(plan, s) for s in plan.narrative_pattern.recommended_order]


def test_a_dependency_on_a_later_section_pulls_that_section_forward() -> None:
    plan = plan_of(three(rel("depends_on", 1, 3)))  # A depends on C, which is recorded last

    assert order(plan) == ["C", "A", "B"]
    change = plan.narrative_pattern.changes[0]
    assert label(plan, change.section_id) == "C"
    assert (change.from_index, change.to_index) == (2, 0)
    first = plan.section(plan.narrative_pattern.recommended_order[0])
    assert first is not None and first.requires == ()
    assert validate_plan(plan) == []


def test_untouched_sections_keep_their_recorded_order() -> None:
    plan = plan_of(three(rel("depends_on", 3, 1)))  # already in the right order

    assert order(plan) == ["A", "B", "C"]
    assert plan.narrative_pattern.changes == ()
    assert plan.narrative_pattern.source_order == plan.narrative_pattern.recommended_order


def test_a_format_that_keeps_the_recorded_order_reports_the_dependency_instead() -> None:
    plan = plan_of(three(rel("depends_on", 1, 3)), StoryConfig("interview"))

    assert order(plan) == ["A", "B", "C"]
    assert all(s.reorder_locked for s in plan.sections)
    assert any(i.kind is IssueKind.CONTEXT_REMOVAL_RISK for i in plan.completeness_issues)


def test_a_circular_dependency_is_dropped_and_reported_not_emitted() -> None:
    plan = plan_of(three(rel("depends_on", 1, 2), rel("depends_on", 2, 1)))

    hard = [
        c
        for c in plan.ordering_constraints
        if c.kind is ConstraintKind.MUST_PRECEDE and c.strength is Strength.HARD
    ]
    assert len(hard) == 1
    assert any(
        i.kind is IssueKind.DISTORTION_RISK and "depend on each other" in i.detail
        for i in plan.completeness_issues
    )
    assert validate_plan(plan) == []


def test_what_an_essential_section_needs_is_never_optional() -> None:
    facts = asset(
        [seg(1, importance=0.05), seg(2, importance=0.9)],
        [topic(1, 1, label="context"), topic(2, 2, label="main")],
        relations=[rel("depends_on", 2, 1)],
    )

    plan = plan_of(facts)

    context = next(s for s in plan.sections if s.label == "context")
    assert context.priority.value == "essential"
    assert any("needed to understand" in r for r in context.priority_reasons)


def test_a_qualification_stays_next_to_its_claim() -> None:
    facts = asset(
        [seg(1, "It is safe."), seg(2, "Unrelated."), seg(3, "Only below 40 degrees.")],
        [topic(1, 1, label="claim"), topic(2, 2, label="other"), topic(3, 3, label="limit")],
        items=[item("k1", "claim", 1, "It is safe.", qualified_by=("u000003",))],
    )

    plan = plan_of(facts)

    pairs = [
        (label(plan, c.first), label(plan, c.second))
        for c in plan.ordering_constraints
        if c.kind is ConstraintKind.KEEP_ADJACENT and c.strength is Strength.HARD
    ]
    assert pairs == [("claim", "limit")]
    # the recording separates them, and the plan says so rather than hiding it
    assert any(i.kind is IssueKind.DISTORTION_RISK for i in plan.completeness_issues)


def test_a_question_and_its_answer_are_linked_and_an_unanswered_one_is_flagged() -> None:
    facts = asset(
        [
            seg(1, "Why does it heat?", roles=[("question", 0.9)]),
            seg(2, "Because of resistance.", roles=[("answer", 0.9)]),
            seg(3, "And what about cold?", roles=[("question", 0.9)]),
        ],
        [topic(1, 1, label="a"), topic(2, 2, label="b"), topic(3, 3, label="c")],
        relations=[rel("answers", 2, 1)],
    )

    plan = plan_of(facts, StoryConfig("interview"))

    states = {
        link.setup.segment_ids[0]: link.state
        for link in plan.setup_payoff_links
        if link.kind is LinkKind.QUESTION_ANSWER
    }
    assert states == {"s00001": LinkState.RESOLVED, "s00003": LinkState.OPEN}
    assert any(i.kind is IssueKind.UNANSWERED_QUESTION for i in plan.completeness_issues)


def test_a_rhetorical_question_is_not_an_open_question() -> None:
    facts = asset(
        [seg(1, "Who knew?", roles=[("question", 0.9)], rhetoric=("rhetorical_question",))],
        [topic(1, 1)],
    )

    plan = plan_of(facts)

    assert [i for i in plan.completeness_issues if i.kind is IssueKind.UNANSWERED_QUESTION] == []
    assert plan.setup_payoff_links == ()


def test_every_format_has_a_consistent_profile() -> None:
    for profile in FORMATS.values():
        assert profile.problems() == []
        assert profile.name in FORMATS
