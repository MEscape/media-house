"""Working with a parsed script: resolve it (slots, facts, approvals), measure it, write it back.

The script a project stores keeps its placeholders (``{SLOT}``, ``{{fact:key}}``): one source of
truth. ``resolve_script`` makes the spoken version from it, so a changed fact or a filled slot
shows everywhere without regenerating anything.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from media_house.modules.production_project.domain.brief import Fact
from media_house.modules.production_project.domain.facts import substitute_facts
from media_house.modules.production_project.domain.script import (
    Exclusion,
    Scene,
    Script,
    ScriptSentence,
)

_SLOT = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_FACT_LEFT = re.compile(r"\{\{fact:[^}]*\}\}")
_WORD = re.compile(r"[\wÄÖÜäöüß][\wÄÖÜäöüß\-.,]*")
#: Spoken German of the channel (words per minute), used when the style names no pace.
DEFAULT_WPM = 140.0


def resolve_script(
    script: Script,
    *,
    slots: Mapping[str, str],
    facts: Sequence[Fact],
    approvals: Collection[str] = (),
) -> Script:
    """The spoken script: filled slots and facts put in, approved gates opened.

    A sentence with a slot nobody filled stays excluded; a gated sentence stays excluded until
    its gate is approved. Unknown ``{{fact:key}}`` references are left as they are (the linter
    reports them).
    """
    return replace(
        script, scenes=tuple(_resolve_scene(s, slots, facts, set(approvals)) for s in script.scenes)
    )


def _resolve_scene(
    scene: Scene, slots: Mapping[str, str], facts: Sequence[Fact], approvals: set[str]
) -> Scene:
    sentences = tuple(_resolve_sentence(s, slots, facts, approvals) for s in scene.sentences)
    graphics, _ = substitute_facts(scene.graphics_intent, facts)
    return replace(scene, sentences=sentences, graphics_intent=graphics)


def _resolve_sentence(
    sentence: ScriptSentence, slots: Mapping[str, str], facts: Sequence[Fact], approvals: set[str]
) -> ScriptSentence:
    text = _SLOT.sub(lambda m: slots.get(m.group(1), m.group(0)), sentence.text)
    text, _missing = substitute_facts(text, facts)
    if sentence.gate and sentence.gate not in approvals:
        exclusion = Exclusion.APPROVAL_REQUIRED
    elif _SLOT.search(text) or _FACT_LEFT.search(text):
        exclusion = Exclusion.UNFILLED_SLOT
    else:
        exclusion = Exclusion.NONE
    # an approved gate is open: the line is plain speech now (the engine reads no gate for it)
    gate = sentence.gate if exclusion is Exclusion.APPROVAL_REQUIRED else ""
    return replace(sentence, text=text, exclusion=exclusion, gate=gate)


def words(text: str) -> int:
    return len(_WORD.findall(text))


@dataclass(frozen=True, slots=True)
class SceneLength:
    scene_id: str
    words: int
    seconds: float


def estimate_lengths(
    script: Script,
    *,
    wpm: float = DEFAULT_WPM,
    include_optional: bool = True,
    off_blocks: Collection[str] = (),
) -> tuple[SceneLength, ...]:
    """The spoken length per scene. Excluded sentences and switched-off optional blocks do not
    count; a pause of a quarter second follows every sentence."""
    out: list[SceneLength] = []
    for scene in script.scenes:
        usable = [
            s
            for s in scene.sentences
            if s.usable and (include_optional or not s.optional) and s.block not in off_blocks
        ]
        count = sum(words(s.text) for s in usable)
        out.append(SceneLength(scene.scene_id, count, count / wpm * 60.0 + 0.25 * len(usable)))
    return tuple(out)


def total_seconds(lengths: Sequence[SceneLength]) -> float:
    return sum(item.seconds for item in lengths)


def format_clock(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


# --- the transcript as a markdown file -----------------------------------------------------


def render_script(
    script: Script,
    *,
    title: str,
    facts: Sequence[Fact] = (),
    slot_rows: Sequence[tuple[str, str, str]] = (),
    wpm: float = DEFAULT_WPM,
) -> str:
    """The script in the transcript format ``parse_script`` reads, with its fact table and slots.

    Times come from the estimated length, so the headings always match the words."""
    lengths = {item.scene_id: item for item in estimate_lengths(script, wpm=wpm)}
    lines = [f"# Transcript: {title}", ""]
    lines += [
        "## Lese-Hinweise",
        "- `[OPTIONAL]` kann entfallen, wenn die Laufzeit knapp wird.",
        "- `[CONDITIONAL]` nur mit schriftlicher Freigabe.",
        "- `{SLOT}` Angabe, die nur der Creator kennt: ohne Eintrag entfällt der Satz.",
        "- `[VERIFY]` vor dem Dreh gegen die Quelle prüfen.",
        "",
    ]
    clock = 0.0
    for scene in script.scenes:
        length = lengths[scene.scene_id].seconds
        span = f"{format_clock(clock)}-{format_clock(clock + length)}"
        clock += length
        lines += [f"## {scene.scene_id} · ca. {span} · {_title_of(scene)}", ""]
        if scene.intent:
            lines.append(f"Absicht: {scene.intent}")
        if scene.function:
            lines.append(f"Funktion: {scene.function}")
        if scene.graphics_intent:
            lines.append(f"Grafik: {scene.graphics_intent}")
        lines += ["", "SPRECHTEXT:"]
        lines += _sentences_block(scene)
        lines.append("")
    lines += ["---", ""]
    if facts:
        lines += [
            "## Fakten-Tabelle",
            "",
            "| Schlüssel | Wert | Quelle | geprüft |",
            "| --- | --- | --- | --- |",
        ]
        for fact in facts:
            source = fact.source.ref if fact.source else ""
            lines.append(
                f"| {fact.key} | {fact.value}{' ' + fact.unit if fact.unit else ''} | {source} "
                f"| {'ja' if fact.verified else 'nein'} |"
            )
        lines.append("")
    if slot_rows:
        lines += ["## Technik-Slots", "", "| Slot | Angabe | Wert |", "| --- | --- | --- |"]
        lines += [f"| {key} | {label} | {value or '(offen)'} |" for key, label, value in slot_rows]
        lines.append("")
    return "\n".join(lines)


def heading_title(heading: str) -> str:
    """``ca. 0:00-0:09 · Einstieg`` -> ``Einstieg``."""
    title = re.sub(
        r"^ca\.\s*\d+:\d{2}\s*[-–]\s*\d+:\d{2}\s*[·\-– ]*",  # noqa: RUF001
        "",
        heading,
    )
    return title.strip() or heading.strip()


def _title_of(scene: Scene) -> str:
    return heading_title(scene.heading) or scene.scene_id


def _sentences_block(scene: Scene) -> list[str]:
    lines: list[str] = []
    open_tag = ""
    for sentence in scene.sentences:
        wanted = _wrap_of(sentence)
        if wanted != open_tag:
            if open_tag:
                lines.append(_close_of(open_tag))
            if wanted:
                lines.append(wanted)
            open_tag = wanted
        suffix = " [VERIFY]" if sentence.verify else ""
        lines.append(f"{sentence.text}{suffix}")
    if open_tag:
        lines.append(_close_of(open_tag))
    return lines


def _wrap_of(sentence: ScriptSentence) -> str:
    if sentence.gate:
        return f"[{sentence.gate}]"
    if sentence.optional:
        return f"[OPTIONAL: {sentence.block}]" if sentence.block else "[OPTIONAL]"
    return ""


def _close_of(open_tag: str) -> str:
    if open_tag.startswith("[OPTIONAL"):
        return "[/OPTIONAL]"
    return "[/" + open_tag.strip("[]").split(":")[0] + "]"
