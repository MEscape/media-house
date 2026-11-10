"""A scripted transcript: scenes in order, what is spoken, and what must not be used.

Format (markdown, German or English labels)::

    ## S01 · ca. 0:00-0:09 · Title
    Absicht: what the scene is for            (Intent: ...)
    SPRECHTEXT:                               (SPOKEN: / SCRIPT:)
    [a stage direction]
    A spoken sentence. Another one with a {SLOT}.
    [OPTIONAL] ... [/OPTIONAL]                may be dropped when the running time is short
    [NUR_MIT_X_FREIGABE] ... [/NUR_MIT_X_FREIGABE]   only with the sponsor's written approval
    [ONLY_WITH_X] ... [/ONLY_WITH_X]                 (same, English)

Rules: a sentence with an unfilled ``{SLOT}`` is dropped; an approval-gated block is excluded
by default; ``[PLATZHALTER ...]`` / ``[PLACEHOLDER ...]`` inside a sentence is removed, the
sentence is kept. Everything else in brackets is a direction, never spoken text. Pure: no I/O.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

_SCENE = re.compile(r"^##\s+(S\d+)\b(.*)$")
_SPOKEN = re.compile(r"^(SPRECHTEXT|SPOKEN|SCRIPT)\s*:\s*$", re.IGNORECASE)
_INTENT = re.compile(r"^(Absicht|Intent)\s*:\s*(.*)$", re.IGNORECASE)
_GATE_OPEN = re.compile(r"^\[(NUR_MIT_[A-Z_]*FREIGABE|ONLY_WITH_[A-Z_]+)\]$")
_GATE_CLOSE = re.compile(r"^\[/(NUR_MIT_[A-Z_]*FREIGABE|ONLY_WITH_[A-Z_]+)\]$")
_PLACEHOLDER = re.compile(r"\[(PLATZHALTER|PLACEHOLDER)[^\]]*\]", re.IGNORECASE)
_DIRECTION = re.compile(r"\[[^\]]*\]")
_SLOT = re.compile(r"\{[A-Z_]+\}")
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
_LEADING_SLOTS = re.compile(r"^((?:\{[A-Z_]+\}\s*)+)(?=\S)")


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


@dataclass(frozen=True, slots=True)
class Script:
    scenes: tuple[Scene, ...]

    @property
    def sentences(self) -> tuple[ScriptSentence, ...]:
        return tuple(s for scene in self.scenes for s in scene.sentences)


def parse_script(text: str) -> Script:
    scenes: list[Scene] = []
    scene_id: str | None = None
    heading, intent = "", ""
    lines: list[tuple[str, bool, bool]] = []  # text, optional, gated
    spoken = optional = gated = False

    def close() -> None:
        if scene_id is not None:
            scenes.append(_scene(scene_id, len(scenes), heading, intent, lines))

    for raw in text.splitlines():
        line = raw.strip()
        if m := _SCENE.match(line):
            close()
            scene_id, heading, intent = m.group(1), m.group(2).strip(_HEADING_TRIM), ""
            lines, spoken, optional, gated = [], False, False, False
            continue
        if scene_id is None:
            continue
        if line.startswith("## ") or line == "---":
            close()
            scene_id = None
            continue
        if m := _INTENT.match(line):
            intent = m.group(2).strip()
        elif _SPOKEN.match(line):
            spoken = True
        elif line.upper() == "[OPTIONAL]":
            optional = True
        elif line.upper() == "[/OPTIONAL]":
            optional = False
        elif _GATE_OPEN.match(line):
            gated = True
        elif _GATE_CLOSE.match(line):
            gated = False
        elif spoken and line:
            lines.append((line, optional, gated))
    close()
    return Script(tuple(scenes))


def _scene(
    scene_id: str, order: int, heading: str, intent: str, lines: list[tuple[str, bool, bool]]
) -> Scene:
    sentences: list[ScriptSentence] = []
    for line, optional, gated in lines:
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
            sentences.append(ScriptSentence(scene_id, len(sentences), part, optional, exclusion))
    return Scene(scene_id, order, heading, intent, tuple(sentences))
