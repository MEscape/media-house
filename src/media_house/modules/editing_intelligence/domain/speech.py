"""Speech evidence for editing: where a sentence really starts and ends, and what surrounds it.

Reads only the facts the application mapped from audio and content analysis. A sentence time that
content analysis took from forced-aligned words is as good as the words; otherwise the extent is
marked with the weaker precision it deserves, so a cut built on it can say so.
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.editing_intelligence.domain.facts import (
    AudioFacts,
    EventFact,
    PauseFact,
    UnitFact,
    WordFact,
)
from media_house.modules.editing_intelligence.domain.timing import exact
from media_house.modules.editing_intelligence.domain.values import Precision

#: Words that carry no content on their own. Deliberately short: a missed filler stays in the
#: edit (safe), a content word mistaken for one would be cut (not safe).
FILLERS: frozenset[str] = frozenset(
    {
        "um", "uh", "uhm", "umm", "er", "erm", "hmm", "mhm", "ah", "eh",
        "äh", "ähm", "öh", "hm", "ehm",
    }
)  # fmt: skip
#: Whole sentences that only fill space ("so yeah", "okay so", "also ja").
FILLER_PHRASE_WORDS: frozenset[str] = FILLERS | {
    "so", "yeah", "yes", "okay", "ok", "well", "right", "like", "you", "know", "i", "mean",
    "also", "ja", "genau", "naja", "halt", "eben",
}  # fmt: skip
GREETING = re.compile(
    r"\b(welcome back|hey guys|hi guys|hi everyone|hello everyone|hello and welcome|"
    r"willkommen zurück|hallo zusammen|hallo und willkommen|servus)\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[\w']+", re.UNICODE)
#: Cues that the passage negates, doubts or imagines what it names.
NEGATION = re.compile(
    r"\b(not|never|no|n't|nothing|nobody|without|instead of|unlike|if|imagine|would|could|"
    r"might|nicht|nie|kein|keine|ohne|statt|wenn|stell dir vor|würde|könnte)\b",
    re.IGNORECASE,
)


def tokens(text: str) -> list[str]:
    return [t.casefold() for t in _TOKEN.findall(text)]


def is_pure_filler(text: str) -> bool:
    """The sentence holds nothing but filler words (and has at least one token)."""
    found = tokens(text)
    return bool(found) and len(found) <= 4 and all(t in FILLER_PHRASE_WORDS for t in found)


def is_greeting(text: str) -> bool:
    return GREETING.search(text) is not None


def negates(text: str) -> bool:
    return NEGATION.search(text) is not None


@dataclass(frozen=True, slots=True)
class Extent:
    """Where a sentence is actually spoken."""

    start: Fraction
    end: Fraction
    precision: Precision
    words: tuple[WordFact, ...]

    @property
    def seconds(self) -> Fraction:
        return self.end - self.start


class AudioIndex:
    """Words, pauses and events of one recording, searchable by time."""

    def __init__(self, audio: AudioFacts) -> None:
        self.audio = audio
        self._words = audio.words
        self._starts = [w.start for w in audio.words]
        self._ends = [w.end for w in audio.words]
        self._position = {w.index: i for i, w in enumerate(audio.words)}
        self._pauses_sorted = sorted(audio.pauses, key=lambda p: p.start)
        self._pause_starts = [p.start for p in self._pauses_sorted]

    def words_in(self, start: float, end: float) -> tuple[WordFact, ...]:
        """Words whose middle lies in ``[start, end)``."""
        lo = bisect_left(self._ends, start)
        out: list[WordFact] = []
        for word in self._words[lo:]:
            if word.start > end:
                break
            middle = (word.start + word.end) / 2
            if start <= middle < end:
                out.append(word)
        return tuple(out)

    def previous_end(self, moment: float) -> float:
        """End of the last word that ends at or before ``moment`` (0 when none)."""
        index = bisect_right(self._ends, moment + 1e-9) - 1
        return self._ends[index] if index >= 0 else 0.0

    def next_start(self, moment: float) -> float:
        """Start of the first word that starts at or after ``moment`` (the end when none)."""
        index = bisect_left(self._starts, moment - 1e-9)
        return self._starts[index] if index < len(self._starts) else self.audio.duration

    def pause_between(self, start: float, end: float) -> PauseFact | None:
        """The detected pause that covers the gap ``[start, end]`` best."""
        best: PauseFact | None = None
        best_overlap = 0.0
        index = max(0, bisect_left(self._pause_starts, start - 30.0))
        for pause in self._pauses_sorted[index:]:
            if pause.start > end:
                break
            overlap = min(end, pause.end) - max(start, pause.start)
            if overlap > best_overlap:
                best, best_overlap = pause, overlap
        return best

    def pauses_within(self, start: float, end: float) -> tuple[PauseFact, ...]:
        return tuple(
            p for p in self._pauses_sorted if p.start >= start - 1e-9 and p.end <= end + 1e-9
        )

    def events_between(
        self, start: float, end: float, kinds: frozenset[str]
    ) -> tuple[EventFact, ...]:
        return tuple(
            e for e in self.audio.events if e.kind in kinds and e.end > start and e.start < end
        )

    def word_before(self, word: WordFact) -> WordFact | None:
        position = self._position.get(word.index, 0)
        return self._words[position - 1] if position > 0 else None

    def word_after(self, word: WordFact) -> WordFact | None:
        position = self._position.get(word.index, len(self._words))
        return self._words[position + 1] if position + 1 < len(self._words) else None


def unit_extent(index: AudioIndex | None, unit: UnitFact) -> Extent | None:
    """The sentence's spoken extent, or ``None`` when it has no time at all."""
    if unit.start is None or unit.end is None:
        return None
    if index is not None:
        words = index.words_in(unit.start - 0.05, unit.end + 0.05)
        if words:
            precision = (
                Precision.WORD_ALIGNED if index.audio.word_level else Precision.SEGMENT_ALIGNED
            )
            return Extent(exact(words[0].start), exact(words[-1].end), precision, words)
    precision = (
        Precision.SENTENCE_SPAN if unit.time_basis in ("words", "source") else Precision.ESTIMATED
    )
    return Extent(exact(unit.start), exact(unit.end), precision, ())
