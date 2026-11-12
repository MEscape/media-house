"""Distilling preference events into candidate rules (deterministic, explainable).

A candidate needs evidence: one explicit statement of the person (a regenerate instruction, a
reason for an override) or several consistent acts. It is never applied and never ``mandatory``
until a person approves it. The same events always give the same candidates.
"""

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from media_house.modules.style_memory.domain.model import PreferenceEvent, Provenance, StyleRule
from media_house.modules.style_memory.domain.values import (
    SCOPE_RANK,
    Category,
    EventKind,
    Origin,
    Scope,
    Status,
    Strength,
)

#: A person's own words weigh double an accepted suggestion.
_EXPLICIT: frozenset[EventKind] = frozenset(
    {EventKind.REGENERATE_INSTRUCTION, EventKind.CONFLICT_OVERRIDE, EventKind.MANUAL_EDIT}
)
_POSITIVE: frozenset[EventKind] = frozenset(
    {
        EventKind.SUGGESTION_ACCEPTED,
        EventKind.CONCEPT_PICKED,
        EventKind.SCENE_ADDED,
        EventKind.REGENERATE_INSTRUCTION,
        EventKind.CONFLICT_OVERRIDE,
        EventKind.MANUAL_EDIT,
    }
)
_NEGATIVE: frozenset[EventKind] = frozenset(
    {EventKind.SUGGESTION_REJECTED, EventKind.SCENE_DELETED}
)
_MIN_SUPPORT = 2.0
# fmt: off
_STOP: frozenset[str] = frozenset(
    {
        "a", "aber", "als", "am", "an", "and", "are", "auch",
        "auf", "be", "bei", "das", "dem", "den", "der", "des",
        "die", "ein", "eine", "einem", "einen", "einer", "for", "für",
        "im", "in", "is", "ist", "kein", "keine", "mal", "mehr",
        "mit", "muss", "must", "nicht", "noch", "nur", "oder", "of",
        "on", "or", "should", "sind", "soll", "sollte", "the", "to",
        "und", "von", "werden", "wie", "wird", "with", "zu", "zum",
        "zur",
    }
)
# fmt: on


def topic_key(signal: str, note: str) -> str:
    """The key events about the same thing share: the signal, else a content-word signature."""
    if signal.strip():
        return _slug(signal)
    words = [w for w in _words(note) if w not in _STOP and len(w) > 2]
    return "-".join(sorted(set(words))[:6])


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", folded)


def _slug(text: str) -> str:
    return "-".join(_words(text))


@dataclass(frozen=True, slots=True)
class Support:
    """What the events of one topic add up to (kept for the review page)."""

    topic: str
    support: float
    against: float
    projects: int
    events: int


def _weight(event: PreferenceEvent) -> float:
    return 2.0 if event.kind in _EXPLICIT else 1.0


def _polarity(event: PreferenceEvent) -> int:
    if event.kind in {EventKind.AB_RATING, EventKind.FINAL_RATING}:
        if event.rating >= 4:
            return 1
        if 0 < event.rating <= 2:
            return -1
        return 0
    if event.kind in _POSITIVE:
        return 1
    if event.kind in _NEGATIVE:
        return -1
    return 0


def distill(
    events: Iterable[PreferenceEvent],
    existing: Sequence[StyleRule],
    *,
    now: datetime,
) -> tuple[StyleRule, ...]:
    """New candidate rules, and existing learned rules updated with fresh evidence.

    A topic a person rejected is never proposed again; a locked rule is never touched; a rule a
    person edited keeps his text (only its evidence and confidence grow).
    """
    by_topic: dict[str, list[PreferenceEvent]] = defaultdict(list)
    for event in events:
        key = topic_key(event.signal, event.note)
        if key and _polarity(event):
            by_topic[key].append(event)
    known = {r.topic: r for r in existing if r.topic}
    out: list[StyleRule] = []
    for topic in sorted(by_topic):
        group = sorted(by_topic[topic], key=lambda e: (e.at, e.event_id))
        support = sum(_weight(e) for e in group if _polarity(e) > 0)
        against = sum(_weight(e) for e in group if _polarity(e) < 0)
        projects = len({e.project_id for e in group if e.project_id})
        current = known.get(topic)
        if support < _MIN_SUPPORT or support < 2 * against:
            continue
        if current is not None and (current.status is Status.REJECTED or current.locked):
            continue
        confidence = _confidence(support, against, projects)
        evidence = tuple(
            Provenance(f"event:{e.event_id}", e.note or e.signal or e.kind.value) for e in group
        )
        if current is not None:
            if current.origin is not Origin.LEARNED:
                continue  # a hand-written rule on the same topic already says it
            out.append(replace(current, confidence=confidence, evidence=evidence, updated_at=now))
            continue
        out.append(_candidate(topic, group, confidence, evidence, now))
    return tuple(out)


def support_of(events: Iterable[PreferenceEvent]) -> tuple[Support, ...]:
    by_topic: dict[str, list[PreferenceEvent]] = defaultdict(list)
    for event in events:
        key = topic_key(event.signal, event.note)
        if key:
            by_topic[key].append(event)
    return tuple(
        Support(
            topic,
            sum(_weight(e) for e in group if _polarity(e) > 0),
            sum(_weight(e) for e in group if _polarity(e) < 0),
            len({e.project_id for e in group if e.project_id}),
            len(group),
        )
        for topic, group in sorted(by_topic.items())
    )


def _confidence(support: float, against: float, projects: int) -> float:
    base = 1.0 - 1.0 / (1.0 + support)
    agreement = support / (support + against) if support + against else 0.0
    spread = 0.8 + 0.2 * min(1.0, projects / 2.0)
    return round(min(1.0, base * agreement * spread), 2)


def _candidate(
    topic: str,
    group: list[PreferenceEvent],
    confidence: float,
    evidence: tuple[Provenance, ...],
    now: datetime,
) -> StyleRule:
    explicit = [e for e in group if e.kind in _EXPLICIT and e.note.strip()]
    spoken = (
        explicit[-1] if explicit else next((e for e in reversed(group) if e.note.strip()), None)
    )
    text = spoken.note.strip() if spoken else topic.replace("-", " ")
    scope = _scope(group)
    key = _scope_key(group, scope)
    if scope is not Scope.GLOBAL and not key:
        scope = (
            Scope.GLOBAL
        )  # an event without the client / format it was about says nothing narrower
    return StyleRule(
        rule_id=f"learned.{topic}",
        text=text,
        category=_category(group),
        strength=Strength.SUGGESTION,
        scope=scope,
        scope_key=key if scope is not Scope.GLOBAL else "",
        origin=Origin.LEARNED,
        status=Status.CANDIDATE,
        confidence=confidence,
        evidence=evidence,
        approved=False,
        locked=False,
        topic=topic,
        created_at=now,
        updated_at=now,
    )


def _category(group: list[PreferenceEvent]) -> Category:
    counts: dict[Category, int] = defaultdict(int)
    for e in group:
        counts[e.category] += 1
    return max(counts, key=lambda c: (counts[c], c.value))


def _scope(group: list[PreferenceEvent]) -> Scope:
    """The narrowest scope anybody asked for; widened to global once three projects agree."""
    narrowest = max((e.scope_hint for e in group), key=lambda s: SCOPE_RANK[s])
    if len({e.project_id for e in group if e.project_id}) >= 3:
        return Scope.GLOBAL
    return narrowest


def _scope_key(group: list[PreferenceEvent], scope: Scope) -> str:
    match scope:
        case Scope.GLOBAL:
            return ""
        case Scope.FORMAT:
            return next((e.format for e in reversed(group) if e.format), "")
        case Scope.CLIENT:
            return next((e.client for e in reversed(group) if e.client), "")
        case Scope.PROJECT:
            return next((e.project_id for e in reversed(group) if e.project_id), "")


def explain(support: Support) -> str:
    return (
        f"{support.events} Ereignisse in {support.projects} Projekt(en): "
        f"{math.floor(support.support)} dafür, {math.floor(support.against)} dagegen"
    )
