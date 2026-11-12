"""An in-memory ``StyleStore`` for the unit tests (the contract test runs it beside SQLite)."""

from collections.abc import Sequence

from media_house.modules.style_memory.domain.model import (
    Exemplar,
    PreferenceEvent,
    ReferenceCard,
    StyleProfile,
    StyleRule,
)


class InMemoryStyleStore:
    def __init__(self) -> None:
        self._profiles: dict[int, StyleProfile] = {}
        self._rules: dict[str, StyleRule] = {}
        self._events: dict[str, PreferenceEvent] = {}
        self._exemplars: dict[str, Exemplar] = {}
        self._cards: dict[str, ReferenceCard] = {}

    def load_profile(self) -> StyleProfile | None:
        return self._profiles[max(self._profiles)] if self._profiles else None

    def save_profile(self, profile: StyleProfile) -> None:
        self._profiles[profile.version] = profile

    def profile_versions(self) -> Sequence[int]:
        return sorted(self._profiles)

    def load_profile_version(self, version: int) -> StyleProfile | None:
        return self._profiles.get(version)

    def list_rules(self) -> Sequence[StyleRule]:
        return [self._rules[k] for k in sorted(self._rules)]

    def get_rule(self, rule_id: str) -> StyleRule | None:
        return self._rules.get(rule_id)

    def save_rule(self, rule: StyleRule) -> None:
        self._rules[rule.rule_id] = rule

    def delete_rule(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    def append_event(self, event: PreferenceEvent) -> None:
        self._events.setdefault(event.event_id, event)

    def list_events(self) -> Sequence[PreferenceEvent]:
        return list(self._events.values())

    def list_exemplars(self) -> Sequence[Exemplar]:
        return [self._exemplars[k] for k in sorted(self._exemplars)]

    def save_exemplar(self, exemplar: Exemplar) -> None:
        self._exemplars[exemplar.exemplar_id] = exemplar

    def delete_exemplar(self, exemplar_id: str) -> None:
        self._exemplars.pop(exemplar_id, None)

    def list_cards(self) -> Sequence[ReferenceCard]:
        return [self._cards[k] for k in sorted(self._cards)]

    def save_card(self, card: ReferenceCard) -> None:
        self._cards[card.card_id] = card
