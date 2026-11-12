"""A scripted transcript: scenes in order, what is spoken, and what must not be used.

Format (markdown, German or English labels)::

    ## S01 · ca. 0:00-0:09 · Title
    Absicht: what the scene is for            (Intent: ...)
    Funktion: hook                            (Function: ...)  what the scene does for the video
    Grafik: what is shown on screen           (Graphics: ...)
    SPRECHTEXT:                               (SPOKEN: / SCRIPT:)
    [a stage direction]
    A spoken sentence. Another one with a {SLOT}.
    [OPTIONAL] ... [/OPTIONAL]                may be dropped when the running time is short
    [OPTIONAL: name] ... [/OPTIONAL]          the same, a named block that can be switched off
    [NUR_MIT_X_FREIGABE] ... [/NUR_MIT_X_FREIGABE]   only with the sponsor's written approval
    [ONLY_WITH_X] ... [/ONLY_WITH_X]                 (same, English)
    [CONDITIONAL: needs approval] ... [/CONDITIONAL] (same, generic)
    A sentence that needs a source check. [VERIFY]   marks it for the fact check (stays spoken)

Rules: a sentence with an unfilled ``{SLOT}`` is dropped; an approval-gated block is excluded
by default; ``[PLATZHALTER ...]`` / ``[PLACEHOLDER ...]`` inside a sentence is removed, the
sentence is kept. Everything else in brackets is a direction, never spoken text. Pure: no I/O.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

_SCENE = re.compile(r"^##\s+(S\d+)\b(.*)$")
_SPOKEN = re.compile(r"^(SPRECHTEXT|SPOKEN|SCRIPT)\s*:\s*$", re.IGNORECASE)
_INTENT = re.compile(r"^(Absicht|Intent)\s*:\s*(.*)$", re.IGNORECASE)
_GATE_OPEN = re.compile(
    r"^\[(NUR_MIT_[A-Z_]*FREIGABE|ONLY_WITH_[A-Z_]+|CONDITIONAL(?::[^\]]*)?|BEDINGT(?::[^\]]*)?)\]$",
    re.IGNORECASE,
)
_GATE_CLOSE = re.compile(
    r"^\[/(NUR_MIT_[A-Z_]*FREIGABE|ONLY_WITH_[A-Z_]+|CONDITIONAL|BEDINGT)\]$", re.IGNORECASE
)
_OPTIONAL_OPEN = re.compile(r"^\[OPTIONAL(?::\s*([^\]]*))?\]$", re.IGNORECASE)
_GRAPHICS = re.compile(r"^(Grafik|Graphics)\s*:\s*(.*)$", re.IGNORECASE)
_FUNCTION = re.compile(r"^(Funktion|Function)\s*:\s*(.*)$", re.IGNORECASE)
_VERIFY = re.compile(r"\[VERIFY[^\]]*\]", re.IGNORECASE)
_TIME_HINT = re.compile(r"(\d{1,2}:\d{2})\s*[-\u2013]\s*(\d{1,2}:\d{2})")
_PLACEHOLDER = re.compile(r"\[(PLATZHALTER|PLACEHOLDER)[^\]]*\]", re.IGNORECASE)
_DIRECTION = re.compile(r"\[[^\]]*\]")
_SLOT = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
#: Separators around a heading title: space, middle dot, hyphen, en dash.
_HEADING_TRIM = " " + chr(0xB7) + "-" + chr(0x2013)
_MONTH_NAMES = "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
#: A sentence ends at . ! ? …, except a German ordinal ("bis zum 22. November", "zwischen dem
#: 29. und 31. Dezember"): after a number it only ends before a capital that is no month.
_SENTENCE = re.compile(
    r"(?<=[.!?…])(?<!\d\.)\s+(?=\S)"
    rf"|(?<=\d\.)\s+(?=(?!(?:{_MONTH_NAMES})\b)[A-ZÄÖÜ])"
)
#: Slots that stand where a whole sentence goes (``... Bombetten. {A} {B} Die Höhe ...``): the
#: filled value is its own sentence, so a missing one must not take the next sentence with it.
_LEADING_SLOTS = re.compile(r"^((?:\{[A-Z][A-Z0-9_]*\}\s*)+)(?=\S)")


class Exclusion(StrEnum):
    NONE = "none"
    APPROVAL_REQUIRED = "approval_required"
    UNFILLED_SLOT = "unfilled_slot"


@dataclass(frozen=True, slots=True)
class ScriptSentence:
    scene_id: str
    index: int
    text: str
    optional: bool
    exclusion: Exclusion
    #: Marked ``[VERIFY]``: spoken, but the fact check must confirm it first.
    verify: bool = False
    #: The named optional block the sentence is in ("" outside a named block).
    block: str = ""
    #: The approval gate that excludes it ("" = none), e.g. ``NUR_MIT_HERSTELLER_FREIGABE``.
    gate: str = ""

    @property
    def usable(self) -> bool:
        return self.exclusion is Exclusion.NONE


@dataclass(frozen=True, slots=True)
class Scene:
    scene_id: str
    order: int
    heading: str
    intent: str
    sentences: tuple[ScriptSentence, ...]
    #: ``0:00-0:09`` as written in the heading ("" when the heading has none).
    time_hint: str = ""
    graphics_intent: str = ""
    #: What the scene does for the video (hook, context, product, comparison, facts, cta,
    #: callback, outro ...): free text, "" when the script does not say.
    function: str = ""

    @property
    def tags(self) -> tuple[str, ...]:
        """The tags of the scene's sentences, in a fixed order."""
        found = {
            "OPTIONAL": any(s.optional for s in self.sentences),
            "CONDITIONAL": any(s.exclusion is Exclusion.APPROVAL_REQUIRED for s in self.sentences),
            "SLOT": any(s.exclusion is Exclusion.UNFILLED_SLOT for s in self.sentences),
            "VERIFY": any(s.verify for s in self.sentences),
        }
        return tuple(tag for tag, present in found.items() if present)


@dataclass(frozen=True, slots=True)
class Script:
    scenes: tuple[Scene, ...]

    @property
    def sentences(self) -> tuple[ScriptSentence, ...]:
        return tuple(s for scene in self.scenes for s in scene.sentences)


def parse_script(text: str) -> Script:
    scenes: list[Scene] = []
    scene_id: str | None = None
    heading, intent, graphics, function = "", "", "", ""
    lines: list[
        tuple[str, str | None, str]
    ] = []  # text, optional block (None = not optional), gated
    spoken = False
    gated = ""
    optional: str | None = None

    def close() -> None:
        if scene_id is not None:
            scenes.append(_scene(scene_id, len(scenes), heading, intent, graphics, function, lines))

    for raw in text.splitlines():
        line = raw.strip()
        if m := _SCENE.match(line):
            close()
            scene_id, heading, intent = m.group(1), m.group(2).strip(_HEADING_TRIM), ""
            graphics = function = ""
            lines, spoken, optional, gated = [], False, None, ""
            continue
        if scene_id is None:
            continue
        if line.startswith("## ") or line == "---":
            close()
            scene_id = None
            continue
        if m := _INTENT.match(line):
            intent = m.group(2).strip()
        elif m := _GRAPHICS.match(line):
            graphics = m.group(2).strip()
        elif m := _FUNCTION.match(line):
            function = m.group(2).strip().casefold()
        elif _SPOKEN.match(line):
            spoken = True
        elif m := _OPTIONAL_OPEN.match(line):
            optional = (m.group(1) or "").strip()
        elif line.upper() == "[/OPTIONAL]":
            optional = None
        elif gate := _GATE_OPEN.match(line):
            gated = gate.group(1).upper()
        elif _GATE_CLOSE.match(line):
            gated = ""
        elif spoken and line:
            lines.append((line, optional, gated))
    close()
    return Script(tuple(scenes))


def _scene(
    scene_id: str,
    order: int,
    heading: str,
    intent: str,
    graphics: str,
    function: str,
    lines: list[tuple[str, str | None, str]],
) -> Scene:
    sentences: list[ScriptSentence] = []
    for line, block, gated in lines:
        verify = bool(_VERIFY.search(line))
        cleaned = _DIRECTION.sub(" ", _PLACEHOLDER.sub(" ", line))
        parts: list[str] = []
        for part in _SENTENCE.split(" ".join(cleaned.split())):
            if m := _LEADING_SLOTS.match(part):
                parts += [m.group(1), part[m.end() :]]
            else:
                parts.append(part)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            exclusion = (
                Exclusion.APPROVAL_REQUIRED
                if gated
                else Exclusion.UNFILLED_SLOT
                if _SLOT.search(part)
                else Exclusion.NONE
            )
            sentences.append(
                ScriptSentence(
                    scene_id,
                    len(sentences),
                    part,
                    block is not None,
                    exclusion,
                    verify,
                    block or "",
                    gated,
                )
            )
    hint = _TIME_HINT.search(heading)
    return Scene(
        scene_id,
        order,
        heading,
        intent,
        tuple(sentences),
        f"{hint.group(1)}-{hint.group(2)}" if hint else "",
        graphics,
        function,
    )


def fact_rows(text: str, names: Iterable[str]) -> dict[str, tuple[tuple[str, str], ...]]:
    """The facts a script's markdown tables give per named thing: every table row whose first
    cell is one of ``names`` (any case), as (column header, cell) pairs in column order. Rows of
    several tables are joined; empty cells and the name column are left out."""
    wanted = {n.casefold(): n for n in names}
    found: dict[str, list[tuple[str, str]]] = {}
    header: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            header = []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue  # the separator row
        if not header:
            header = cells
            continue
        name = wanted.get(cells[0].casefold())
        if name is None:
            continue
        row = found.setdefault(name, [])
        for label, cell in zip(header[1:], cells[1:], strict=False):
            if cell and label and all(label != have for have, _ in row):
                row.append((label, cell))
    return {name: tuple(row) for name, row in found.items()}
