"""The review actions a person has on a rule, and which rules apply to a task.

Learned rules are only ever proposals until a person approves them. Nothing here overrides a
brief constraint: the rules of this module are style, a brief constraint always wins, and the
consumer that holds both (the project's compliance engine) enforces that order.
"""

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime

from media_house.modules.style_memory.domain.model import Provenance, StyleRule
from media_house.modules.style_memory.domain.values import (
    SCOPE_RANK,
    STRENGTH_RANK,
    TASK_CATEGORIES,
    Category,
    Origin,
    Scope,
    Status,
    Strength,
    Task,
)
from media_house.shared.errors import DomainError


class RuleLocked(DomainError):
    code = "style_memory.rule_locked"

    def __init__(self, rule_id: str) -> None:
        super().__init__(
            f"Rule {rule_id} is locked",
            user_message="Die Regel ist gesperrt. Entsperre sie, bevor du sie änderst.",
        )


def write_rule(
    *,
    rule_id: str,
    text: str,
    category: Category,
    strength: Strength,
    scope: Scope,
    scope_key: str,
    now: datetime,
    evidence: tuple[Provenance, ...] = (),
    note: str = "",
) -> StyleRule:
    """A rule a person wrote: active at once, as binding as he made it."""
    return StyleRule(
        rule_id=rule_id,
        text=text.strip(),
        category=category,
        strength=strength,
        scope=scope,
        scope_key=scope_key,
        origin=Origin.USER,
        status=Status.ACTIVE,
        confidence=1.0,
        evidence=evidence,
        approved=True,
        locked=False,
        topic="",
        created_at=now,
        updated_at=now,
        note=note,
    )


def approve(rule: StyleRule, *, now: datetime, strength: Strength | None = None) -> StyleRule:
    """A person accepts a candidate. ``strength`` lets him make it ``mandatory`` on the spot."""
    _unlocked(rule)
    chosen = strength or (
        rule.strength if rule.strength is not Strength.MANDATORY else Strength.DEFAULT
    )
    return replace(rule, status=Status.ACTIVE, approved=True, strength=chosen, updated_at=now)


def reject(rule: StyleRule, *, now: datetime) -> StyleRule:
    _unlocked(rule)
    return replace(rule, status=Status.REJECTED, updated_at=now)


def edit(
    rule: StyleRule,
    *,
    now: datetime,
    text: str | None = None,
    category: Category | None = None,
    strength: Strength | None = None,
    scope: Scope | None = None,
    scope_key: str | None = None,
) -> StyleRule:
    """Change a rule. A learned rule only becomes mandatory through ``approve``."""
    _unlocked(rule)
    return replace(
        rule,
        text=rule.text if text is None else text.strip(),
        category=rule.category if category is None else category,
        strength=rule.strength if strength is None else strength,
        scope=rule.scope if scope is None else scope,
        scope_key=rule.scope_key if scope_key is None else scope_key,
        updated_at=now,
    )


def lock(rule: StyleRule, *, now: datetime) -> StyleRule:
    return replace(rule, locked=True, updated_at=now)


def unlock(rule: StyleRule, *, now: datetime) -> StyleRule:
    return replace(rule, locked=False, updated_at=now)


def ensure_deletable(rule: StyleRule) -> None:
    _unlocked(rule)


def _unlocked(rule: StyleRule) -> None:
    if rule.locked:
        raise RuleLocked(rule.rule_id)


def matches(rule: StyleRule, *, format: str, client: str, project_id: str) -> bool:  # noqa: A002
    """Whether the rule's scope covers this project."""
    match rule.scope:
        case Scope.GLOBAL:
            return True
        case Scope.FORMAT:
            return rule.scope_key == format
        case Scope.CLIENT:
            return rule.scope_key == client
        case Scope.PROJECT:
            return rule.scope_key == project_id


def select_rules(
    rules: Iterable[StyleRule],
    *,
    task: Task,
    format: str,  # noqa: A002
    client: str,
    project_id: str,
    limit: int = 24,
) -> tuple[StyleRule, ...]:
    """The active rules for one generation step, binding ones first, the most specific scope
    first, then the most confident. Of two rules about one topic only the most specific stays."""
    wanted = TASK_CATEGORIES[task]
    pool = [
        r
        for r in rules
        if r.applies
        and r.category in wanted
        and matches(r, format=format, client=client, project_id=project_id)
    ]
    pool.sort(
        key=lambda r: (
            STRENGTH_RANK[r.strength],
            -SCOPE_RANK[r.scope],
            -r.confidence,
            r.rule_id,
        )
    )
    seen: set[str] = set()
    chosen: list[StyleRule] = []
    for rule in pool:
        if rule.topic:
            if rule.topic in seen:
                continue
            seen.add(rule.topic)
        chosen.append(rule)
    return tuple(chosen[:limit])


def candidates(rules: Sequence[StyleRule]) -> tuple[StyleRule, ...]:
    return tuple(r for r in rules if r.status is Status.CANDIDATE)
