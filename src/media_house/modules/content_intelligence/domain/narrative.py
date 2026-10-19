"""Narrative roles: what each passage DOES in the communication (not what it is about).

A passage may hold several roles or none (``unclassified``). Position alone never assigns a role:
a recording need not open with an introduction or close with a conclusion. Roles are cues the
speaker gave in words (plus a few structural facts such as an answer to an earlier question), so
they are heuristic and carry the cue as evidence.
"""

from media_house.modules.content_intelligence.domain.frame import Frame
from media_house.modules.content_intelligence.domain.keyinfo import is_question, is_statistic
from media_house.modules.content_intelligence.domain.model import (
    Evidence,
    KeyItem,
    RoleLabel,
    Score,
)
from media_house.modules.content_intelligence.domain.structure import Structure
from media_house.modules.content_intelligence.domain.values import (
    KeyKind,
    Method,
    Role,
    ScoreName,
)

#: (role, cue, minimum weight, how many leading sentences are searched; 0 = the whole passage)
_CUE_ROLES: tuple[tuple[Role, str, float, int], ...] = (
    (Role.INTRODUCTION, "intro", 0.8, 3),
    (Role.CONTEXT_SETTING, "context", 0.6, 3),
    (Role.EXPLANATION, "explanation", 0.5, 0),
    (Role.EXAMPLE, "example", 0.8, 2),
    (Role.DEMONSTRATION, "demonstration", 0.7, 0),
    (Role.COUNTERARGUMENT, "counter", 0.7, 2),
    (Role.CAVEAT, "caveat", 0.8, 1),
    (Role.RECAP, "recap", 0.9, 2),
    (Role.EMPHASIS, "emphasis", 0.7, 0),
    (Role.TURNING_POINT, "turning", 0.7, 0),
    (Role.RESOLUTION, "resolution", 0.7, 0),
    (Role.CONCLUSION, "conclusion", 0.8, 0),
    (Role.CALL_TO_ACTION, "call_to_action", 0.9, 0),
    (Role.ANECDOTE, "anecdote", 0.7, 0),
    (Role.HUMOR, "humor", 0.7, 0),
)
_MAIN_POINT_KINDS = frozenset(
    {
        KeyKind.CLAIM,
        KeyKind.DECISION,
        KeyKind.CONCLUSION,
        KeyKind.DEFINITION,
        KeyKind.RECOMMENDATION,
        KeyKind.FACT,
        KeyKind.QUOTABLE,
    }
)
_TRANSITION_MAX_WORDS = 40
_MAIN_POINT_IMPORTANCE = 0.6
_HOOK_SCORE = 0.55


def _add(
    labels: list[RoleLabel],
    role: Role,
    evidence: Evidence,
    weight: float | None,
    method: Method = Method.HEURISTIC,
) -> None:
    if all(label.role is not role for label in labels):
        labels.append(RoleLabel(role=role, method=method, confidence=weight, evidence=(evidence,)))


def cue_roles(frame: Frame, structure: Structure, items: list[KeyItem]) -> list[list[RoleLabel]]:
    lex = frame.lexicon
    by_segment: dict[str, list[KeyItem]] = {}
    for item in items:
        if item.segment_id:
            by_segment.setdefault(item.segment_id, []).append(item)
    out: list[list[RoleLabel]] = []
    for k, rng in enumerate(structure.segments):
        labels: list[RoleLabel] = []
        seg_items = by_segment.get(f"s{k + 1:05d}", [])
        units = [frame.units[i] for i in rng]

        for role, cue, minimum, lead in _CUE_ROLES:
            scope = units[:lead] if lead else units
            for unit in scope:
                weight = lex.weight(cue, unit.text)
                if weight >= minimum:
                    _add(labels, role, frame.cue(cue, unit, weight), weight)
                    break
        first = units[0]
        if lex.weight("transition", first.text) >= 0.8 and (
            len(units) <= 3 or sum(u.words for u in units) <= _TRANSITION_MAX_WORDS
        ):
            _add(labels, Role.TRANSITION, frame.cue("transition", first, 0.8), 0.8)
        if any(i.kind in {KeyKind.EXPLANATION, KeyKind.DEFINITION} for i in seg_items):
            item = next(i for i in seg_items if i.kind in {KeyKind.EXPLANATION, KeyKind.DEFINITION})
            _add(labels, Role.EXPLANATION, item.evidence[0], item.confidence)
        evidence_unit = next(
            (u for u in units if lex.weight("evidence", u.text) >= 0.6 and is_statistic(u.text)),
            None,
        )
        if evidence_unit is not None:
            _add(labels, Role.SUPPORTING_EVIDENCE, frame.cue("evidence", evidence_unit, 0.6), 0.6)
        questions = [u for u in units if is_question(u, frame)]
        if questions and (is_question(first, frame) or len(questions) * 2 >= len(units)):
            _add(labels, Role.QUESTION, frame.cue("question", questions[0], 0.6), 0.6)
        for item in seg_items:
            if item.kind is KeyKind.ANSWER:
                _add(labels, Role.ANSWER, item.evidence[0], item.confidence)
            if item.kind is KeyKind.CONCLUSION:
                _add(labels, Role.CONCLUSION, item.evidence[0], item.confidence)
        audio = frame.audio
        if audio is not None:
            for unit in units:
                if unit.span.known and audio.laughter_between(
                    unit.span.start or 0.0, unit.span.end or 0.0
                ):
                    evidence = Evidence(
                        kind="audio:laughter",
                        method=Method.OBSERVED,
                        unit_ids=(unit.unit_id,),
                        weight=0.6,
                        detail="laughter was detected during the sentence",
                    )
                    _add(labels, Role.HUMOR, evidence, 0.6, Method.OBSERVED)
                    break
        out.append(labels)
    return out


def finalize_roles(
    frame: Frame,
    structure: Structure,
    items: list[KeyItem],
    labels: list[list[RoleLabel]],
    scores: list[list[Score]],
) -> list[tuple[RoleLabel, ...]]:
    by_segment: dict[str, list[KeyItem]] = {}
    for item in items:
        if item.segment_id:
            by_segment.setdefault(item.segment_id, []).append(item)
    out: list[tuple[RoleLabel, ...]] = []
    for k in range(len(structure.segments)):
        current = list(labels[k])
        importance = next((s.value for s in scores[k] if s.name is ScoreName.IMPORTANCE), None)
        hook = next((s.value for s in scores[k] if s.name is ScoreName.HOOK), None)
        seg_items = by_segment.get(f"s{k + 1:05d}", [])
        main = next((i for i in seg_items if i.kind in _MAIN_POINT_KINDS), None)
        if main is not None and importance is not None and importance >= _MAIN_POINT_IMPORTANCE:
            current.append(
                RoleLabel(
                    role=Role.MAIN_POINT,
                    method=Method.HEURISTIC,
                    confidence=importance,
                    evidence=(
                        *main.evidence[:1],
                        Evidence(
                            kind="importance",
                            method=Method.HEURISTIC,
                            unit_ids=main.unit_ids,
                            weight=importance,
                            detail="high importance and it states a key item",
                        ),
                    ),
                )
            )
        if k == 0 and hook is not None and hook >= _HOOK_SCORE:
            current.append(
                RoleLabel(
                    role=Role.HOOK,
                    method=Method.HEURISTIC,
                    confidence=hook,
                    evidence=(
                        Evidence(
                            kind="hook_score",
                            method=Method.HEURISTIC,
                            unit_ids=(frame.units[structure.segments[k].start].unit_id,),
                            weight=hook,
                            detail="the opening passage scores as a hook",
                        ),
                    ),
                )
            )
        if not current:
            current.append(
                RoleLabel(
                    role=Role.UNCLASSIFIED,
                    method=Method.HEURISTIC,
                    evidence=(
                        Evidence(
                            kind="no_cue",
                            method=Method.HEURISTIC,
                            unit_ids=(frame.units[structure.segments[k].start].unit_id,),
                            detail="no narrative cue matched",
                        ),
                    ),
                )
            )
        out.append(tuple(current))
    return out
