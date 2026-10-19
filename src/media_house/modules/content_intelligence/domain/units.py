# ruff: noqa: RUF001
"""Transcript -> sentences ("units") with source references and honest timing.

A unit is a verbatim slice of one upstream segment's text. Its time comes from the words inside
it when the transcript has word times (``WORDS`` or ``UPSTREAM_ESTIMATE``), from the segment
itself when the unit is the whole segment (``SOURCE``), and is interpolated proportionally to
characters otherwise (``INTERPOLATED``). Nothing is invented: unit text is never altered.
"""

import itertools
import re
from dataclasses import dataclass
from typing import Final

from media_house.modules.content_intelligence.domain.config import Thresholds
from media_house.modules.content_intelligence.domain.source import (
    SourceSegment,
    SourceTranscript,
    SourceWord,
)
from media_house.modules.content_intelligence.domain.span import Span
from media_house.modules.content_intelligence.domain.values import SpeechTiming, TimeBasis

_ABBREVIATIONS: Final = frozenset(
    {
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "prof.",
        "sr.",
        "jr.",
        "vs.",
        "etc.",
        "e.g.",
        "i.e.",
        "st.",
        "approx.",
        "fig.",
        "inc.",
        "ltd.",
        "z.b.",
        "d.h.",
        "u.a.",
        "usw.",
        "bzw.",
        "ca.",
        "nr.",
        "vgl.",
        "ggf.",
        "evtl.",
        "sog.",
        "bspw.",
        "inkl.",
        "zzgl.",
        "o.ä.",
        "z.t.",
    }
)
_CLOSERS: Final = "\"')]}”’»"
_STRONG: Final = "!?…"
_CJK_END: Final = "。！？"
_PAUSE_SPLIT_SECONDS: Final = 0.6
_WORD = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class Unit:
    """One sentence-like statement and where it came from."""

    unit_id: str
    index: int
    segment_id: int
    #: Verbatim text (surrounding whitespace removed).
    text: str
    #: Character range inside the upstream segment's text.
    char_range: tuple[int, int]
    span: Span
    speaker: str | None
    #: Silence before the unit in seconds, when both neighbours are timed (evidence, not a cut).
    gap_before: float | None
    #: ``False`` when the unit visibly begins/ends in the middle of a sentence.
    starts_clean: bool
    ends_clean: bool

    @property
    def words(self) -> int:
        return len(_WORD.findall(self.text))


def unit_id(index: int) -> str:
    return f"u{index:06d}"


# --- sentence boundaries ---------------------------------------------------------------------
def split_sentences(text: str, language: str | None) -> list[tuple[int, int]]:
    """Character ranges of sentences in ``text`` (whitespace trimmed, never empty)."""
    ranges: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        end: int | None = None
        if ch in _CJK_END:
            end = i + 1
        elif ch in ".!?…":
            j = i
            while j + 1 < n and (text[j + 1] in ".!?…" or text[j + 1] in _CLOSERS):
                j += 1
            end = j + 1
            if end < n and not text[end].isspace():
                end = None  # "3.5", "e.g.", "file.txt": not a sentence end
            elif (
                ch == "." and j == i and not _sentence_end_after_dot(text, start, i, end, language)
            ):
                end = None
            i = j
        if end is not None:
            _append(ranges, text, start, end)
            start = end
        i += 1
    _append(ranges, text, start, n)
    return ranges


def _append(ranges: list[tuple[int, int]], text: str, start: int, end: int) -> None:
    chunk = text[start:end]
    stripped = chunk.strip()
    if not stripped:
        return
    left = start + (len(chunk) - len(chunk.lstrip()))
    ranges.append((left, left + len(stripped)))


def _sentence_end_after_dot(
    text: str, start: int, dot: int, after: int, language: str | None
) -> bool:
    previous = text[start : dot + 1].split()
    token = previous[-1].casefold() if previous else ""
    if token in _ABBREVIATIONS:
        return False
    bare = token.rstrip(".")
    if len(bare) == 1 and bare.isalpha():
        return False  # an initial: "J. Smith"
    following = text[after:].lstrip()
    if not following:
        return True
    if token[:-1].isdigit() and language == "de":
        return False  # German ordinal: "am 3. Mai"
    return not following[0].islower()


# --- splitting and merging -------------------------------------------------------------------
def _word_count(text: str, start: int, end: int) -> int:
    return len(_WORD.findall(text[start:end]))


def _split_long(
    text: str,
    ranges: list[tuple[int, int]],
    pauses: list[int],
    max_words: int,
) -> list[tuple[int, int]]:
    """Split sentences above ``max_words`` at a pause, else a clause mark, else whitespace."""
    out: list[tuple[int, int]] = []
    pending = list(ranges)
    while pending:
        start, end = pending.pop(0)
        if _word_count(text, start, end) <= max_words:
            out.append((start, end))
            continue
        cut = _best_cut(text, start, end, pauses)
        if cut is None:
            out.append((start, end))
            continue
        pending[:0] = [(start, cut), (cut, end)]
    return [(s, e) for s, e in ((_trim(text, a, b)) for a, b in out) if e > s]


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    chunk = text[start:end]
    left = start + (len(chunk) - len(chunk.lstrip()))
    return left, left + len(chunk.strip())


def _best_cut(text: str, start: int, end: int, pauses: list[int]) -> int | None:
    middle = (start + end) / 2
    inside = [p for p in pauses if start + 10 < p < end - 10]
    clauses = [m.end() for m in re.finditer(r"[,;:—–]\s", text[start:end])]
    clauses = [start + c for c in clauses if start + 10 < start + c < end - 10]
    spaces = [start + m.end() for m in re.finditer(r"\s+", text[start:end])]
    spaces = [s for s in spaces if start + 10 < s < end - 10]
    for candidates in (inside, clauses, spaces):
        if candidates:
            return min(candidates, key=lambda c: abs(c - middle))
    return None


def _merge_fragments(text: str, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Fold one-word fragments ("Yes." "Well,") into the sentence after them (else before)."""
    merged: list[tuple[int, int]] = []
    carry: tuple[int, int] | None = None
    for start, end in ranges:
        if carry is not None:
            start = carry[0]
            carry = None
        if _word_count(text, start, end) < 2 and len(ranges) > 1:
            carry = (start, end)
            continue
        merged.append((start, end))
    if carry is not None:
        if merged:
            merged[-1] = (merged[-1][0], carry[1])
        else:
            merged.append(carry)
    return merged


# --- timing ----------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Located:
    start: int
    end: int
    word: SourceWord


def _locate_words(segment: SourceSegment) -> list[_Located] | None:
    """Where each word sits in the segment text; ``None`` unless nearly all words are found."""
    if not segment.words:
        return None
    found: list[_Located] = []
    cursor = 0
    text = segment.text
    for word in segment.words:
        token = word.text.strip()
        if not token:
            continue
        at = text.find(token, cursor)
        if at < 0:
            continue
        found.append(_Located(at, at + len(token), word))
        cursor = at + len(token)
    return found if len(found) >= 0.8 * len(segment.words) else None


def _word_basis(word: SourceWord) -> TimeBasis:
    return TimeBasis.WORDS if word.timing is SpeechTiming.ALIGNED else TimeBasis.UPSTREAM_ESTIMATE


def _unit_span(
    segment: SourceSegment, located: list[_Located] | None, start: int, end: int
) -> Span:
    if located:
        inside = [w for w in located if w.start >= start - 1 and w.end <= end + 1]
        if inside:
            first, last = inside[0].word, inside[-1].word
            return Span(first.start, last.end, _word_basis(first), _word_basis(last))
    if segment.start is None or segment.end is None:
        return Span.missing()
    length = max(1, len(segment.text))
    first_char = len(segment.text) - len(segment.text.lstrip())
    last_char = len(segment.text.rstrip())
    whole_start, whole_end = start <= first_char, end >= last_char
    duration = segment.end - segment.start
    begin = (
        segment.start
        if whole_start
        else min(segment.end, segment.start + duration * start / length)
    )
    finish = segment.end if whole_end else max(begin, segment.start + duration * end / length)
    return Span(
        begin,
        finish,
        TimeBasis.SOURCE if whole_start else TimeBasis.INTERPOLATED,
        TimeBasis.SOURCE if whole_end else TimeBasis.INTERPOLATED,
    )


def _pause_offsets(located: list[_Located] | None) -> list[int]:
    if not located:
        return []
    return [
        b.start
        for a, b in itertools.pairwise(located)
        if b.word.start - a.word.end >= _PAUSE_SPLIT_SECONDS
    ]


# --- public ---------------------------------------------------------------------------------
def build_units(
    source: SourceTranscript, thresholds: Thresholds, language: str | None
) -> tuple[Unit, ...]:
    """All sentences of the transcript in order, with spans, silence and cleanliness flags."""
    drafts: list[tuple[SourceSegment, tuple[int, int], Span]] = []
    for segment in source.segments:
        text = segment.text
        if not text.strip():
            continue
        located = _locate_words(segment)
        ranges = split_sentences(text, language)
        ranges = _split_long(text, ranges, _pause_offsets(located), thresholds.max_sentence_words)
        ranges = _merge_fragments(text, ranges)
        drafts.extend((segment, r, _unit_span(segment, located, *r)) for r in ranges)

    ends = [_ends_sentence(s.text[r[0] : r[1]]) for s, r, _ in drafts]
    punctuated = bool(ends) and sum(ends) / len(ends) >= 0.3
    units: list[Unit] = []
    for index, (segment, rng, span) in enumerate(drafts):
        text = segment.text[rng[0] : rng[1]]
        previous = units[-1] if units else None
        gap = (
            max(0.0, span.start - previous.span.end)
            if previous and span.start is not None and previous.span.end is not None
            else None
        )
        starts_clean = (
            not punctuated
            or previous is None
            or (previous.segment_id == segment.id and previous.ends_clean)
            or (
                previous.segment_id != segment.id
                and (previous.ends_clean or text[:1].isupper() or not text[:1].isalpha())
            )
        )
        units.append(
            Unit(
                unit_id=unit_id(index),
                index=index,
                segment_id=segment.id,
                text=text,
                char_range=rng,
                span=span,
                speaker=segment.speaker,
                gap_before=gap,
                starts_clean=starts_clean,
                ends_clean=(not punctuated) or ends[index],
            )
        )
    return tuple(units)


def _ends_sentence(text: str) -> bool:
    return text.rstrip(_CLOSERS + " ")[-1:] in {".", "!", "?", "…", "。", "！", "？"}
