"""Grouping shown words into readable caption units, line breaks and display times.

Words are first bound into ATOMS that are never split: a recognised fact (price "19,99 €",
rank "Platz 3", spec "25 Schuss", product name) and a number with the unit after it. A dynamic
programme then chooses the group boundaries with the lowest cost:

* hard limits: words per caption, characters per line x lines (at the look's case), the
  longest caption; a pause >= ``pause_split``, a sentence end (when preferred), a speaker
  change and a clip change with a gap always break;
* soft costs: a line ending on an article/preposition/conjunction, a break inside a phrase
  (commas and conjunctions are preferred), uneven group sizes, captions shorter than readable.

Times: a caption appears ``lead_in`` before its first word and lingers ``linger`` after the
last one, is stretched to ``min_duration`` and to the reading speed where the next caption
leaves room, and never overlaps the next one (overlapping speech: ``trim`` or ``drop_later``).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.modules.caption_engine.domain.profile import Segmentation
from media_house.modules.caption_engine.domain.text import (
    DANGLING,
    PHRASE_OPENERS,
    bare,
    ends_phrase,
    shown,
)

_INF = math.inf


@dataclass(frozen=True, slots=True, kw_only=True)
class Token:
    """One shown word as segmentation sees it (timeline seconds)."""

    key: int
    text: str
    start: Fraction
    end: Fraction
    speaker: str | None
    clip_id: str
    sentence_id: str
    sentence_end: bool
    #: Tokens with the same atom id are never split across captions or lines.
    atom: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Group:
    tokens: tuple[Token, ...]
    lines: tuple[str, ...]
    start: Fraction
    end: Fraction


@dataclass(frozen=True, slots=True, kw_only=True)
class Note:
    """A segmentation finding (turned into a QC issue by the caller)."""

    code: str
    message: str
    start: Fraction
    end: Fraction
    keys: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Segmented:
    groups: tuple[Group, ...]
    #: Token keys that could not be shown (overlapping speech).
    dropped: tuple[int, ...]
    notes: tuple[Note, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    max_chars: int
    max_lines: int
    case: str


def _atoms(tokens: Sequence[Token]) -> list[list[Token]]:
    out: list[list[Token]] = []
    for t in tokens:
        if out and out[-1][-1].atom == t.atom:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _text(tokens: Sequence[Token], case: str) -> str:
    return shown(" ".join(t.text for t in tokens), case)


def wrap(atoms: Sequence[Sequence[Token]], limits: Limits) -> tuple[str, ...] | None:
    """The most even line break at atom boundaries (``None`` = does not fit)."""
    whole = _text([t for a in atoms for t in a], limits.case)
    if len(whole) <= limits.max_chars:
        return (whole,)
    best: tuple[tuple[float, int], tuple[str, ...]] | None = None
    n = len(atoms)

    def consider(cuts: tuple[int, ...]) -> None:
        nonlocal best
        bounds = (0, *cuts, n)
        lines = tuple(
            _text([t for a in atoms[bounds[i] : bounds[i + 1]] for t in a], limits.case)
            for i in range(len(bounds) - 1)
        )
        if any(len(line) > limits.max_chars for line in lines):
            return
        dangling = sum(1 for c in cuts if bare(atoms[c - 1][-1].text) in DANGLING)
        # Prefer even lines; a dangling word at a line end costs as much as 6 characters.
        cost = (max(len(line) for line in lines) + 6.0 * dangling, -len(lines[-1]))
        if best is None or cost < best[0]:
            best = (cost, lines)

    if limits.max_lines >= 2:
        for a in range(1, n):
            consider((a,))
    if limits.max_lines >= 3 and best is None:
        for a in range(1, n - 1):
            for b in range(a + 1, n):
                consider((a, b))
    return best[1] if best is not None else None


def _forced_break(prev: Token, nxt: Token, rules: Segmentation) -> bool:
    if nxt.start - prev.end >= Fraction(rules.pause_split).limit_denominator(1000):
        return True
    if rules.sentence_boundaries and prev.sentence_end:
        return True
    if rules.break_on_speaker and prev.speaker != nxt.speaker:
        return True
    return prev.clip_id != nxt.clip_id and nxt.start > prev.end


def _break_cost(prev: Token, nxt: Token | None, rules: Segmentation) -> float:
    if nxt is None or prev.sentence_end:
        return 0.0
    if bare(prev.text) in DANGLING:
        return 6.0
    if rules.phrase_boundaries and (ends_phrase(prev.text) or bare(nxt.text) in PHRASE_OPENERS):
        return 0.5
    if ends_phrase(prev.text):
        return 0.5
    return 3.0


def _plan_run(
    tokens: Sequence[Token], rules: Segmentation, limits: Limits
) -> tuple[list[Group], list[Note]]:
    """Lowest-cost grouping of one run of consecutive shown words."""
    atoms = _atoms(tokens)
    n = len(atoms)
    cost = [_INF] * (n + 1)
    back = [0] * (n + 1)
    lines_at: dict[tuple[int, int], tuple[str, ...]] = {}
    cost[0] = 0.0
    max_span = Fraction(rules.max_duration).limit_denominator(1000)
    target = max(1.0, rules.max_words * 0.75)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            chunk = atoms[i:j]
            words = sum(len(a) for a in chunk)
            single = j - i == 1
            if i < j - 1 and _forced_break(atoms[i][-1], atoms[i + 1][0], rules):
                break  # a forced break inside the chunk: every longer chunk has it too
            if words > rules.max_words and not single:
                break
            first, last = chunk[0][0], chunk[-1][-1]
            if last.end - first.start > max_span and not single:
                break
            lines = wrap(chunk, limits)
            if lines is None:
                if not single:
                    break
                lines = (_text(chunk[0], limits.case),)
            if cost[i] == _INF:
                continue
            nxt = atoms[j][0] if j < n else None
            spoken = float(last.end - first.start) + rules.linger
            short = max(0.0, rules.min_duration - spoken)
            c = (
                cost[i]
                + 1.0
                + _break_cost(last, nxt, rules)
                + 2.0 * ((target - words) / target) ** 2
                + 2.0 * short
            )
            if c < cost[j]:
                cost[j], back[j] = c, i
                lines_at[(i, j)] = lines
    groups: list[Group] = []
    notes: list[Note] = []
    j = n
    while j > 0:
        i = back[j]
        chunk = atoms[i:j]
        flat = tuple(t for a in chunk for t in a)
        lines = lines_at[(i, j)]
        if any(len(line) > limits.max_chars for line in lines) or len(lines) > limits.max_lines:
            notes.append(
                Note(
                    code="line.atom_too_long",
                    message=f"{' '.join(t.text for t in flat)!r} cannot be split and exceeds"
                    f" {limits.max_lines} x {limits.max_chars} characters",
                    start=flat[0].start,
                    end=flat[-1].end,
                    keys=tuple(t.key for t in flat),
                )
            )
        groups.append(Group(tokens=flat, lines=lines, start=flat[0].start, end=flat[-1].end))
        j = i
    groups.reverse()
    return groups, notes


def segment(
    runs: Sequence[Sequence[Token]],
    rules: Segmentation,
    limits: Limits,
    tail: Fraction = Fraction(0),
    until: Fraction | None = None,
) -> Segmented:
    """Group every run, then time all groups together (runs may interleave in time).

    ``tail``: time the exit animation needs after ``linger`` (the words stay fully visible).
    ``until``: the end of the timeline; no caption is held past it."""
    groups: list[Group] = []
    notes: list[Note] = []
    for run in runs:
        if run:
            g, n = _plan_run(run, rules, limits)
            groups += g
            notes += n
    groups.sort(key=lambda g: (g.start, g.tokens[0].key))
    timed, dropped, timing_notes = _time(groups, rules, limits, tail, until)
    return Segmented(groups=tuple(timed), dropped=tuple(dropped), notes=(*notes, *timing_notes))


def _sec(value: float) -> Fraction:
    return Fraction(value).limit_denominator(1000)


def _time(
    groups: list[Group],
    rules: Segmentation,
    limits: Limits,
    tail: Fraction,
    until: Fraction | None = None,
) -> tuple[list[Group], list[int], list[Note]]:
    lead, linger, gap = _sec(rules.lead_in), _sec(rules.linger), _sec(rules.min_gap)
    shortest, longest = _sec(rules.min_duration), _sec(rules.max_duration)
    notes: list[Note] = []
    dropped: list[int] = []
    # Overlapping speech first: a caption may not start before the previous one's words end.
    kept: list[Group] = []
    for g in groups:
        prev = kept[-1] if kept else None
        if prev is not None and g.start < prev.tokens[-1].end:
            if rules.overlap == "drop_later":
                dropped += [t.key for t in g.tokens]
                notes.append(
                    Note(
                        code="timing.overlap_dropped",
                        message="overlapping speech: the later caption is not shown",
                        start=g.start,
                        end=g.end,
                        keys=tuple(t.key for t in g.tokens),
                    )
                )
                continue
            keep = tuple(t for t in prev.tokens if t.start < g.start)
            lost = [t.key for t in prev.tokens if t.start >= g.start]
            notes.append(
                Note(
                    code="timing.overlap_trimmed",
                    message="overlapping speech: the earlier caption ends when the next starts",
                    start=g.start,
                    end=prev.end,
                    keys=tuple(lost),
                )
            )
            dropped += lost
            kept.pop()
            if keep:
                atoms = _atoms(keep)
                lines = wrap(atoms, limits) or (_text(keep, limits.case),)
                kept.append(replace(prev, tokens=keep, lines=lines, end=min(prev.end, g.start)))
        kept.append(g)
    out: list[Group] = []
    for k, g in enumerate(kept):
        floor = out[-1].end + gap if out else Fraction(0)
        start = max(g.tokens[0].start - lead, floor, Fraction(0))
        start = min(start, g.tokens[0].start)
        nxt = kept[k + 1].tokens[0].start - lead if k + 1 < len(kept) else None
        ceiling = nxt - gap if nxt is not None else None
        spoken_end = min(g.end, g.tokens[-1].end)
        chars = sum(len(line) for line in g.lines)
        wanted = max(
            spoken_end + linger + tail,
            start + shortest,
            start + Fraction(chars) / _sec(rules.reading_cps),
        )
        wanted = min(wanted, max(start + longest, spoken_end))
        end = wanted if ceiling is None else max(min(wanted, ceiling), spoken_end)
        if g.end < g.tokens[-1].end:  # trimmed for overlapping speech
            end = g.end
        if until is not None:  # the linger / reading time never runs past the edit
            end = max(min(end, until), start)
        out.append(replace(g, start=start, end=end))
    return out, dropped, notes
