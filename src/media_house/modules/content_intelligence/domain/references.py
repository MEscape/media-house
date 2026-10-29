"""Named things and the phrases that point back at them: "this model", "the previous version".

A passage rarely names what it is about: it says "this model" because the sentence before named
it. A consumer that matches the passage to pictures by its own words alone would look for a
"model" in general. This module answers two small, checkable questions from the text:

* ``designations`` - which product-like names does a text contain ("Pixel 9", "iPhone 15 Pro",
  "X100V")? A designation is a family plus a value; two designations of one family with
  different values are DIFFERENT things (a Pixel 8 is not a Pixel 9).
* ``resolve_references`` - what does a pointing phrase refer to? ``RESOLVED`` only when the nearest
  preceding statements name exactly one candidate; ``AMBIGUOUS`` when they name several;
  ``UNRESOLVED`` when nothing names it. Uncertainty is kept, never guessed away: the nearest
  statement with several names stops the search instead of picking one.

Limits (by design, stated for the consumer): names are recognised by shape (a capitalised word
followed by a number, or a letter-digit code), so a product without a number in its name
("Galaxy Fold") is not a designation; pointing phrases are matched for English and German; a
phrase pointing at a PROCESS ("the same process") has no name to resolve to and is reported
``UNRESOLVED`` so the consumer widens its context instead of trusting a guess. Nothing is
inferred from a number sequence: "the previous version" of a Pixel 9 is only named when the
context itself names an older Pixel.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from media_house.modules.content_intelligence.domain.model import ContentAnalysis

#: How many statements before a phrase are searched for what it points at.
WINDOW: Final = 6

_SUFFIX = r"\s+(?:Pro\s+Max|Pro|Max|Ultra|Plus|Mini|Lite|SE|XL|FE)"
#: A name-like word, then a number or a letter-digit token ("Pixel 9", "iPhone 15 Pro").
_NAMED = re.compile(
    rf"\b(?P<family>[A-Z][A-Za-z]{{2,}}|[a-z]+[A-Z][A-Za-z]*)\s+"
    rf"(?P<value>[A-Z]?\d{{1,4}}[A-Za-z]?)(?P<suffix>{_SUFFIX})?\b"
)
#: A letter-digit code on its own: "X100V", "A7IV", "M3".
_CODE = re.compile(r"\b(?P<family>[A-Z]{1,3})(?P<value>\d{1,4}[A-Za-z]{0,2})\b")
_LEADING_NUMBER = re.compile(r"\d+")
_LEADING_LETTERS = re.compile(r"[a-z]+")
#: Words that precede a number without naming a product ("Step 3", "Around 5 minutes").
_NOT_NAMES: Final = frozenset(
    {
        "about", "after", "again", "almost", "also", "around", "before", "between", "chapter",
        "day", "episode", "figure", "from", "item", "just", "level", "minute", "minutes", "month",
        "nearly", "number", "only", "option", "over", "page", "part", "point", "roughly",
        "rule", "scene", "section", "shot", "since", "step", "table", "take", "then", "tip",
        "top", "track", "under", "until", "version", "week", "with", "within", "year", "years",
        "schritt", "kapitel", "tag", "folge", "seite", "teil", "etwa", "rund", "knapp",
    }
)  # fmt: skip
#: Format and unit codes that look like a product code ("MP4", "H264", "CO2").
_NOT_CODES: Final = frozenset({"mp3", "mp4", "h264", "h265", "h2", "co2", "b2b", "b2c", "p2p"})

_NOUN_EN = (
    r"model|device|phone|camera|product|version|unit|laptop|lens|generation|"
    r"edition|variant|one|handset|tablet"
)
_NOUN_DE = r"Modell|Gerät|Handy|Kamera|Produkt|Version|Generation|Variante"
_THING_EN = r"process|method|technique|setup|app|tool|feature|procedure|step|workflow"
_THING_DE = r"Prozess|Verfahren|Methode|Technik|Funktion|Schritt"
_DEMONSTRATIVE = re.compile(
    rf"\b(?:(?:this|that|these|those|the\s+same)\s+(?:\w+\s+)?(?:{_NOUN_EN}|{_THING_EN})"
    rf"|(?:dies(?:es|er|e|en)|jen(?:es|er|e)|das\s+gleiche|dasselbe|derselbe|dieselbe)\s+"
    rf"(?:{_NOUN_DE}|{_THING_DE}))\b",
    re.IGNORECASE,
)
_PREVIOUS = re.compile(
    rf"\b(?:(?:the\s+)?(?:previous|earlier|older|prior|former|last)\s+(?:\w+\s+)?"
    rf"(?:{_NOUN_EN})|last\s+year\W{{0,2}}s\s+(?:{_NOUN_EN})|"
    rf"(?:das\s+|der\s+|die\s+)?(?:vorherige|vorige|frühere|ältere|letzte)[nmrs]?\s+"
    rf"(?:{_NOUN_DE}))\b",
    re.IGNORECASE,
)
_PROCESS_WORDS = re.compile(rf"\b(?:{_THING_EN}|{_THING_DE})\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True, kw_only=True)
class Designation:
    """A product-like name: ``family`` + ``value``. ``text`` is how it was written."""

    family: str
    value: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.family} {self.value}".strip()

    @property
    def stem(self) -> str:
        """The letters a value starts with ("s" of "s24"): models of one line share it."""
        found = _LEADING_LETTERS.match(self.value)
        return found.group(0) if found else ""

    @property
    def number(self) -> int | None:
        found = _LEADING_NUMBER.search(self.value)
        return int(found.group(0)) if found else None


class ReferenceKind(StrEnum):
    #: "this model", "that device", "the same process".
    POINTS_BACK = "points_back"
    #: "the previous version", "the earlier model", "last year's model".
    PREVIOUS = "previous"


class ReferenceState(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    unit_id: str
    phrase: str
    kind: ReferenceKind
    state: ReferenceState
    #: The thing it points at; only when ``state`` is ``RESOLVED``.
    designation: Designation | None = None
    #: The statement that names it.
    antecedent_unit_id: str | None = None
    #: Every name the nearest statements offered (several when ``AMBIGUOUS``).
    candidates: tuple[Designation, ...] = ()
    reason: str = ""
    #: The phrase points at an activity ("the same process"), not at a named thing.
    about_activity: bool = False


def designations(text: str) -> tuple[Designation, ...]:
    """Product-like names in ``text``, in reading order, without repeats."""
    found: dict[str, tuple[int, Designation]] = {}
    spans: list[tuple[int, int]] = []
    for match in _NAMED.finditer(text):
        if match.group("family").casefold() in _NOT_NAMES:
            continue
        spans.append(match.span())
        value = match.group("value") + (match.group("suffix") or "")
        design = _make(match.group("family"), value, match.group(0))
        found.setdefault(design.key, (match.start(), design))
    for match in _CODE.finditer(text):
        if any(a <= match.start() < b for a, b in spans):
            continue
        design = _make("", match.group(0), match.group(0))
        if design.value in _NOT_CODES:
            continue
        found.setdefault(design.key, (match.start(), design))
    return tuple(d for _, d in sorted(found.values(), key=lambda item: item[0]))


def _make(family: str, value: str, text: str) -> Designation:
    return Designation(
        family=family.casefold(),
        value=" ".join(value.casefold().split()),
        text=" ".join(text.split()),
    )


def resolve_references(
    analysis: ContentAnalysis, unit_ids: Sequence[str] | None = None
) -> tuple[Reference, ...]:
    """The pointing phrases of the given statements (default: all) and what each refers to."""
    wanted = None if unit_ids is None else set(unit_ids)
    return resolve_in_texts([(u.unit_id, u.text) for u in analysis.units], wanted)


def resolve_in_texts(
    statements: Sequence[tuple[str, str]], wanted: set[str] | None = None
) -> tuple[Reference, ...]:
    """``resolve_references`` over plain ``(id, text)`` pairs in speaking order."""
    named = [designations(text) for _, text in statements]
    out: list[Reference] = []
    for position, (unit_id, text) in enumerate(statements):
        if wanted is not None and unit_id not in wanted:
            continue
        for match in _PREVIOUS.finditer(text):
            out.append(_previous(unit_id, match.group(0), position, statements, named))
        for match in _DEMONSTRATIVE.finditer(text):
            if _inside_previous(match.start(), text):
                continue
            out.append(_points_back(unit_id, match.group(0), text, position, statements, named))
    return tuple(out)


def _inside_previous(start: int, text: str) -> bool:
    return any(m.start() <= start < m.end() for m in _PREVIOUS.finditer(text))


def _points_back(
    unit_id: str,
    phrase: str,
    text: str,
    position: int,
    statements: Sequence[tuple[str, str]],
    named: Sequence[tuple[Designation, ...]],
) -> Reference:
    if _PROCESS_WORDS.search(phrase):
        return Reference(
            unit_id=unit_id,
            phrase=phrase,
            kind=ReferenceKind.POINTS_BACK,
            state=ReferenceState.UNRESOLVED,
            reason="it points at an activity, which has no name to resolve to",
            about_activity=True,
        )
    # A name earlier in the same statement ("The Pixel 9 is fast and this phone ...") counts first.
    here = [d for d in named[position] if text.find(d.text) < text.find(phrase)]
    if len({d.key for d in here}) == 1:
        return _resolved(unit_id, phrase, ReferenceKind.POINTS_BACK, here[0], unit_id)
    for back in range(1, WINDOW + 1):
        index = position - back
        if index < 0:
            break
        names = {d.key: d for d in named[index]}
        if len(names) == 1:
            return _resolved(
                unit_id,
                phrase,
                ReferenceKind.POINTS_BACK,
                next(iter(names.values())),
                statements[index][0],
            )
        if len(names) > 1:
            return Reference(
                unit_id=unit_id,
                phrase=phrase,
                kind=ReferenceKind.POINTS_BACK,
                state=ReferenceState.AMBIGUOUS,
                antecedent_unit_id=statements[index][0],
                candidates=tuple(names.values()),
                reason="the nearest statement that names something names several things",
            )
    return Reference(
        unit_id=unit_id,
        phrase=phrase,
        kind=ReferenceKind.POINTS_BACK,
        state=ReferenceState.UNRESOLVED,
        reason=f"nothing is named in the {WINDOW} statements before it",
    )


def _previous(
    unit_id: str,
    phrase: str,
    position: int,
    statements: Sequence[tuple[str, str]],
    named: Sequence[tuple[Designation, ...]],
) -> Reference:
    context: list[tuple[int, Designation]] = []
    for index in range(max(0, position - WINDOW), position + 1):
        context += [(index, d) for d in named[index]]
    subjects = {d.key: (i, d) for i, d in context}
    if not subjects:
        return _unresolved_previous(unit_id, phrase, "no model is named in the context")
    current = max(subjects.values(), key=lambda item: item[0])[1]
    older = [
        (i, d)
        for i, d in subjects.values()
        if d.family == current.family
        and d.number is not None
        and current.number is not None
        and d.number < current.number
    ]
    if not older:
        return _unresolved_previous(
            unit_id,
            phrase,
            f"the context names no older {current.family} than {current.text}",
            candidates=(current,),
        )
    best = max(older, key=lambda item: (item[1].number or 0, item[0]))
    return _resolved(unit_id, phrase, ReferenceKind.PREVIOUS, best[1], statements[best[0]][0])


def _unresolved_previous(
    unit_id: str, phrase: str, reason: str, candidates: tuple[Designation, ...] = ()
) -> Reference:
    return Reference(
        unit_id=unit_id,
        phrase=phrase,
        kind=ReferenceKind.PREVIOUS,
        state=ReferenceState.UNRESOLVED,
        candidates=candidates,
        reason=reason,
    )


def _resolved(
    unit_id: str, phrase: str, kind: ReferenceKind, designation: Designation, antecedent: str
) -> Reference:
    return Reference(
        unit_id=unit_id,
        phrase=phrase,
        kind=kind,
        state=ReferenceState.RESOLVED,
        designation=designation,
        antecedent_unit_id=antecedent,
        candidates=(designation,),
    )
