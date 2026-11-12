"""Rules, learning and reference translation: pure, deterministic, nothing learned is binding."""

from dataclasses import replace
from datetime import timedelta

import pytest

from media_house.modules.style_memory.domain.learning import distill, topic_key
from media_house.modules.style_memory.domain.model import (
    PacingStats,
    PreferenceEvent,
    ReferenceCard,
    StyleRule,
)
from media_house.modules.style_memory.domain.references import Verdict, translate
from media_house.modules.style_memory.domain.rulebook import (
    RuleLocked,
    approve,
    edit,
    lock,
    select_rules,
    write_rule,
)
from media_house.modules.style_memory.domain.seed import (
    manual_session_events,
    mesc_profile,
    mesc_rules,
)
from media_house.modules.style_memory.domain.values import (
    Category,
    EventKind,
    Origin,
    Scope,
    Status,
    Strength,
    Task,
)
from media_house.shared.errors import InvariantViolation
from tests.support.fakes import T0


def _event(
    signal: str,
    note: str,
    kind: EventKind = EventKind.REGENERATE_INSTRUCTION,
    *,
    project: str = "p1",
    n: int = 0,
    rating: int = 0,
    scope: Scope = Scope.GLOBAL,
    client: str = "",
) -> PreferenceEvent:
    return PreferenceEvent(
        event_id=f"{signal}-{project}-{n}",
        kind=kind,
        at=T0 + timedelta(minutes=n),
        project_id=project,
        scene_id="",
        rule_id="",
        format="ranking",
        client=client,
        category=Category.VOICE,
        signal=signal,
        note=note,
        rating=rating,
        scope_hint=scope,
    )


def test_one_explicit_instruction_makes_a_candidate_that_is_never_applied() -> None:
    found = distill([_event("intro_fliesst", "Intro muss aus dem Kontext fließen")], [], now=T0)

    (rule,) = found
    assert rule.status is Status.CANDIDATE and not rule.applies
    assert rule.origin is Origin.LEARNED and rule.strength is Strength.SUGGESTION
    assert rule.text == "Intro muss aus dem Kontext fließen"
    assert rule.evidence[0].source == "event:intro_fliesst-p1-0"


def test_one_accepted_suggestion_alone_is_not_enough() -> None:
    event = _event("x", "", EventKind.SUGGESTION_ACCEPTED)

    assert distill([event], [], now=T0) == ()
    assert len(distill([event, replace(event, event_id="x2")], [], now=T0)) == 1


def test_more_rejections_than_support_blocks_the_candidate() -> None:
    events = [
        _event("a", "mehr Humor", n=0),
        _event("a", "", EventKind.SUGGESTION_REJECTED, n=1),
        _event("a", "", EventKind.SUGGESTION_REJECTED, n=2),
    ]

    assert distill(events, [], now=T0) == ()


def test_distilling_twice_gives_the_same_candidates() -> None:
    events = list(manual_session_events(T0))

    assert distill(events, [], now=T0) == distill(events, [], now=T0)
    assert len(distill(events, [], now=T0)) == len(events)


def test_a_rejected_or_locked_topic_is_not_proposed_again() -> None:
    (rule,) = distill([_event("a", "mehr Humor")], [], now=T0)
    rejected = replace(rule, status=Status.REJECTED)
    locked = replace(rule, locked=True)
    more = [_event("a", "mehr Humor", n=1), _event("a", "mehr Humor", n=2)]

    assert distill(more, [rejected], now=T0) == ()
    assert distill(more, [locked], now=T0) == ()


def test_scope_stays_narrow_until_three_projects_agree() -> None:
    one = distill([_event("a", "x", scope=Scope.CLIENT, client="Acme")], [], now=T0)[0]
    assert (one.scope, one.scope_key) == (Scope.CLIENT, "Acme")
    no_client = distill([_event("b", "y", scope=Scope.CLIENT)], [], now=T0)[0]
    assert no_client.scope is Scope.GLOBAL
    three = [_event("c", "z", project=f"p{i}", scope=Scope.PROJECT) for i in range(3)]
    assert distill(three, [], now=T0)[0].scope is Scope.GLOBAL


def test_topic_key_uses_the_signal_else_content_words() -> None:
    assert topic_key("Intro fließt!", "egal") == "intro-fliesst"
    assert topic_key("", "Das Intro muss fließen") == topic_key("", "Intro fließen muss das")


def test_a_learned_rule_cannot_be_mandatory_without_approval() -> None:
    (rule,) = distill([_event("a", "mehr Humor")], [], now=T0)

    with pytest.raises(InvariantViolation):
        replace(rule, strength=Strength.MANDATORY)
    approved = approve(rule, now=T0, strength=Strength.MANDATORY)
    assert approved.applies and approved.strength is Strength.MANDATORY


def test_a_locked_rule_refuses_every_change() -> None:
    rule = lock(
        write_rule(
            rule_id="r",
            text="x",
            category=Category.VOICE,
            strength=Strength.DEFAULT,
            scope=Scope.GLOBAL,
            scope_key="",
            now=T0,
        ),
        now=T0,
    )

    with pytest.raises(RuleLocked):
        edit(rule, now=T0, text="y")


def test_selection_orders_by_strength_then_specificity_and_dedupes_topics() -> None:
    def rule(
        rule_id: str, strength: Strength, scope: Scope, key: str, topic: str = ""
    ) -> StyleRule:
        return replace(
            write_rule(
                rule_id=rule_id,
                text=rule_id,
                category=Category.VOICE,
                strength=strength,
                scope=scope,
                scope_key=key,
                now=T0,
            ),
            topic=topic,
        )

    rules = [
        rule("g-default", Strength.DEFAULT, Scope.GLOBAL, ""),
        rule("g-must", Strength.MANDATORY, Scope.GLOBAL, ""),
        rule("c-default", Strength.DEFAULT, Scope.CLIENT, "acme"),
        rule("other-client", Strength.MANDATORY, Scope.CLIENT, "zed"),
        rule("t-global", Strength.DEFAULT, Scope.GLOBAL, "", topic="t"),
        rule("t-client", Strength.DEFAULT, Scope.CLIENT, "acme", topic="t"),
    ]

    chosen = select_rules(rules, task=Task.SCRIPT, format="ranking", client="acme", project_id="p")

    assert [r.rule_id for r in chosen] == ["g-must", "c-default", "t-client", "g-default"]


def test_task_selects_only_relevant_categories() -> None:
    rules = mesc_rules(T0)

    plan = select_rules(rules, task=Task.ASSET_PLAN, format="ranking", client="", project_id="")

    assert {r.category for r in plan} <= {Category.ASSETS, Category.NEVER}
    assert any(r.rule_id == "seed.assets.raw_footage_only" for r in plan)


def test_seed_rules_name_where_they_come_from() -> None:
    assert all(r.evidence and r.evidence[0].source for r in mesc_rules(T0))
    assert all(p.evidence for p in mesc_profile(T0).hook_patterns)


def test_a_reference_that_uses_a_forbidden_device_is_a_conflict_with_alternatives() -> None:
    profile = mesc_profile(T0)
    card = ReferenceCard(
        "c",
        "Seltix",
        "https://example.test",
        PacingStats(12.0, 6.0, 150.0, 2.0),
        ("Glitch transitions", "jump cuts", "spinning vortex"),
        T0,
    )

    result = translate(card, profile, format="ranking")

    by_name = {t.technique: t for t in result.techniques}
    glitch = by_name["Glitch transitions"]
    assert glitch.verdict is Verdict.CONFLICT and "Text-Pop-in" in glitch.instead
    assert by_name["jump cuts"].verdict is Verdict.ALLOWED
    assert by_name["spinning vortex"].verdict is Verdict.UNKNOWN
    cuts = result.pacing[0]
    assert cuts.verdict is Verdict.CONFLICT and cuts.adopted == 6.0
    assert len(result.conflicts) == 3
