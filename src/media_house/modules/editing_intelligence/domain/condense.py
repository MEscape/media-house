"""A condensed short: the whole story told in its strongest phrases, not one stretch of it.

A list video (a ranking, a brochure, a haul, an unboxing) is a run of items. Its short keeps the
story's thread: the opener's first line as the hook, the sentence that announces how many items
follow (the premise), every item in story order compressed to its strongest phrases (the one that
states its rank, price or a spec, plus the line that names it), then the verdict (the closing
payoff and the question it answers) and a short call to action. Every phrase is a whole kept
sentence that starts and ends cleanly, so the cuts fall on phrase boundaries.

Items are the sections that state a rank (``Platz 3``) when at least two do; otherwise the longest
block of consecutive sections that each state a price, rank or spec. A section opening on a
caveat ("but there are rules") qualifies what came before and is no item of its own.

The phrases are fitted to the wanted range by dropping the lowest-priority phrase first; the hook
and each item's strongest phrase are never dropped. No items found: nothing (the caller falls
back to the contiguous highlight). The same plan always gives the same short.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.modules.editing_intelligence.domain.assemble import Piece
from media_house.modules.editing_intelligence.domain.facts import AudioFacts, StoryFacts
from media_house.modules.editing_intelligence.domain.model import CutPoint, Decision, ShortPart
from media_house.modules.editing_intelligence.domain.timing import exact, to_rational
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import Action

#: A short pause the edit keeps between two sentences (seconds), added to the spoken length.
_GAP = Fraction(15, 100)
#: A longer sentence is an explanation, not a phrase.
_PHRASE = Fraction(8)
_HOOK = Fraction(5)
_CTA_MAX = Fraction(4)
_SPECS = frozenset({"shots", "effects", "height", "duration", "caliber", "weight"})
_ITEM_FACTS = _SPECS | {"price", "rank"}
_VERDICT = frozenset({"payoff", "turning_point", "conclusion", "resolution"})
_CTA = re.compile(
    r"\b(link|beschreibung|abo|abonn\w*|kommentar\w*|subscribe|description|comment\w*|follow)\b",
    re.IGNORECASE,
)
_COUNTS: Mapping[int, tuple[str, ...]] = {
    2: ("zwei", "two"), 3: ("drei", "three"), 4: ("vier", "four"), 5: ("fünf", "five"),
    6: ("sechs", "six"), 7: ("sieben", "seven"), 8: ("acht", "eight"), 9: ("neun", "nine"),
    10: ("zehn", "ten"), 11: ("elf", "eleven"), 12: ("zwölf", "twelve"),
}  # fmt: skip

# Priorities: higher is kept longer when the short is too long.
_MUST = 100.0
_ANSWER, _NAME, _PREMISE, _QUESTION, _CTA_P, _HOOK_MORE, _LEAD_IN, _FILL = (
    5.0, 4.0, 3.0, 2.5, 2.0, 1.8, 1.5, 1.0,
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class _Pick:
    unit: EditUnit
    priority: float


def condensed_cut(
    units: Sequence[EditUnit],
    decisions: Mapping[str, Decision],
    story: StoryFacts,
    low: float,
    high: float,
) -> tuple[tuple[EditUnit, ...], tuple[ShortPart, ...]]:
    """The hook, premise, every item's strongest phrases and the verdict, ``low``-``high`` s,
    and the story parts the short must carry (each marked present or not after fitting)."""
    kept = [
        u
        for u in sorted(units, key=lambda u: u.order)
        if u.extent is not None and decisions[u.decision_id].action is not Action.CUT
    ]
    sections = list(dict.fromkeys(u.section_id for u in kept))
    phrases = {
        s: [u for u in kept if u.section_id == s and _phrase(u) and not u.pure_filler]
        for s in sections
    }
    items = _items(sections[1:], phrases, kept)
    if not items:
        return (), ()
    picks: dict[str, _Pick] = {}
    #: Each required part: its section and the phrase that carries it (none: nothing can).
    parts: dict[str, tuple[str, EditUnit | None]] = {}

    def pick(unit: EditUnit | None, priority: float, part: str = "") -> None:
        if unit is None:
            return
        if unit.decision_id not in picks:
            picks[unit.decision_id] = _Pick(unit, priority)
        if part:
            parts.setdefault(part, (unit.section_id, unit))

    hook, total = phrases[sections[0]], Fraction(0)
    for i, u in enumerate(hook):
        total += _seconds(u)
        if i and total > _HOOK:
            break
        pick(u, _MUST if i == 0 else _HOOK_MORE, "hook" if i == 0 else "")
    first, last = sections.index(items[0]), sections.index(items[-1])
    pick(
        _premise(len(items), [u for s in sections[1:first] for u in phrases[s]]),
        _PREMISE,
        "premise",
    )
    for s in items:
        ranked = sorted(
            (u for u in phrases[s] if _item_facts(u)), key=lambda u: (-_strength(u), u.order)
        )
        if not ranked:
            ranked = phrases[s][:1]
        parts[f"item:{s}"] = (s, ranked[0] if ranked else None)  # every item is required
        pick(ranked[0] if ranked else None, _MUST)
        lead = phrases[s][0] if phrases[s] else None
        if lead is not None and lead.decision_id not in picks:
            pick(lead, _NAME)
        else:  # the strongest phrase names the item: add the next one with a new kind of fact
            seen = set(_item_facts(ranked[0])) if ranked else set()
            pick(next((u for u in ranked if set(_item_facts(u)) - seen), None), _NAME)
        for u in ranked:
            pick(u, _FILL)
    closing = sections[last + 1 :]
    answer = _verdict(closing, phrases, story, pick)
    after = [
        u
        for s in closing
        for u in phrases[s]
        if (answer is None or u.order > answer.order) and _CTA.search(u.unit.text)
    ]
    short = [u for u in after if _seconds(u) <= _CTA_MAX]
    pick(min(short, key=lambda u: (_seconds(u), u.order)) if short else None, _CTA_P, "cta")
    chosen = _fit(list(picks.values()), Fraction(str(low)), Fraction(str(high)))
    kept_ids = {u.decision_id for u in chosen}
    return chosen, tuple(
        ShortPart(
            part=name,
            section_id=section,
            label=found.label if (found := story.section(section)) else "",
            present=unit is not None and unit.decision_id in kept_ids,
        )
        for name, (section, unit) in parts.items()
    )


def _items(
    candidates: Sequence[str], phrases: Mapping[str, Sequence[EditUnit]], kept: Sequence[EditUnit]
) -> list[str]:
    ranked = [s for s in candidates if any("rank" in u.unit.facts for u in phrases[s])]
    if len(ranked) >= 2:
        return ranked
    opens = {s: next(u for u in kept if u.section_id == s) for s in candidates}
    best: list[str] = []
    run: list[str] = []
    for s in candidates:
        if opens[s].function != "caveat" and any(_item_facts(u) for u in phrases[s]):
            run.append(s)
            if len(run) > len(best):
                best = list(run)
        else:
            run = []
    return best if len(best) >= 2 else []


def _premise(count: int, setup: Sequence[EditUnit]) -> EditUnit | None:
    """The earliest setup sentence that announces how many items follow ("five new ...")."""
    words = _COUNTS.get(count, ())
    pattern = re.compile(rf"\b({count}|{'|'.join(words) or str(count)})\b", re.IGNORECASE)
    return next(
        (
            u
            for u in setup
            if u.unit.words >= 3 and _seconds(u) <= _HOOK and pattern.search(u.unit.text)
        ),
        None,
    )


def _verdict(
    closing: Sequence[str],
    phrases: Mapping[str, Sequence[EditUnit]],
    story: StoryFacts,
    pick: Callable[[EditUnit | None, float, str], None],
) -> EditUnit | None:
    """The last closing section with a payoff, its first phrase, and the question it answers."""
    section = next(
        (s for s in reversed(closing) if any(u.function in _VERDICT for u in phrases[s])), None
    )
    if section is None:
        return None
    answer = next((u for u in phrases[section] if not _CTA.search(u.unit.text)), None)
    pick(answer, _ANSWER, "verdict")
    for need in story.contexts:
        if need.section_id != section or need.requires_section_id not in phrases:
            continue
        asked = phrases[need.requires_section_id]
        if asked:
            pick(asked[0], _QUESTION, "")
            pick(asked[-1], _LEAD_IN, "")
    return answer


def _fit(picks: list[_Pick], low: Fraction, high: Fraction) -> tuple[EditUnit, ...]:
    """Drop the lowest-priority phrases (the longest first) until the short fits ``high``; fill
    phrases join only while the short is still under ``low``."""
    core = [p for p in picks if p.priority > _FILL]
    fill = sorted((p for p in picks if p.priority <= _FILL), key=lambda p: p.unit.order)
    for p in fill:
        if _length(core) >= low:
            break
        if _length([*core, p]) <= high:
            core.append(p)
    droppable = sorted(
        (p for p in core if p.priority < _MUST),
        key=lambda p: (p.priority, -_seconds(p.unit), -p.unit.order),
    )
    for p in droppable:
        if _length(core) <= high:
            break
        core.remove(p)
    return tuple(sorted((p.unit for p in core), key=lambda u: u.order))


def _length(picks: Sequence[_Pick]) -> Fraction:
    spoken = sum((_seconds(p.unit) for p in picks), Fraction(0))
    return spoken + _GAP * max(len(picks) - 1, 0)


def _seconds(u: EditUnit) -> Fraction:
    return u.extent.seconds if u.extent is not None else Fraction(0)


def _phrase(u: EditUnit) -> bool:
    return u.unit.starts_clean and u.unit.ends_clean and _seconds(u) <= _PHRASE


def _item_facts(u: EditUnit) -> tuple[str, ...]:
    return tuple(f for f in u.unit.facts if f in _ITEM_FACTS)


def _strength(u: EditUnit) -> int:
    """Rank first, then price, then how many kinds of spec the phrase states."""
    kinds = set(_item_facts(u))
    return 4 * ("rank" in kinds) + 2 * ("price" in kinds) + len(kinds & _SPECS)


#: A Short may open on the sound of the action before its first word (an alarm, a bang).
_LEAD = Fraction(4)
#: Sound this close to the first word is the word's own onset, not a lead-in.
_ONSET = Fraction(3, 10)
_AUDIBLE = frozenset({"impulse", "laughter", "scream", "cry", "music", "noise", "energy_spike"})
#: A gap before the first word that is mostly not silence holds something to hear.
_SOUNDING = 0.5


def hook_lead_in(piece: Piece, audio: AudioFacts | None) -> Piece:
    """``piece`` opened up to 4 s earlier when audible non-speech precedes its first word.

    Audible: a measured non-speech event (impulse, laughter, music, noise, an energy spike) or a
    gap the audio analysis found mostly not silent. Never reaches back over earlier speech. No
    audio analysis: the piece as it is."""
    first = piece.units[0].extent if piece.units else None
    if audio is None or first is None:
        return piece
    word = first.start
    spoken = [exact(w.end) for w in audio.words if exact(w.end) <= word - _ONSET]
    floor = max(word - _LEAD, max(spoken, default=Fraction(0)), Fraction(0))
    sounds = [
        exact(e.start)
        for e in audio.events
        if e.kind in _AUDIBLE and exact(e.end) > floor and exact(e.start) < word - _ONSET
    ] + [
        exact(p.start)
        for p in audio.pauses
        if p.silence_ratio is not None
        and p.silence_ratio < _SOUNDING
        and exact(p.end) > floor
        and exact(p.start) < word - _ONSET
    ]
    start = max(floor, min(sounds, default=piece.start))
    if start >= piece.start:
        return piece
    cut = CutPoint(
        time=to_rational(start),
        precision=piece.start_precision,
        confidence=piece.cut_in.confidence if piece.cut_in else None,
        reason="opens on the sound before the first word (the hook's action)",
    )
    return replace(piece, start=start, cut_in=cut)
