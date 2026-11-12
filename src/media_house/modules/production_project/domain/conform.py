"""Relate what was recorded to the script: which scene each spoken segment belongs to, the order
of the recordings, and which segments speak a sentence the script excludes.

The presenter paraphrases, so matching is a bag of content words (Dice coefficient), not exact
text. A segment is only EXCLUDED when it clearly matches an excluded sentence (``exclude_at``);
a weak match never removes speech. Pure and deterministic: no I/O.
"""

import re
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.production_project.domain.script import (
    Exclusion,
    Script,
    ScriptSentence,
)

_WORD = re.compile(r"[a-z0-9]+")
#: A slot's name is not speech: a recording that says "Kaliber 30 mm" filled the slot.
_SLOT = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
#: Frequent German / English function words: they match everything and prove nothing.
_STOP = frozenset(
    {
        "und",
        "oder",
        "aber",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "ich",
        "du",
        "er",
        "sie",
        "es",
        "wir",
        "ihr",
        "ist",
        "sind",
        "war",
        "hab",
        "habe",
        "hat",
        "haben",
        "bin",
        "bist",
        "auch",
        "noch",
        "nur",
        "schon",
        "mal",
        "dann",
        "denn",
        "doch",
        "ja",
        "nein",
        "nicht",
        "mit",
        "von",
        "vor",
        "fur",
        "auf",
        "aus",
        "bei",
        "bis",
        "im",
        "in",
        "an",
        "am",
        "zum",
        "zur",
        "um",
        "so",
        "wie",
        "was",
        "wer",
        "wo",
        "da",
        "hier",
        "jetzt",
        "dass",
        "the",
        "a",
        "and",
        "or",
        "but",
        "of",
        "to",
        "on",
        "at",
        "for",
        "is",
        "are",
        "be",
        "it",
        "this",
        "that",
        "you",
        "we",
        "they",
        "with",
        "as",
    }
)


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    asset_id: str
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class SegmentMatch:
    line: TranscriptLine
    scene_id: str | None
    sentence_index: int | None
    score: float
    exclusion: Exclusion

    @property
    def excluded(self) -> bool:
        return self.exclusion is not Exclusion.NONE


@dataclass(frozen=True, slots=True)
class Take:
    """Speech that is not the take to use: a retake, a false start, or a line that contradicts
    the script it speaks (``reason`` says which)."""

    line: TranscriptLine
    reason: str


@dataclass(frozen=True, slots=True)
class Conformance:
    #: Recordings ordered by the scene they speak (unmatched recordings last, by name order given).
    order: tuple[str, ...]
    matches: tuple[SegmentMatch, ...]
    #: Recordings in which no segment matched the script (gags, reactions, silent footage).
    unplaced: tuple[str, ...]
    #: Script scenes no recording speaks.
    missing_scenes: tuple[str, ...]
    #: Abandoned takes and lines that contradict the script, in recording order.
    rejected: tuple[Take, ...] = ()

    @property
    def excluded(self) -> tuple[SegmentMatch, ...]:
        return tuple(m for m in self.matches if m.excluded)


def tokens(text: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss"))
    ascii_text = folded.encode("ascii", "ignore").decode()
    return frozenset(w for w in _WORD.findall(ascii_text) if w not in _STOP and len(w) > 1)


def dice(a: frozenset[str], b: frozenset[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def conform(
    script: Script,
    lines: Sequence[TranscriptLine],
    asset_order: Sequence[str],
    *,
    match_at: float = 0.3,
    exclude_at: float = 0.5,
) -> Conformance:
    sentences = [(s, tokens(_SLOT.sub(" ", s.text))) for s in script.sentences]
    order_of = {scene.scene_id: scene.order for scene in script.scenes}
    matches: list[SegmentMatch] = []
    for line in lines:
        words = tokens(line.text)
        best: ScriptSentence | None = None
        best_words: frozenset[str] = frozenset()
        score = 0.0
        for sentence, sentence_words in sentences:
            value = dice(words, sentence_words)
            if value > score:
                best, best_words, score = sentence, sentence_words, value
        if best is None or score < match_at:
            matches.append(SegmentMatch(line, None, None, round(score, 3), Exclusion.NONE))
            continue
        # Removing speech needs more than similarity: the segment must say most of the sentence.
        shared = len(words & best_words)
        covers = shared >= min(3, len(best_words)) and shared / len(best_words) >= 0.5
        exclusion = best.exclusion if score >= exclude_at and covers else Exclusion.NONE
        matches.append(SegmentMatch(line, best.scene_id, best.index, round(score, 3), exclusion))

    position: dict[str, float] = {}
    for asset_id in asset_order:
        # A recording is where its clear matches are: a weak match to a far scene (a shared word
        # like "Speditionskosten") must not pull a whole recording forward. Weight = score².
        placed = [
            (order_of[m.scene_id] + (m.sentence_index or 0) / 1000, m.score**2)
            for m in matches
            if m.line.asset_id == asset_id and m.scene_id is not None
        ]
        if placed:
            position[asset_id] = _weighted_median(placed)
    rank = {a: i for i, a in enumerate(asset_order)}
    order = tuple(sorted(position, key=lambda a: (position[a], rank[a])))
    unplaced = tuple(a for a in asset_order if a not in position)
    spoken = {m.scene_id for m in matches if m.scene_id is not None}
    missing = tuple(s.scene_id for s in script.scenes if s.sentences and s.scene_id not in spoken)
    by_index = {(s.scene_id, s.index): s for s in script.sentences}
    contradicting = [
        Take(m.line, f"contradicts the script ({why})")
        for m in matches
        if m.scene_id is not None
        and m.sentence_index is not None
        and m.score >= exclude_at
        and not m.excluded
        and (why := contradiction(m.line.text, by_index[m.scene_id, m.sentence_index].text))
    ]
    rejected = sorted(
        [*retakes(lines), *contradicting], key=lambda t: (t.line.asset_id, t.line.start)
    )
    return Conformance(order + unplaced, tuple(matches), unplaced, missing, tuple(rejected))


def _weighted_median(values: Sequence[tuple[float, float]]) -> float:
    ordered = sorted(values)
    half = sum(w for _, w in ordered) / 2
    seen = 0.0
    for value, weight in ordered:
        seen += weight
        if seen >= half:
            return value
    return statistics.median(v for v, _ in ordered)


# --- takes ------------------------------------------------------------------------------------
#: "22." may be a German ordinal ("bis zum 22. November"): see ``_opens_sentence``.
_SENTENCE_END = re.compile(r"(?:(?<!\d)\.|[!?]|\.\.\.|…)[\"'»“”)]*$")
_ORDINAL = re.compile(r"\d\.$")
_ELLIPSIS = re.compile(r"(?:\.\.\.|…)[\"'»“”)]*$")
_MONTHS = frozenset(
    {
        "januar", "februar", "marz", "april", "mai", "juni", "juli", "august", "september",
        "oktober", "november", "dezember",
    }
)  # fmt: skip


def sentence_lines(
    asset_id: str, words: Sequence[tuple[str, float, float]]
) -> tuple[TranscriptLine, ...]:
    """Timed words -> one line per spoken sentence. A recognizer segment often holds a false start
    and its retake ("Also ich bevorzuge dann tatsächlich... Und welche würde ich nehmen?"); split
    at every sentence end, including a broken-off "...", they can be told apart."""
    out: list[TranscriptLine] = []
    current: list[tuple[str, float, float]] = []
    for i, word in enumerate(words):
        current.append(word)
        text = word[0].strip()
        nxt = words[i + 1][0].strip() if i + 1 < len(words) else ""
        if _SENTENCE_END.search(text) or (_ORDINAL.search(text) and _opens_sentence(nxt)):
            out.append(_line(asset_id, current))
            current = []
    if current:
        out.append(_line(asset_id, current))
    return tuple(out)


def _opens_sentence(word: str) -> bool:
    """After a number with a full stop a sentence only ends before a capital that is no month."""
    return (
        bool(word)
        and word[0].isupper()
        and _words(word)[:1] != []
        and (_words(word)[0] not in _MONTHS)
    )


def _line(asset_id: str, words: Sequence[tuple[str, float, float]]) -> TranscriptLine:
    return TranscriptLine(asset_id, words[0][1], words[-1][2], " ".join(w for w, _, _ in words))


#: Spoken filler that two unrelated lines share ("auf jeden Fall", "wirklich", "natürlich").
_FILLER_STEMS = frozenset({"jede", "fall", "wirk", "natu", "tats", "ganz", "also", "eben", "eige"})


def _stems(text: str) -> frozenset[str]:
    """Content words cut to four letters: "nehme" and "nehmen" are the same word in a retake."""
    return frozenset(w[:4] for w in tokens(text)) - _FILLER_STEMS


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss"))
    return _WORD.findall(folded.encode("ascii", "ignore").decode())


def retakes(
    lines: Sequence[TranscriptLine],
    *,
    similar_at: float = 0.5,
    window_lines: int = 3,
    window_seconds: float = 30.0,
) -> tuple[Take, ...]:
    """Speech the presenter abandoned, recording by recording (lines in time order):

    * a RETAKE: a later line says the same again (stems, Dice >= ``similar_at``) within
      ``window_lines`` lines and ``window_seconds``; everything from the first attempt up to the
      retake goes (an abandoned attempt often has a second, half-finished sentence);
    * a FALSE START: a short line (up to three words) the next line begins with ("Fünf." /
      "Fünf brandneue Batterien ...");
    * a BROKEN-OFF sentence ("... würde ich so mal hin...") that the next line restarts from its
      last words: both go, a half sentence spliced to its restart is not a sentence.

    Only the earlier attempt is ever rejected; the last take stands."""
    out: dict[TranscriptLine, str] = {}
    by_asset: dict[str, list[TranscriptLine]] = {}
    for line in lines:
        by_asset.setdefault(line.asset_id, []).append(line)
    for recorded in by_asset.values():
        recorded.sort(key=lambda x: x.start)
        for i, line in enumerate(recorded):
            stems = _stems(line.text)
            words = _words(line.text)
            for j in range(i + 1, min(len(recorded), i + 1 + window_lines)):
                later = recorded[j]
                if later.start - line.end > window_seconds:
                    break
                if len(stems) >= 2 and dice(stems, _stems(later.text)) >= similar_at:
                    for k in range(i, j):
                        out.setdefault(recorded[k], f"retake: said again at {later.start:.1f} s")
                    break
            if i + 1 >= len(recorded):
                continue
            nxt = recorded[i + 1]
            following = _words(nxt.text)
            if 0 < len(words) <= 3 and following[: len(words)] == words and len(following) > 3:
                out.setdefault(line, "false start: the next line begins with it")
            elif _ELLIPSIS.search(line.text.strip()) and len(following) >= 2:
                tail = words[-6:]
                if any(tail[k : k + 2] == following[:2] for k in range(len(tail) - 1)):
                    out.setdefault(line, "broken off and restarted in the next line")
                    out.setdefault(nxt, "the restart of a broken-off sentence")
    return tuple(Take(line, reason) for line, reason in out.items())


def contradiction(spoken: str, script_text: str) -> str:
    """Why a line that speaks a script sentence names a different month ("22. Januar" for
    "22. November"), or ``""``. Figures are not compared: a paraphrase matched to a neighbouring
    sentence carries other figures, and prices are checked as commerce facts elsewhere."""
    said = {w for w in _words(spoken) if w in _MONTHS}
    wanted = {w for w in _words(_SLOT.sub(" ", script_text)) if w in _MONTHS}
    if said and wanted and said - wanted and wanted - said:
        return (
            f"month {' '.join(sorted(said - wanted))} instead of {' '.join(sorted(wanted - said))}"
        )
    return ""
