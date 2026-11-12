"""Preference memory: capture what the creator does, distil candidate rules, review them."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from media_house.core.application.ports import Clock
from media_house.modules.style_memory.application.ports import StyleStore
from media_house.modules.style_memory.domain import rulebook
from media_house.modules.style_memory.domain.learning import Support, distill, support_of
from media_house.modules.style_memory.domain.model import PreferenceEvent, StyleRule
from media_house.modules.style_memory.domain.rulebook import RuleLocked
from media_house.modules.style_memory.domain.values import Category, Scope, Status, Strength
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, Result
from media_house.shared.types import new_id


class RecordPreference:
    """Implements ``PreferenceRecorder``: store the event, then refresh the candidates."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def record(self, event: PreferenceEvent) -> None:
        self._store.append_event(event)
        DistillPreferences(self._store, self._clock).execute()


class DistillPreferences:
    """Events in, candidate rules out. Idempotent: the same events change nothing twice."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def execute(self) -> tuple[StyleRule, ...]:
        existing = self._store.list_rules()
        found = distill(self._store.list_events(), existing, now=self._clock.now())
        by_id = {r.rule_id: r for r in existing}
        changed: list[StyleRule] = []
        for rule in found:
            before = by_id.get(rule.rule_id)
            if before is None or _differs(before, rule):
                self._store.save_rule(rule)
                changed.append(rule)
        return tuple(changed)


def _differs(before: StyleRule, after: StyleRule) -> bool:
    return (before.confidence, before.evidence) != (after.confidence, after.evidence)


@dataclass(frozen=True, slots=True)
class RuleReview:
    """A rule and the evidence behind it, as the review page lists it."""

    rule: StyleRule
    support: Support | None


class ReviewRules:
    """The person's hand on the rules: approve, edit, lock, scope, delete. Nothing is automatic."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def list(self, *, status: Status | None = None) -> tuple[RuleReview, ...]:
        rules = [r for r in self._store.list_rules() if status is None or r.status is status]
        support = {s.topic: s for s in support_of(self._store.list_events())}
        return tuple(RuleReview(r, support.get(r.topic)) for r in rules)

    def approve(
        self, rule_id: str, *, strength: Strength | None = None
    ) -> Result[StyleRule, NotFoundError | ConflictError]:
        return self._change(rule_id, lambda r, now: rulebook.approve(r, now=now, strength=strength))

    def reject(self, rule_id: str) -> Result[StyleRule, NotFoundError | ConflictError]:
        return self._change(rule_id, lambda r, now: rulebook.reject(r, now=now))

    def edit(
        self,
        rule_id: str,
        *,
        text: str | None = None,
        category: Category | None = None,
        strength: Strength | None = None,
        scope: Scope | None = None,
        scope_key: str | None = None,
    ) -> Result[StyleRule, NotFoundError | ConflictError]:
        return self._change(
            rule_id,
            lambda r, now: rulebook.edit(
                r,
                now=now,
                text=text,
                category=category,
                strength=strength,
                scope=scope,
                scope_key=scope_key,
            ),
        )

    def set_locked(
        self, rule_id: str, locked: bool
    ) -> Result[StyleRule, NotFoundError | ConflictError]:
        op = rulebook.lock if locked else rulebook.unlock
        return self._change(rule_id, lambda r, now: op(r, now=now))

    def delete(self, rule_id: str) -> Result[None, NotFoundError | ConflictError]:
        rule = self._store.get_rule(rule_id)
        if rule is None:
            return Err(_missing(rule_id))
        try:
            rulebook.ensure_deletable(rule)
        except RuleLocked as exc:
            return Err(ConflictError(str(exc), user_message=exc.user_message))
        self._store.delete_rule(rule_id)
        return Ok(None)

    def add(
        self,
        *,
        text: str,
        category: Category,
        strength: Strength,
        scope: Scope,
        scope_key: str,
    ) -> StyleRule:
        rule = rulebook.write_rule(
            rule_id=f"user.{new_id()[:12]}",
            text=text,
            category=category,
            strength=strength,
            scope=scope,
            scope_key=scope_key,
            now=self._clock.now(),
        )
        self._store.save_rule(rule)
        return rule

    def _change(
        self, rule_id: str, op: Callable[[StyleRule, datetime], StyleRule]
    ) -> Result[StyleRule, NotFoundError | ConflictError]:
        rule = self._store.get_rule(rule_id)
        if rule is None:
            return Err(_missing(rule_id))
        try:
            changed = op(rule, self._clock.now())
        except RuleLocked as exc:
            return Err(ConflictError(str(exc), user_message=exc.user_message))
        self._store.save_rule(changed)
        return Ok(changed)


def _missing(rule_id: str) -> NotFoundError:
    return NotFoundError(f"No rule {rule_id}", user_message="Diese Regel gibt es nicht (mehr).")
