# ruff: noqa: RUF001
"""Key information: what was actually said that matters, and the rhetorical function of sentences.

Every item is a VERBATIM sentence (origin ``direct``) found by a rule, with the cue that fired as
evidence. Nothing is paraphrased or inferred here, so nothing can be invented. Hedges stay
visible (evidence + lower strength) and a caveat that follows a statement is attached to it
(``qualified_by``) so a consumer cannot take the statement without its qualification.
"""

import re
from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.frame import Frame
from media_house.modules.content_intelligence.domain.ids import item_id, segment_id
from media_house.modules.content_intelligence.domain.model import (
    Evidence,
    KeyItem,
    RhetoricalMarker,
    excerpt,
)
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.values import (
    KeyKind,
    Method,
    Origin,
    RhetoricalKind,
)

_NUMBER = re.compile(r"\d[\d.,]*")
_STAT_UNIT = re.compile(
    r"%|percent|prozent|million|billion|mio|mrd|[€$£]|\b(?:eur|usd|kg|km|ms|gb|mb|ghz|mhz|hours?|"
    r"stunden|minutes?|minuten|years?|jahre|times|mal)\b",
    re.IGNORECASE,
)
_STRONG_CLAIM = re.compile(
    r"\b(?:always|never|must|nobody|everyone|nothing|best|worst|only|immer|nie|niemals|muss|"
    r"niemand|jeder|nichts|beste|schlechteste|nur)\b",
    re.IGNORECASE,
)
_ANTITHESIS = re.compile(
    r"\bnot\b.{2,60}\bbut\b|\bnicht\b.{2,60}\bsondern\b|\bnot just\b|\bnicht nur\b", re.IGNORECASE
)
_QUOTE_MARKS = "\"'”“»«„"
_MIN_CLAIM_WORDS = 6
_QUOTABLE_WORDS = (6, 26)
_KEPT_PER_UNIT = 2

#: (cue name, minimum weight, kind) - checked in this order, at most ``_KEPT_PER_UNIT`` kept.
_CUE_KINDS: tuple[tuple[str, float, KeyKind], ...] = (
    ("definition", 0.7, KeyKind.DEFINITION),
    ("decision", 0.8, KeyKind.DECISION),
    ("action", 0.8, KeyKind.ACTION_ITEM),
    ("recommendation", 0.8, KeyKind.RECOMMENDATION),
    ("conclusion", 0.8, KeyKind.CONCLUSION),
    ("example", 0.8, KeyKind.EXAMPLE),
)


def is_question(unit: Unit, frame: Frame) -> bool:
    text = unit.text.rstrip(_QUOTE_MARKS + " ")
    if text.endswith(("?", "？")):
        return True
    if frame.lexicon.supported and not unit.ends_clean:
        return False
    punctuated = any(
        u.ends_clean and u.text.rstrip().endswith((".", "!", "?")) for u in frame.units
    )
    return (
        not punctuated
        and unit.words <= 20
        and frame.lexicon.weight("question_word", unit.text) >= 0.5
    )


def is_statistic(text: str) -> bool:
    """A number that states a quantity (a year or an ordinal alone does not)."""
    for m in _NUMBER.finditer(text):
        digits = m.group().strip(".,")
        if not digits or (len(digits) == 4 and digits.isdigit() and 1900 <= int(digits) <= 2099):
            continue
        if len(digits) >= 2 or _STAT_UNIT.search(text):
            return True
    return False


@dataclass(frozen=True, slots=True)
class KeyInfo:
    items: list[KeyItem]
    markers: list[tuple[int, RhetoricalMarker]]  # (unit index, marker)


def _qualifier(frame: Frame, index: int, segment_end: int) -> tuple[str, ...]:
    """The next sentence when it opens with a caveat (and belongs to the same passage)."""
    nxt = index + 1
    if nxt >= segment_end:
        return ()
    unit, following = frame.units[index], frame.units[nxt]
    if unit.speaker and following.speaker and unit.speaker != following.speaker:
        return ()
    return (following.unit_id,) if frame.lexicon.weight("caveat", following.text) >= 0.8 else ()


def extract(frame: Frame, segments: list[range]) -> KeyInfo:
    lex = frame.lexicon
    items: list[KeyItem] = []

    def add(
        unit: Unit,
        kind: KeyKind,
        seg: int,
        evidence: Evidence,
        end: int,
        weight: float,
    ) -> None:
        hedge = lex.weight("hedge", unit.text)
        evidences = [evidence]
        strength = weight
        if hedge:
            evidences.append(frame.cue("hedge", unit, hedge))
            strength = round(weight * 0.8, 3)
        items.append(
            KeyItem(
                item_id=item_id(len(items)),
                kind=kind,
                text=unit.text,
                origin=Origin.DIRECT,
                method=Method.HEURISTIC,
                unit_ids=(unit.unit_id,),
                segment_id=segment_id(seg),
                span=unit.span,
                evidence=tuple(evidences),
                confidence=strength,
                qualified_by=_qualifier(frame, unit.index, end),
            )
        )

    for seg, rng in enumerate(segments):
        found_in_segment = 0
        for i in rng:
            unit = frame.units[i]
            kept = 0
            if is_question(unit, frame):
                add(unit, KeyKind.QUESTION, seg, frame.cue("question", unit, 0.6), rng.stop, 0.6)
                found_in_segment += 1
                continue
            for name, minimum, kind in _CUE_KINDS:
                weight = lex.weight(name, unit.text)
                if weight >= minimum and kept < _KEPT_PER_UNIT:
                    add(unit, kind, seg, frame.cue(name, unit, weight), rng.stop, weight)
                    kept += 1
            if lex.weight("caveat", unit.text) >= 0.8 and unit.index == rng.start:
                add(unit, KeyKind.CAVEAT, seg, frame.cue("caveat", unit, 0.8), rng.stop, 0.8)
                kept += 1
            if kept < _KEPT_PER_UNIT and is_statistic(unit.text):
                evidence = Evidence(
                    kind="statistic",
                    method=Method.DETERMINISTIC,
                    unit_ids=(unit.unit_id,),
                    excerpt=excerpt(unit.text, 100),
                    weight=0.6,
                    detail="contains a quantity",
                )
                add(unit, KeyKind.FACT, seg, evidence, rng.stop, 0.6)
                kept += 1
            claim = max(
                lex.weight("emphasis", unit.text), 0.7 if _STRONG_CLAIM.search(unit.text) else 0.0
            )
            if kept < _KEPT_PER_UNIT and claim and unit.words >= _MIN_CLAIM_WORDS:
                add(
                    unit,
                    KeyKind.CLAIM,
                    seg,
                    frame.cue("strong_claim", unit, claim),
                    rng.stop,
                    claim,
                )
                kept += 1
            low, high = _QUOTABLE_WORDS
            if (
                kept < _KEPT_PER_UNIT
                and low <= unit.words <= high
                and unit.starts_clean
                and not (lex.anaphora and lex.anaphora.search(unit.text))
                and (_ANTITHESIS.search(unit.text) or lex.weight("surprise", unit.text) >= 0.6)
            ):
                add(unit, KeyKind.QUOTABLE, seg, frame.cue("antithesis", unit, 0.6), rng.stop, 0.6)
                kept += 1
            found_in_segment += kept
        if not found_in_segment:
            _representative(frame, rng, seg, items)
    return KeyInfo(items, _markers(frame))


def _representative(frame: Frame, rng: range, seg: int, items: list[KeyItem]) -> None:
    """With no cue at all, the most central declarative sentence stands for the passage."""
    candidates = [
        i
        for i in rng
        if frame.units[i].words >= _MIN_CLAIM_WORDS and not is_question(frame.units[i], frame)
    ]
    if len(rng) < 2 or len(candidates) < 2:
        return
    group = list(rng)
    best = max(candidates, key=lambda i: (frame.similarity.group_similarity([i], group), -i))
    centrality = frame.centrality_scale(frame.similarity.group_similarity([best], group))
    unit = frame.units[best]
    items.append(
        KeyItem(
            item_id=item_id(len(items)),
            kind=KeyKind.CLAIM,
            text=unit.text,
            origin=Origin.DIRECT,
            method=Method.HEURISTIC,
            unit_ids=(unit.unit_id,),
            segment_id=segment_id(seg),
            span=unit.span,
            evidence=(
                Evidence(
                    kind="centrality",
                    method=Method.DETERMINISTIC,
                    unit_ids=(unit.unit_id,),
                    excerpt=excerpt(unit.text, 100),
                    weight=centrality,
                    detail="most representative sentence of its passage; no cue matched",
                ),
            ),
            confidence=round(0.4 * centrality, 3),
        )
    )


def _markers(frame: Frame) -> list[tuple[int, RhetoricalMarker]]:
    lex = frame.lexicon
    out: list[tuple[int, RhetoricalMarker]] = []
    audio = frame.audio

    def mark(unit: Unit, kind: RhetoricalKind, evidence: Evidence, method: Method) -> None:
        out.append(
            (
                unit.index,
                RhetoricalMarker(
                    kind=kind, unit_ids=(unit.unit_id,), method=method, evidence=(evidence,)
                ),
            )
        )

    for unit in frame.units:
        nxt = frame.units[unit.index + 1] if unit.index + 1 < len(frame.units) else None
        if is_question(unit, frame) and nxt is not None and nxt.speaker == unit.speaker:
            weight = 0.7 if lex.weight("answer_lead", nxt.text) else 0.4
            mark(
                unit,
                RhetoricalKind.RHETORICAL_QUESTION,
                frame.cue("question_then_same_speaker", unit, weight),
                Method.HEURISTIC,
            )
        if (w := lex.weight("contrast", unit.text)) or _ANTITHESIS.search(unit.text):
            mark(
                unit,
                RhetoricalKind.CONTRAST,
                frame.cue("contrast", unit, w or 0.5),
                Method.HEURISTIC,
            )
        if w := lex.weight("emphasis", unit.text):
            mark(unit, RhetoricalKind.EMPHASIS, frame.cue("emphasis", unit, w), Method.HEURISTIC)
        elif audio is not None and unit.span.known:
            strength = audio.emphasis_between(unit.span.start or 0.0, unit.span.end or 0.0)
            if strength is not None and strength >= 0.7:
                evidence = Evidence(
                    kind="audio:emphasis",
                    method=Method.OBSERVED,
                    unit_ids=(unit.unit_id,),
                    weight=strength,
                    detail="a word was delivered with notable emphasis",
                )
                mark(unit, RhetoricalKind.EMPHASIS, evidence, Method.OBSERVED)
        if w := lex.weight("surprise", unit.text):
            mark(unit, RhetoricalKind.SURPRISE, frame.cue("surprise", unit, w), Method.HEURISTIC)
        if (
            lex.weight("hedge", unit.text)
            and len(lex.matches(unit.text, ("hedge",)))
            and unit.words <= 40
        ):
            mark(unit, RhetoricalKind.UNCERTAINTY, frame.cue("hedge", unit, 0.5), Method.HEURISTIC)
        if w := lex.weight("personal", unit.text):
            mark(
                unit,
                RhetoricalKind.PERSONAL_DISCLOSURE,
                frame.cue("personal", unit, w),
                Method.HEURISTIC,
            )
        if (w := lex.weight("conclusion", unit.text)) and (
            lex.weight("emphasis", unit.text) or _STRONG_CLAIM.search(unit.text)
        ):
            mark(
                unit,
                RhetoricalKind.STRONG_CONCLUSION,
                frame.cue("conclusion", unit, w),
                Method.HEURISTIC,
            )
        if w := max(lex.weight("turning", unit.text), lex.weight("counter", unit.text)):
            mark(
                unit,
                RhetoricalKind.DIRECTION_CHANGE,
                frame.cue("direction_change", unit, w),
                Method.HEURISTIC,
            )
        if w := lex.weight("persuade", unit.text):
            mark(unit, RhetoricalKind.PERSUASION, frame.cue("persuade", unit, w), Method.HEURISTIC)
        if w := lex.weight("humor", unit.text):
            mark(unit, RhetoricalKind.HUMOR, frame.cue("humor", unit, w), Method.HEURISTIC)
        elif (
            audio is not None
            and unit.span.known
            and audio.laughter_between(unit.span.start or 0.0, unit.span.end or 0.0)
        ):
            evidence = Evidence(
                kind="audio:laughter",
                method=Method.OBSERVED,
                unit_ids=(unit.unit_id,),
                weight=0.6,
                detail="laughter was detected during the sentence",
            )
            mark(unit, RhetoricalKind.HUMOR, evidence, Method.OBSERVED)
    return out
