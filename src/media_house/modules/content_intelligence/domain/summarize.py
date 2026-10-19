"""Extractive summaries: the sentences that best represent a passage, quoted in their own words.

An extractive summary cannot invent anything and cannot lose a qualification silently: when a
chosen sentence is qualified by the next one (``but only if ...``) the qualifier is pulled in
too. Higher levels avoid reusing the sentences a lower level already chose. A language model may
later replace a summary with a checked paraphrase (``refine``); this pass is the grounded floor.
"""

from collections.abc import Collection, Mapping, Sequence

from media_house.modules.content_intelligence.domain.frame import Frame
from media_house.modules.content_intelligence.domain.model import (
    Evidence,
    KeyItem,
    Summary,
    excerpt,
)
from media_house.modules.content_intelligence.domain.values import KeyKind, Method, Origin

KIND_WEIGHT: dict[KeyKind, float] = {
    KeyKind.CONCLUSION: 1.0,
    KeyKind.DECISION: 0.9,
    KeyKind.DEFINITION: 0.8,
    KeyKind.RECOMMENDATION: 0.7,
    KeyKind.CLAIM: 0.6,
    KeyKind.FACT: 0.6,
    KeyKind.ACTION_ITEM: 0.6,
    KeyKind.ANSWER: 0.5,
    KeyKind.QUOTABLE: 0.4,
    KeyKind.EXPLANATION: 0.4,
    KeyKind.ARGUMENT: 0.5,
    KeyKind.EXAMPLE: 0.2,
    KeyKind.CAVEAT: 0.2,
    KeyKind.QUESTION: 0.1,
    KeyKind.OPEN_QUESTION: 0.1,
}
_MIN_WORDS = 8
_MIN_UNIT_WORDS = 4
_REDUNDANCY = 0.5
_MAX_CHARS = 700
_TAKEAWAY_KINDS = frozenset(
    {
        KeyKind.CONCLUSION,
        KeyKind.DECISION,
        KeyKind.RECOMMENDATION,
        KeyKind.DEFINITION,
        KeyKind.CLAIM,
        KeyKind.FACT,
        KeyKind.ACTION_ITEM,
        KeyKind.QUOTABLE,
        KeyKind.ANSWER,
    }
)


def _unit_score(
    frame: Frame,
    i: int,
    group: Sequence[int],
    by_unit: Mapping[int, list[KeyItem]],
    avoid: Collection[int],
) -> float:
    central = frame.centrality_scale(frame.similarity.group_similarity([i], list(group)))
    key = max((KIND_WEIGHT.get(k.kind, 0.0) for k in by_unit.get(i, ())), default=0.0)
    unit_words = max(1, len(frame.stats.words[i]))
    density = min(1.0, len(frame.stats.stems[i]) / unit_words * 1.4)
    return 0.5 * central + 0.3 * key + 0.2 * density - (0.35 if i in avoid else 0.0)


def summarize(
    frame: Frame,
    indices: Sequence[int],
    count: int,
    by_unit: Mapping[int, list[KeyItem]],
    avoid: Collection[int] = (),
) -> Summary | None:
    lex = frame.lexicon
    usable = [
        i
        for i in indices
        if frame.units[i].words >= _MIN_UNIT_WORDS and not lex.weight("filler", frame.units[i].text)
    ]
    statements = [
        i for i in usable if not any(k.kind is KeyKind.QUESTION for k in by_unit.get(i, ()))
    ]
    pool = statements or usable
    if sum(frame.units[i].words for i in pool) < _MIN_WORDS:
        return None
    scores = {i: _unit_score(frame, i, indices, by_unit, avoid) for i in pool}
    chosen: list[int] = []
    while pool and len(chosen) < count:
        best = max(
            (i for i in pool if i not in chosen),
            key=lambda i: (
                scores[i]
                - _REDUNDANCY
                * max((frame.similarity.similarity(i, c) for c in chosen), default=0.0),
                -i,
            ),
            default=None,
        )
        if best is None:
            break
        chosen.append(best)
    index_of = {u.unit_id: u.index for u in frame.units}
    qualifiers = [
        index_of[q]
        for i in chosen
        for item in by_unit.get(i, ())
        for q in item.qualified_by
        if index_of.get(q) is not None and index_of[q] not in chosen
    ]
    picked = sorted({*chosen, *qualifiers})
    while len(picked) > 1 and sum(len(frame.units[i].text) + 1 for i in picked) > _MAX_CHARS:
        droppable = [i for i in picked if i in chosen and i not in qualifiers]
        if not droppable:
            break
        picked.remove(min(droppable, key=lambda i: scores[i]))
    return Summary(
        text=" ".join(frame.units[i].text for i in picked),
        origin=Origin.DIRECT,
        method=Method.HEURISTIC,
        unit_ids=tuple(frame.units[i].unit_id for i in picked),
        qualified_by=tuple(frame.units[i].unit_id for i in qualifiers if i in picked),
        evidence=tuple(
            Evidence(
                kind="summary_selection",
                method=Method.HEURISTIC,
                unit_ids=(frame.units[i].unit_id,),
                excerpt=excerpt(frame.units[i].text, 80),
                weight=max(0.0, min(1.0, scores.get(i, 0.0))),
                detail="chosen for representativeness, key content and low redundancy"
                if i in scores
                else "added because it qualifies a chosen sentence",
            )
            for i in picked
        ),
    )


def takeaways(
    frame: Frame,
    items: Sequence[KeyItem],
    importance_by_segment: Mapping[str, float],
    count: int,
) -> list[Summary]:
    index_of = {u.unit_id: u.index for u in frame.units}
    scored = [
        (
            KIND_WEIGHT[item.kind]
            * (0.5 + 0.5 * importance_by_segment.get(item.segment_id or "", 0.5))
            * (item.confidence if item.confidence is not None else 0.5),
            item,
        )
        for item in items
        if item.kind in _TAKEAWAY_KINDS
    ]
    scored.sort(key=lambda s: (-s[0], index_of[s[1].unit_ids[0]]))
    chosen: list[KeyItem] = []
    for _score, item in scored:
        i = index_of[item.unit_ids[0]]
        if any(
            frame.similarity.similarity(i, index_of[c.unit_ids[0]]) >= 0.6 * frame.overlap_threshold
            for c in chosen
        ):
            continue
        chosen.append(item)
        if len(chosen) == count:
            break
    out: list[Summary] = []
    for item in sorted(chosen, key=lambda k: index_of[k.unit_ids[0]]):
        extra = [frame.units[index_of[q]] for q in item.qualified_by if q in index_of]
        out.append(
            Summary(
                text=" ".join([item.text, *(u.text for u in extra)]),
                origin=Origin.DIRECT,
                method=Method.HEURISTIC,
                unit_ids=(*item.unit_ids, *(u.unit_id for u in extra)),
                qualified_by=tuple(u.unit_id for u in extra),
                evidence=item.evidence,
            )
        )
    return out
