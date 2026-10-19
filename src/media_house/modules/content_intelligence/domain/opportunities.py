"""B-roll and graphics opportunities: where supplementary visuals or on-screen text might help.

These are observations about the SPOKEN content ("a place is described", "a figure is stated"),
kept apart from whether a suitable asset exists. No asset is ever claimed. Concepts and figures
are copied from the transcript, never invented. Existing visual analysis is used two ways: a shot
that already shows the concept marks the b-roll opportunity as covered, and on-screen text that
already contains a figure or name suppresses the graphic for it.
"""

import re
from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.context import VisualShot
from media_house.modules.content_intelligence.domain.frame import Frame
from media_house.modules.content_intelligence.domain.ids import opportunity_id, segment_id
from media_house.modules.content_intelligence.domain.keyinfo import is_question, is_statistic
from media_house.modules.content_intelligence.domain.lexical import stem, tokens
from media_house.modules.content_intelligence.domain.model import (
    Evidence,
    KeyItem,
    Opportunity,
    excerpt,
)
from media_house.modules.content_intelligence.domain.structure import Structure
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.values import (
    GraphicKind,
    KeyKind,
    Method,
    OpportunityKind,
    Role,
    VisualCategory,
)

_QUANTITY = re.compile(
    r"\d[\d.,]*\s?(?:%|percent|prozent|million(?:s|en)?|billion(?:s|en)?|mio\.?|mrd\.?|[€$£]|"
    r"eur|usd|kg|km|ms|gb|mb|ghz|mhz|hours?|stunden|minutes?|minuten|years?|jahre[n]?)?",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[\"“„»]([^\"”“»«]{8,160})[\"”“«]")
_BROLL_MIN = 0.4
_BROLL_PER_SEGMENT = 2
_GRAPHICS_PER_SEGMENT = 3
_EXPLAINING = frozenset({Role.EXPLANATION, Role.DEMONSTRATION, Role.EXAMPLE})
_BROLL_FAMILIES: tuple[tuple[str, VisualCategory, str], ...] = (
    (
        "location",
        VisualCategory.LOCATION,
        "a place is described, and footage of it could help the viewer picture it",
    ),
    (
        "process",
        VisualCategory.PROCESS,
        "a process or sequence of steps is explained, and showing it could make it concrete",
    ),
    (
        "history",
        VisualCategory.ARCHIVE,
        "a past event or period is mentioned, and archive or historical imagery could anchor it",
    ),
    (
        "comparison",
        VisualCategory.COMPARISON,
        "two things are compared, and a side-by-side visual could show the difference",
    ),
    (
        "object",
        VisualCategory.OBJECT,
        "a specific object is discussed, and a close-up could show what is meant",
    ),
)
_BROLL_LIMITS = (
    "a semantic opportunity only: no suitable footage has been identified",
    "found from wording and context; a literal visual could mislead if the sentence is figurative",
)


@dataclass(frozen=True, slots=True)
class Found:
    broll: list[list[Opportunity]]
    graphics: list[list[Opportunity]]
    #: Graphics not suggested because the same text is already on screen.
    suppressed: int
    visual_checked: bool


def _norm(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


def _shots(frame: Frame, unit: Unit) -> tuple[VisualShot, ...]:
    if frame.visual is None or not unit.span.known:
        return ()
    return frame.visual.shots_between(unit.span.start or 0.0, unit.span.end or 0.0)


def _covering_shots(frame: Frame, unit: Unit, concept_stems: set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for shot in _shots(frame, unit):
        words = {stem(t) for t in tokens(" ".join([shot.description or "", *shot.objects]))}
        if concept_stems & words:
            out.append(shot.shot_id)
    return tuple(out)


def _on_screen(frame: Frame, unit: Unit, concept: str, numeric: bool) -> bool:
    """Whether some shot during the sentence already shows ``concept`` as text."""
    wanted = re.sub(r"\D", "", concept) if numeric else _norm(concept)
    if not wanted:
        return False
    for shot in _shots(frame, unit):
        for line in shot.screen_text:
            have = re.sub(r"\D", "", line) if numeric else _norm(line)
            if wanted in have:
                return True
    return False


def detect(
    frame: Frame, structure: Structure, items: list[KeyItem], roles: list[set[Role]]
) -> Found:
    lex = frame.lexicon
    counter = 0
    suppressed = 0
    by_segment: dict[str, list[KeyItem]] = {}
    for item in items:
        if item.segment_id:
            by_segment.setdefault(item.segment_id, []).append(item)
    broll: list[list[Opportunity]] = []
    graphics: list[list[Opportunity]] = []

    for k, rng in enumerate(structure.segments):
        units = [frame.units[i] for i in rng]
        sid = segment_id(k)
        seg_items = by_segment.get(sid, [])
        candidates: list[Opportunity] = []
        for unit in units:
            if is_question(unit, frame) or lex.weight("filler", unit.text):
                continue
            for cue, category, why in _BROLL_FAMILIES:
                weight = lex.weight(cue, unit.text)
                if not weight:
                    continue
                strength = weight * (1.0 if roles[k] & _EXPLAINING or unit.words >= 8 else 0.8)
                if strength < _BROLL_MIN:
                    continue
                terms = frame.stats.key_terms([unit.index], 3)
                concept = ", ".join(terms) if terms else excerpt(unit.text, 60)
                # Coverage is judged on the sentence's six most telling terms, not just the label.
                wide = frame.stats.key_terms([unit.index], 6)
                covering = _covering_shots(frame, unit, {stem(t) for w in wide for t in tokens(w)})
                limits = list(_BROLL_LIMITS)
                if frame.visual is None:
                    limits.append(
                        "no visual analysis was available, so existing footage was not checked"
                    )
                candidates.append(
                    Opportunity(
                        opportunity_id="",
                        kind=OpportunityKind.BROLL,
                        category=category.value,
                        concept=concept,
                        segment_id=sid,
                        unit_ids=(unit.unit_id,),
                        span=unit.span,
                        rationale=f"{why[0].upper()}{why[1:]}.",
                        strength=round(min(1.0, strength), 3),
                        method=Method.HEURISTIC,
                        visual_evidence=covering,
                        already_covered=bool(covering),
                        evidence=(frame.cue(cue, unit, weight),),
                        limitations=tuple(limits),
                    )
                )
        broll_k: list[Opportunity] = []
        for opp in sorted(candidates, key=lambda o: (-o.strength, o.unit_ids)):
            if all(o.category != opp.category for o in broll_k) and len(broll_k) < max(
                _BROLL_PER_SEGMENT, -(-len(units) // 5)
            ):
                broll_k.append(_with_id(opp, counter))
                counter += 1
        broll.append(broll_k)

        pending: list[Opportunity] = []
        process_hits = [u for u in units if lex.weight("process", u.text) >= 0.6]
        for unit in units:
            for kind, concept, strength, reason, numeric in _graphic_cues(
                frame, unit, seg_items, process_hits
            ):
                if numeric:
                    # A figure per part: only those not yet on screen are worth suggesting.
                    parts = [p for p in concept.split(", ") if not _on_screen(frame, unit, p, True)]
                    concept = ", ".join(parts)
                else:
                    parts = [] if _on_screen(frame, unit, concept, False) else [concept]
                if not parts:
                    suppressed += 1
                    continue
                limits = ["the text shown must be copied from the transcript, not rewritten"]
                if frame.visual is None:
                    limits.append(
                        "on-screen text could not be checked: no visual analysis was available"
                    )
                pending.append(
                    Opportunity(
                        opportunity_id="",
                        kind=OpportunityKind.GRAPHIC,
                        category=kind.value,
                        concept=concept,
                        segment_id=sid,
                        unit_ids=(unit.unit_id,),
                        span=unit.span,
                        rationale=_GRAPHIC_WHY[kind],
                        strength=strength,
                        method=Method.HEURISTIC,
                        evidence=(reason,),
                        limitations=tuple(limits),
                    )
                )
        graphics_k: list[Opportunity] = []
        for opp in sorted(pending, key=lambda o: (-o.strength, o.unit_ids)):
            if (
                all(o.category != opp.category for o in graphics_k)
                and len(graphics_k) < _GRAPHICS_PER_SEGMENT
            ):
                graphics_k.append(_with_id(opp, counter))
                counter += 1
        graphics.append(graphics_k)
    return Found(broll, graphics, suppressed, frame.visual is not None)


_GRAPHIC_WHY = {
    GraphicKind.KEY_NUMBER: "A figure is stated aloud; as text, viewers can read and remember it.",
    GraphicKind.DEFINITION: "A term is defined; on screen, viewers who miss a word still get it.",
    GraphicKind.LIST: "Several points are enumerated; a list shows their number and order.",
    GraphicKind.PROCESS: "Steps of a process are described; a diagram could show how they connect.",
    GraphicKind.COMPARISON: "Things are compared; a table or chart shows how they differ.",
    GraphicKind.NAME_AND_ROLE: "A person is introduced; a name and role caption identifies them.",
    GraphicKind.QUOTE: "A quotation is spoken; showing it verbatim makes the wording checkable.",
    GraphicKind.CALL_TO_ACTION: "The viewer is asked to act; a card can show where or how.",
    GraphicKind.TERM_CLARIFICATION: "A term is named as special; a caption could clarify it.",
}


def _graphic_cues(
    frame: Frame, unit: Unit, items: list[KeyItem], process_hits: list[Unit]
) -> list[tuple[GraphicKind, str, float, Evidence, bool]]:
    lex = frame.lexicon
    out: list[tuple[GraphicKind, str, float, Evidence, bool]] = []
    if is_statistic(unit.text):
        quantities = [
            m.group().strip()
            for m in _QUANTITY.finditer(unit.text)
            if len(m.group().strip(".,")) >= 2 or m.group().strip()[-1:] in "%€$£"
        ]
        quantities = [
            q
            for q in quantities
            if not (
                len(q.strip(".,")) == 4
                and q.strip(".,").isdigit()
                and 1900 <= int(q.strip(".,")) <= 2099
            )
        ]
        if quantities:
            weight = 0.75 if lex.weight("evidence", unit.text) else 0.65
            out.append(
                (
                    GraphicKind.KEY_NUMBER,
                    ", ".join(dict.fromkeys(quantities[:2])),
                    weight,
                    frame.cue("statistic", unit, weight),
                    True,
                )
            )
    for item in items:
        if item.kind is KeyKind.DEFINITION and unit.unit_id in item.unit_ids:
            out.append(
                (GraphicKind.DEFINITION, excerpt(unit.text, 140), 0.7, item.evidence[0], False)
            )
    simple = (
        (GraphicKind.LIST, "list", 0.6, 0.6),
        (GraphicKind.COMPARISON, "comparison", 0.6, 0.55),
        (GraphicKind.NAME_AND_ROLE, "name_role", 0.7, 0.7),
        (GraphicKind.CALL_TO_ACTION, "call_to_action", 0.9, 0.7),
        (GraphicKind.TERM_CLARIFICATION, "unfamiliar_term", 0.4, 0.5),
    )
    for kind, cue, minimum, strength in simple:
        weight = lex.weight(cue, unit.text)
        if weight >= minimum:
            out.append(
                (kind, excerpt(unit.text, 140), strength, frame.cue(cue, unit, weight), False)
            )
    if len(process_hits) >= 2 and unit.unit_id == process_hits[0].unit_id:
        out.append(
            (
                GraphicKind.PROCESS,
                excerpt(unit.text, 140),
                0.6,
                frame.cue("process", unit, 0.6),
                False,
            )
        )
    quoted = _QUOTED.search(unit.text)
    if quoted:
        out.append(
            (
                GraphicKind.QUOTE,
                quoted.group(1).strip(),
                0.6,
                frame.cue("quote_marks", unit, 0.6),
                False,
            )
        )
    return out


def _with_id(opp: Opportunity, counter: int) -> Opportunity:
    from dataclasses import replace

    return replace(opp, opportunity_id=opportunity_id(counter))
