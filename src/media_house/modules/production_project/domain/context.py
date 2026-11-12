"""The pipeline context: a system prompt for every new video, in three sizes.

One structured set of items is rendered at three levels (about 60, 190 and 460 words) so the
three can never contradict each other. Every level carries the topic and facts, the idea and red
thread, look and pacing rules, the hard rules, the asset logic, and what the engine may decide
freely. A person may edit each text; the word count is always shown.

The evaluation (``score``) compares the three on the same assets and excerpt: automatic checks
fill what is measurable, the creator rates the rest, and the winner is learned per format.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from media_house.modules.production_project.domain.script_tools import words

LEVELS: tuple[str, ...] = ("minimal", "normal", "detailed")
BUDGET: dict[str, int] = {"minimal": 60, "normal": 190, "detailed": 460}
#: A level may run this much over its budget before items are dropped.
_SLACK = 1.12


@dataclass(frozen=True, slots=True)
class ContextItem:
    section: str
    text: str
    #: The smallest level that carries the item: 1 = all, 2 = normal and detailed, 3 = detailed.
    level: int
    #: Lower = more important: the last to be dropped when a level is over budget.
    priority: int = 5


@dataclass(frozen=True, slots=True)
class ContextInputs:
    title: str
    client: str
    format_title: str
    products: tuple[tuple[str, str], ...]
    facts: tuple[str, ...]
    logline: str
    hook: str
    callback: str
    retention: tuple[str, ...]
    cuts_per_minute: tuple[float, float]
    overlay_limit: int
    allowed_transitions: tuple[str, ...]
    forbidden_transitions: tuple[str, ...]
    caption_note: str
    layouts: tuple[str, ...]
    #: ``(short label, full text)`` per hard rule: the label for small contexts, the text for big.
    hard_rules: tuple[tuple[str, str], ...]
    soft_rules: tuple[str, ...]
    never: tuple[str, ...]
    #: ``(role, what it is, scenes)`` per requirement.
    assets: tuple[tuple[str, str, str], ...]
    #: The learned and written style rules that apply (their texts).
    style_rules: tuple[str, ...] = ()


_SECTIONS: tuple[tuple[str, str], ...] = (
    ("topic", "Thema"),
    ("idea", "Idee"),
    ("look", "Look"),
    ("rules", "Regeln"),
    ("assets", "Material"),
    ("freedom", "Freiheit"),
)


_BRACES = re.compile(r"\{[^}]*\}")


def clean(text: str) -> str:
    """Text for a context: placeholders nobody filled and bracket notes taken out."""
    plain = re.sub(r"\s*\([^)]*\)", "", _BRACES.sub("", text))
    return " ".join(plain.split()).strip(" :;,-")


def _cut(text: str, count: int) -> str:
    parts = text.split()
    return text if len(parts) <= count else " ".join(parts[:count]).rstrip(",.:;") + " …"


def items_of(i: ContextInputs) -> tuple[ContextItem, ...]:
    out: list[ContextItem] = []

    def add(section: str, text: str, level: int, priority: int = 5) -> None:
        if text.strip():
            out.append(ContextItem(section, text.strip(), level, priority))

    n = len(i.products)
    add("topic", f"{i.title}, {i.format_title}: {n} Produkte.", 1, 1)
    add("topic", "Zahlen nur aus der Faktentabelle.", 1, 1)
    add("topic", "Produkte und Preise: " + "; ".join(f"{a} {b}" for a, b in i.products) + ".", 2, 2)
    for fact in i.facts[:8]:
        add("topic", fact, 3, 4)
    add("idea", _cut(clean(i.logline), 12) or "Roter Faden aus dem freigegebenen Script.", 1, 1)
    if hook := clean(i.hook):
        add("idea", f"Hook: {_cut(hook, 22)}", 2, 2)
    add("idea", f"Bogen zum Anfang: {_cut(clean(i.callback), 18)}", 2, 3)
    for mechanic in i.retention:
        add("idea", f"Zuschauerbindung: {mechanic}", 3, 4)
    low, high = i.cuts_per_minute
    add("look", f"Tempo {low:g}-{high:g} Schnitte/min, max. {i.overlay_limit} Overlays.", 1, 1)
    add("look", "Erlaubte Übergänge: " + ", ".join(i.allowed_transitions) + ".", 2, 3)
    add("look", "Verboten: " + ", ".join(i.forbidden_transitions) + ".", 2, 2)
    add("look", i.caption_note, 2, 4)
    for layout in i.layouts:
        add("look", f"Typisches Layout: {layout}.", 3, 5)
    labels = [label for label, _ in i.hard_rules]
    add("rules", "; ".join(labels[:3]) + "." if labels else "Briefingregeln einhalten.", 1, 1)
    if labels[3:]:
        add(
            "rules",
            "Alle weiteren Briefingregeln bleiben verbindlich; offene Angaben nicht erfinden.",
            2,
            2,
        )
    for full in dict.fromkeys(full for _label, full in i.hard_rules):
        add("rules", full, 3, 3)
    for rule in i.soft_rules:
        add("rules", f"Wunsch: {rule}", 3, 4)
    for rule in i.never:
        add("rules", f"Nie: {rule}.", 3, 3)
    for rule in i.style_rules:
        add("rules", f"Stil: {rule}", 3, 4)
    add("assets", "Talking-Takes nach Szenen zuordnen; Rollen laut manifest.json.", 1, 1)
    add("assets", "Jede Datei nur für ihre Rolle nutzen.", 2, 1)
    for role, what, scenes in i.assets:
        add("assets", f"{role}: {what}" + (f" (Szenen {scenes})" if scenes else "") + ".", 3, 4)
    add("freedom", "Frei: Schnitt, Tempo, Grafik, Ton. Fest: Zahlen, Skript.", 1, 1)
    add("freedom", "Bei Zweifel weniger Effekte, nicht mehr.", 2, 3)
    add("freedom", "Gutes Material nicht zerstören: lieber ruhig als überladen.", 3, 4)
    add("freedom", "Musik, Effekte und Schnittfolge entscheidest du innerhalb der Regeln.", 2, 2)
    return tuple(out)


def render(items: Sequence[ContextItem], level: str) -> str:
    """The text of one level: its items grouped by section, over-budget details dropped last
    to first (most detailed and least important first)."""
    rank = LEVELS.index(level) + 1
    chosen = [it for it in items if it.level <= rank]
    limit = int(BUDGET[level] * _SLACK)
    while sum(words(it.text) for it in chosen) > limit and len(chosen) > 1:
        victim = max(range(len(chosen)), key=lambda k: (chosen[k].priority, chosen[k].level, k))
        if chosen[victim].priority <= 1:
            break
        del chosen[victim]
    lines: list[str] = []
    for key, title in _SECTIONS:
        body = [it.text for it in chosen if it.section == key]
        if body:
            lines.append(f"{title}: " + " ".join(body))
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ContextVariant:
    level: str
    text: str
    word_count: int
    #: A person edited the text after it was generated.
    edited: bool = False


def variants(inputs: ContextInputs) -> tuple[ContextVariant, ...]:
    items = items_of(inputs)
    out = []
    for level in LEVELS:
        text = render(items, level)
        out.append(ContextVariant(level, text, words(text)))
    return tuple(out)


def edit_variant(variant: ContextVariant, text: str) -> ContextVariant:
    return replace(variant, text=text, word_count=words(text), edited=True)


# --- evaluation -------------------------------------------------------------------------------


class Criterion(StrEnum):
    HOOK = "hook"
    FIDELITY = "fidelity"
    NUMBERS = "numbers"
    GRAPHICS = "graphics"
    RHYTHM = "rhythm"
    ASSETS = "assets"
    RULES = "rules"


#: How much each criterion counts; a wrong number or a broken rule matters most.
WEIGHTS: dict[Criterion, float] = {
    Criterion.HOOK: 1.0,
    Criterion.FIDELITY: 1.5,
    Criterion.NUMBERS: 2.0,
    Criterion.GRAPHICS: 1.0,
    Criterion.RHYTHM: 1.0,
    Criterion.ASSETS: 1.5,
    Criterion.RULES: 2.0,
}
#: What only a person can judge.
MANUAL: frozenset[Criterion] = frozenset({Criterion.GRAPHICS})


@dataclass(frozen=True, slots=True)
class RunMeasurements:
    """What the run measured about its own result (``None`` = not measurable here)."""

    hook_seconds: float | None = None
    script_fidelity: float | None = None
    numbers_correct: float | None = None
    overlay_max: int | None = None
    cuts_per_minute: float | None = None
    asset_mapping: float | None = None
    rule_violations: int | None = None


@dataclass(frozen=True, slots=True)
class Score:
    criterion: Criterion
    #: 1 to 5.
    value: float
    #: ``auto`` (measured) or ``manual`` (rated by the creator).
    method: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class EvalRun:
    run_id: str
    variant: str
    #: ``excerpt`` (the first ~75 s) or ``full``.
    scope: str
    scores: tuple[Score, ...]
    notes: str
    at: datetime


def auto_scores(
    m: RunMeasurements, *, cuts: tuple[float, float], overlay_limit: int
) -> tuple[Score, ...]:
    out: list[Score] = []
    if m.hook_seconds is not None:
        out.append(
            Score(
                Criterion.HOOK,
                _ladder(m.hook_seconds, (5, 8, 12, 20), descending=True),
                "auto",
                f"Hook nach {m.hook_seconds:.1f} s",
            )
        )
    if m.script_fidelity is not None:
        out.append(
            Score(
                Criterion.FIDELITY,
                _ladder(m.script_fidelity, (0.9, 0.8, 0.7, 0.55)),
                "auto",
                f"{m.script_fidelity:.0%} des Skripts getroffen",
            )
        )
    if m.numbers_correct is not None:
        value = 5.0 if m.numbers_correct >= 1.0 else 3.0 if m.numbers_correct >= 0.97 else 1.0
        out.append(
            Score(Criterion.NUMBERS, value, "auto", f"{m.numbers_correct:.0%} der Zahlen stimmen")
        )
    if m.overlay_max is not None and overlay_limit:
        within = m.overlay_max <= overlay_limit
        out.append(
            Score(
                Criterion.GRAPHICS,
                5.0 if within else 2.0,
                "auto",
                f"höchstens {m.overlay_max} Overlays gleichzeitig (Limit {overlay_limit})",
            )
        )
    if m.cuts_per_minute is not None:
        out.append(
            Score(
                Criterion.RHYTHM,
                _rhythm(m.cuts_per_minute, cuts),
                "auto",
                f"{m.cuts_per_minute:.1f} Schnitte/min",
            )
        )
    if m.asset_mapping is not None:
        out.append(
            Score(
                Criterion.ASSETS,
                _ladder(m.asset_mapping, (0.95, 0.85, 0.7, 0.5)),
                "auto",
                f"{m.asset_mapping:.0%} der Dateien richtig zugeordnet",
            )
        )
    if m.rule_violations is not None:
        out.append(
            Score(
                Criterion.RULES,
                5.0 if m.rule_violations == 0 else 3.0 if m.rule_violations == 1 else 1.0,
                "auto",
                f"{m.rule_violations} Regelverstöße",
            )
        )
    return tuple(out)


def _ladder(value: float, steps: tuple[float, ...], *, descending: bool = False) -> float:
    """5 at the first step, one less per step missed (``descending``: smaller is better)."""
    for k, step in enumerate(steps):
        if (value <= step) if descending else (value >= step):
            return 5.0 - k
    return 1.0


def _rhythm(value: float, band: tuple[float, float]) -> float:
    low, high = band
    if low <= value <= high:
        return 5.0
    gap = (low - value) / low if value < low else (value - high) / high
    return 4.0 if gap <= 0.25 else 3.0 if gap <= 0.5 else 2.0 if gap <= 1.0 else 1.0


def merge_ratings(auto: Sequence[Score], manual: Sequence[Score]) -> tuple[Score, ...]:
    """The creator's rating of a criterion is averaged with the measured one (graphics: the
    overlay limit is measured, legibility is his)."""
    by_crit: dict[Criterion, list[Score]] = {}
    for s in (*auto, *manual):
        by_crit.setdefault(s.criterion, []).append(s)
    merged: list[Score] = []
    for crit, group in by_crit.items():
        value = sum(s.value for s in group) / len(group)
        method = "auto+manual" if len({s.method for s in group}) > 1 else group[0].method
        merged.append(
            Score(crit, round(value, 2), method, "; ".join(s.note for s in group if s.note))
        )
    return tuple(merged)


def complete(run: EvalRun) -> bool:
    """Every criterion has a score (the manual ones too)."""
    return {s.criterion for s in run.scores} >= set(Criterion)


def total(run: EvalRun) -> float:
    """The weighted mean over the scored criteria, 1 to 5."""
    weight = sum(WEIGHTS[s.criterion] for s in run.scores)
    return (
        round(sum(s.value * WEIGHTS[s.criterion] for s in run.scores) / weight, 3)
        if weight
        else 0.0
    )


def winner(runs: Sequence[EvalRun]) -> str | None:
    """The best level of one scope, only when all three were run and rated completely. A tie goes
    to the shorter text (the cheaper prompt)."""
    by_level = {r.variant: r for r in runs}
    if set(by_level) != set(LEVELS) or not all(complete(r) for r in by_level.values()):
        return None
    return max(LEVELS, key=lambda lv: (total(by_level[lv]), -LEVELS.index(lv)))


def learned_best_level(history: Sequence[tuple[str, str, float]]) -> dict[str, str]:
    """Per format, the level that won most often (ties: the higher mean score)."""
    wins: dict[str, dict[str, list[float]]] = {}
    for fmt, level, score in history:
        wins.setdefault(fmt, {}).setdefault(level, []).append(score)
    return {
        fmt: max(levels, key=lambda lv: (len(levels[lv]), sum(levels[lv]) / len(levels[lv])))
        for fmt, levels in wins.items()
    }
