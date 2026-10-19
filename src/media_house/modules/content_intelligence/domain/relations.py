"""Typed links between passages: what depends on, answers, repeats or qualifies what.

Every relationship names its two ends, the evidence that justifies it and how strong that
evidence is. A link is only created when a cue or a measurement supports it. Repetition is
classified by what the later passage ADDS; "nothing new" is one possible answer, never an
instruction to remove anything.
"""

import itertools
from dataclasses import dataclass, replace

from media_house.modules.content_intelligence.domain.frame import Frame, scale
from media_house.modules.content_intelligence.domain.ids import (
    item_id,
    relationship_id,
    segment_id,
)
from media_house.modules.content_intelligence.domain.keyinfo import is_question
from media_house.modules.content_intelligence.domain.lexical import containment, tokens
from media_house.modules.content_intelligence.domain.model import (
    Evidence,
    KeyItem,
    Ref,
    Relationship,
    Topic,
    excerpt,
)
from media_house.modules.content_intelligence.domain.values import (
    Adds,
    KeyKind,
    Method,
    Origin,
    RefKind,
    RelationKind,
    SegmentLevel,
)

_PRIORITY = {
    RelationKind.ANSWERS: 0,
    RelationKind.DEPENDS_ON: 1,
    RelationKind.REFERENCES: 2,
    RelationKind.QUALIFIES: 3,
    RelationKind.CONTRADICTS: 4,
    RelationKind.SUMMARIZES: 5,
    RelationKind.ILLUSTRATES: 6,
    RelationKind.EXPLAINS: 7,
    RelationKind.SUPPORTS: 8,
    RelationKind.REPEATS: 9,
    RelationKind.OVERLAPS: 10,
    RelationKind.CONTINUES: 11,
    RelationKind.TRANSITIONS_TO: 12,
}
_MAX_DEFINITIONS = 200
_MAX_OVERLAPS_PER_SEGMENT = 3
_RARE_SHARE = 0.10


@dataclass(frozen=True, slots=True)
class Relations:
    relationships: tuple[Relationship, ...]
    #: ANSWER and OPEN_QUESTION items created while linking questions.
    new_items: tuple[KeyItem, ...]
    #: Per segment index: relationships where it depends on / references something earlier.
    dependencies: dict[int, list[str]]
    #: Per segment index: relationships where it repeats or overlaps something.
    redundancies: dict[int, list[str]]
    #: Per segment index: the earlier sentences a reader needs for it (unit ids).
    context: dict[int, list[str]]
    #: What each overlap link adds, keyed by (later segment index, earlier segment index).
    adds: dict[tuple[int, int], Adds]


def _seg(k: int) -> Ref:
    return Ref(kind=RefKind.SEGMENT, id=segment_id(k))


def classify_adds(frame: Frame, earlier: int, later: int) -> tuple[Adds, Evidence]:
    """What the later sentence contributes beyond the earlier one (precedence as listed)."""
    lex = frame.lexicon
    a, b = frame.units[earlier], frame.units[later]
    checks: tuple[tuple[Adds, float, str], ...] = (
        (Adds.RECAP, max(lex.weight("recap", b.text), lex.weight("conclusion", b.text)), "recap"),
        (Adds.CALLBACK, lex.weight("callback", b.text), "callback"),
        (Adds.NEW_EXAMPLE, lex.weight("example", b.text), "example"),
        (
            Adds.QUALIFICATION,
            max(lex.weight("caveat", b.text), lex.weight("hedge", b.text)),
            "qualification",
        ),
    )
    for kind, weight, cue in checks:
        if weight >= 0.5:
            return kind, frame.cue(cue, b, weight)
    new_numbers = set(_digits(b.text)) - set(_digits(a.text))
    new_terms = set(frame.stats.stems[later]) - set(frame.stats.stems[earlier])
    rare_new = [t for t in new_terms if frame.stats.idf.get(t, 0) >= 2.5]
    if new_numbers or (
        len(rare_new) >= 2 and len(new_terms) >= 0.4 * max(1, len(set(frame.stats.stems[later])))
    ):
        detail = f"adds {', '.join(sorted(new_numbers) or sorted(rare_new)[:3])}"
        return Adds.NEW_EVIDENCE, Evidence(
            kind="new_terms",
            method=Method.DETERMINISTIC,
            unit_ids=(b.unit_id,),
            excerpt=excerpt(b.text, 100),
            weight=0.6,
            detail=detail,
        )
    if a.speaker and b.speaker and a.speaker != b.speaker:
        return Adds.DIFFERENT_PERSPECTIVE, Evidence(
            kind="speaker",
            method=Method.OBSERVED,
            unit_ids=(b.unit_id,),
            weight=0.5,
            detail=f"{a.speaker} then {b.speaker}",
        )
    if b.words >= 1.5 * a.words and lex.weight("explanation", b.text) >= 0.5:
        return Adds.STRONGER_EXPLANATION, frame.cue("explanation", b, 0.5)
    return Adds.NOTHING_NEW, Evidence(
        kind="no_new_terms",
        method=Method.DETERMINISTIC,
        unit_ids=(b.unit_id,),
        weight=0.5,
        detail="no new number, term, example, qualification or speaker",
    )


def _digits(text: str) -> list[str]:
    return [t for t in tokens(text) if any(c.isdigit() for c in t)]


def _answers(frame: Frame, items: list[KeyItem]) -> tuple[list[Relationship], list[KeyItem]]:
    lex = frame.lexicon
    horizon = frame.thresholds.answer_horizon
    index_of = {u.unit_id: u.index for u in frame.units}
    relationships: list[Relationship] = []
    created: list[KeyItem] = []
    threshold = frame.overlap_threshold
    for question in [i for i in items if i.kind is KeyKind.QUESTION]:
        qi = index_of[question.unit_ids[0]]
        best: tuple[float, int, list[Evidence]] | None = None
        for j in range(qi + 1, min(len(frame.units), qi + 1 + horizon)):
            unit = frame.units[j]
            if is_question(unit, frame):
                continue
            lead = lex.weight("answer_lead", unit.text) if j - qi <= 3 else 0.0
            overlap = scale(frame.similarity.similarity(qi, j), 0.3 * threshold, threshold)
            other_voice = (
                0.3
                if j == qi + 1
                and unit.speaker
                and frame.units[qi].speaker not in {None, unit.speaker}
                else 0.0
            )
            score = 0.6 * lead + 0.8 * overlap + other_voice
            if score < 0.35 or (best is not None and score <= best[0]):
                continue
            why = [
                Evidence(
                    kind="similarity",
                    method=Method.DETERMINISTIC,
                    unit_ids=(unit.unit_id, question.unit_ids[0]),
                    weight=overlap,
                    detail=f"overlap with the question {overlap:.2f}",
                )
            ]
            if lead:
                why.append(frame.cue("answer_lead", unit, lead))
            if other_voice:
                why.append(
                    Evidence(
                        kind="speaker_change",
                        method=Method.OBSERVED,
                        unit_ids=(unit.unit_id,),
                        weight=0.3,
                        detail="a different speaker replies",
                    )
                )
            best = (score, j, why)
        if best is None:
            nxt = frame.units[qi + 1] if qi + 1 < len(frame.units) else None
            asked_of_someone = nxt is None or (
                nxt.speaker and nxt.speaker != frame.units[qi].speaker
            )
            if asked_of_someone:
                created.append(_open_question(frame, question, len(items) + len(created)))
            continue
        score, j, why = best
        unit = frame.units[j]
        answer = KeyItem(
            item_id=item_id(len(items) + len(created)),
            kind=KeyKind.ANSWER,
            text=unit.text,
            origin=Origin.DIRECT,
            method=Method.HEURISTIC,
            unit_ids=(unit.unit_id,),
            segment_id=segment_id(frame.segment_of[j]),
            span=unit.span,
            evidence=tuple(why),
            confidence=min(1.0, round(score, 3)),
        )
        created.append(answer)
        relationships.append(
            Relationship(
                rel_id="",
                kind=RelationKind.ANSWERS,
                source=Ref(kind=RefKind.ITEM, id=answer.item_id),
                target=Ref(kind=RefKind.ITEM, id=question.item_id),
                method=Method.HEURISTIC,
                confidence=answer.confidence,
                evidence=tuple(why),
                note="delayed answer" if j - qi > 3 else "",
            )
        )
    return relationships, created


def _open_question(frame: Frame, question: KeyItem, number: int) -> KeyItem:
    return KeyItem(
        item_id=item_id(number),
        kind=KeyKind.OPEN_QUESTION,
        text=question.text,
        origin=Origin.DIRECT,
        method=Method.HEURISTIC,
        unit_ids=question.unit_ids,
        segment_id=question.segment_id,
        span=question.span,
        evidence=(
            Evidence(
                kind="no_answer_found",
                method=Method.HEURISTIC,
                unit_ids=question.unit_ids,
                excerpt=excerpt(question.text, 100),
                weight=0.5,
                detail=f"no reply within {frame.thresholds.answer_horizon} sentences",
            ),
        ),
        confidence=0.5,
    )


def _definitions(frame: Frame, items: list[KeyItem]) -> list[Relationship]:
    out: list[Relationship] = []
    index_of = {u.unit_id: u.index for u in frame.units}
    n = len(frame.units)
    document_frequency = dict.fromkeys(frame.stats.idf, 0)
    for stems in frame.stats.stems:
        for t in set(stems):
            document_frequency[t] += 1
    for definition in [i for i in items if i.kind is KeyKind.DEFINITION][:_MAX_DEFINITIONS]:
        di = index_of[definition.unit_ids[0]]
        rare = sorted(
            {t for t in frame.stats.stems[di] if document_frequency[t] <= max(3, _RARE_SHARE * n)},
            key=lambda t: (-frame.stats.idf[t], t),
        )[:2]
        if not rare:
            continue
        home = frame.segment_of[di]
        users: dict[int, tuple[int, int]] = {}
        for j in range(di + 1, n):
            seg = frame.segment_of[j]
            if seg == home:
                continue
            hits = sum(1 for t in rare if t in frame.stats.stems[j])
            if hits and (seg not in users or hits > users[seg][1]):
                users[seg] = (j, hits)
        terms = ", ".join(frame.stats.surface.get(t, t) for t in rare)
        for seg, (j, hits) in sorted(users.items())[:6]:
            out.append(
                Relationship(
                    rel_id="",
                    kind=RelationKind.DEPENDS_ON,
                    source=_seg(seg),
                    target=_seg(home),
                    method=Method.HEURISTIC,
                    confidence=min(0.8, 0.45 + 0.15 * hits),
                    evidence=(
                        Evidence(
                            kind="defined_term_reused",
                            method=Method.DETERMINISTIC,
                            unit_ids=(frame.units[j].unit_id, definition.unit_ids[0]),
                            excerpt=excerpt(frame.units[j].text, 100),
                            weight=min(0.8, 0.45 + 0.15 * hits),
                            detail=f"uses {terms} defined earlier",
                        ),
                    ),
                )
            )
    return out


def _boundary_links(frame: Frame, segments: list[range], topics: list[Topic]) -> list[Relationship]:
    out: list[Relationship] = []
    lex = frame.lexicon
    topic_of_segment: dict[str, str] = {}
    for topic in topics:
        if topic.level is SegmentLevel.TOPIC:
            for sid in topic.segment_ids:
                topic_of_segment[sid] = topic.topic_id
    for k in range(1, len(segments)):
        first = frame.units[segments[k].start]
        here, before = _seg(k), _seg(k - 1)
        text = first.text
        lead = tokens(text)[:1]
        pronoun = bool(lead and lead[0] in lex.pronouns)
        connector = bool(lex.anaphora and lex.anaphora.search(text))
        if pronoun or connector:
            weight = 0.5 if pronoun else 0.3
            out.append(
                Relationship(
                    rel_id="",
                    kind=RelationKind.REFERENCES,
                    source=here,
                    target=before,
                    method=Method.HEURISTIC,
                    confidence=weight,
                    evidence=(frame.cue("reference_lead", first, weight),),
                    note="opens by pointing back at the previous passage",
                )
            )
        for kind, cue, minimum, note in (
            (RelationKind.QUALIFIES, "caveat", 0.8, ""),
            (RelationKind.ILLUSTRATES, "example", 0.8, ""),
            (RelationKind.EXPLAINS, "explanation", 0.5, ""),
            (RelationKind.SUPPORTS, "evidence", 0.6, ""),
            (RelationKind.CONTRADICTS, "counter", 0.7, "counterargument cue"),
        ):
            weight = lex.weight(cue, text)
            if weight >= minimum:
                out.append(
                    Relationship(
                        rel_id="",
                        kind=kind,
                        source=here,
                        target=before,
                        method=Method.HEURISTIC,
                        confidence=weight * 0.9,
                        evidence=(frame.cue(cue, first, weight),),
                        note=note,
                    )
                )
        for i in segments[k]:
            unit = frame.units[i]
            if lex.weight("correction", unit.text) >= 0.8:
                target = _best_earlier(frame, i, segments[k].start, 20)
                out.append(
                    Relationship(
                        rel_id="",
                        kind=RelationKind.CONTRADICTS,
                        source=here,
                        target=_seg(target if target is not None else k - 1),
                        method=Method.HEURISTIC,
                        confidence=0.6,
                        evidence=(frame.cue("correction", unit, 0.8),),
                        note="explicit correction cue",
                    )
                )
            recap = max(lex.weight("recap", unit.text), lex.weight("conclusion", unit.text))
            if recap >= 0.8 and k >= 2:
                prior_topic = topic_of_segment.get(segment_id(k - 1))
                own_topic = topic_of_segment.get(segment_id(k))
                target_ref = (
                    Ref(kind=RefKind.TOPIC, id=prior_topic)
                    if prior_topic and prior_topic != own_topic
                    else before
                )
                out.append(
                    Relationship(
                        rel_id="",
                        kind=RelationKind.SUMMARIZES,
                        source=here,
                        target=target_ref,
                        method=Method.HEURISTIC,
                        confidence=recap * 0.9,
                        evidence=(frame.cue("recap", unit, recap),),
                    )
                )
                break
        same_topic = topic_of_segment.get(segment_id(k)) == topic_of_segment.get(segment_id(k - 1))
        if same_topic and not any(r.target == before and r.source == here for r in out):
            out.append(
                Relationship(
                    rel_id="",
                    kind=RelationKind.CONTINUES,
                    source=here,
                    target=before,
                    method=Method.DETERMINISTIC,
                    confidence=0.5,
                    evidence=(
                        Evidence(
                            kind="same_topic",
                            method=Method.DETERMINISTIC,
                            unit_ids=(first.unit_id,),
                            weight=0.5,
                            detail="adjacent passages of one topic",
                        ),
                    ),
                )
            )
    return out


def _best_earlier(frame: Frame, index: int, segment_start: int, horizon: int) -> int | None:
    best: tuple[float, int] | None = None
    for j in range(max(0, segment_start - horizon), segment_start):
        s = frame.similarity.similarity(index, j)
        if s > 0 and (best is None or s > best[0]):
            best = (s, frame.segment_of[j])
    return best[1] if best else None


def _topic_transitions(frame: Frame, topics: list[Topic]) -> list[Relationship]:
    out: list[Relationship] = []
    for level in (SegmentLevel.TOPIC, SegmentLevel.SECTION):
        ordered = [t for t in topics if t.level is level]
        for a, b in itertools.pairwise(ordered):
            boundary = b.boundary
            out.append(
                Relationship(
                    rel_id="",
                    kind=RelationKind.TRANSITIONS_TO,
                    source=Ref(kind=RefKind.TOPIC, id=a.topic_id),
                    target=Ref(kind=RefKind.TOPIC, id=b.topic_id),
                    method=Method.DETERMINISTIC,
                    confidence=boundary.strength if boundary else None,
                    evidence=boundary.evidence if boundary else (),
                    note=f"{level.value} boundary",
                )
            )
    return out


def _repeats(frame: Frame) -> tuple[list[Relationship], dict[tuple[int, int], Adds]]:
    threshold = frame.overlap_threshold
    best: dict[tuple[int, int], tuple[float, int, int]] = {}
    for i, near in frame.neighbours.items():
        for j, sim in near:
            if sim < threshold or j <= i:
                continue
            a, b = frame.segment_of[i], frame.segment_of[j]
            if a == b:
                continue
            if (a, b) not in best or sim > best[(a, b)][0]:
                best[(a, b)] = (sim, i, j)
    per_target: dict[int, list[tuple[float, int, int, int]]] = {}
    for (a, b), (sim, i, j) in best.items():
        per_target.setdefault(b, []).append((sim, a, i, j))
    out: list[Relationship] = []
    adds: dict[tuple[int, int], Adds] = {}
    for b, found in sorted(per_target.items()):
        for sim, a, i, j in sorted(found, key=lambda f: (-f[0], f[1]))[:_MAX_OVERLAPS_PER_SEGMENT]:
            first, second = frame.units[i], frame.units[j]
            tok_a, tok_b = tokens(first.text), tokens(second.text)
            overlap = containment(tok_a, tok_b)
            verbatim = tok_a == tok_b or overlap >= frame.thresholds.near_verbatim
            kind = RelationKind.REPEATS if verbatim else RelationKind.OVERLAPS
            added, why = classify_adds(frame, i, j)
            adds[(b, a)] = added
            similarity = Evidence(
                kind="similarity",
                method=Method.DETERMINISTIC,
                unit_ids=(first.unit_id, second.unit_id),
                excerpt=excerpt(second.text, 100),
                weight=min(1.0, sim),
                detail=f"{frame.similarity.identity} {sim:.2f}; word overlap {overlap:.2f}",
            )
            out.append(
                Relationship(
                    rel_id="",
                    kind=kind,
                    source=_seg(b),
                    target=_seg(a),
                    method=Method.HEURISTIC,
                    confidence=min(1.0, sim),
                    evidence=(similarity, why),
                    adds=added,
                    note="later passage restates the earlier one",
                )
            )
    return out, adds


def build(
    frame: Frame, segments: list[range], items: list[KeyItem], topics: list[Topic]
) -> Relations:
    answers, new_items = _answers(frame, items)
    repeats, adds = _repeats(frame)
    raw = [
        *answers,
        *_definitions(frame, items),
        *_boundary_links(frame, segments, topics),
        *_topic_transitions(frame, topics),
        *repeats,
    ]
    raw.sort(key=lambda r: (_PRIORITY[r.kind], -(r.confidence or 0.0), r.source.id, r.target.id))
    kept = raw[: frame.settings.max_relationships]
    final = tuple(replace(r, rel_id=relationship_id(n)) for n, r in enumerate(kept))
    dependencies: dict[int, list[str]] = {}
    redundancies: dict[int, list[str]] = {}
    context: dict[int, list[str]] = {}
    index_of = {segment_id(k): k for k in range(len(segments))}
    depends = {RelationKind.DEPENDS_ON, RelationKind.REFERENCES}
    for r in final:
        if r.source.kind is not RefKind.SEGMENT or r.source.id not in index_of:
            continue
        k = index_of[r.source.id]
        if r.kind in depends or r.kind in {RelationKind.QUALIFIES, RelationKind.EXPLAINS}:
            dependencies.setdefault(k, []).append(r.rel_id)
        if r.kind in {RelationKind.REPEATS, RelationKind.OVERLAPS}:
            redundancies.setdefault(k, []).append(r.rel_id)
        if r.kind in depends and r.target.kind is RefKind.SEGMENT and r.target.id in index_of:
            target = segments[index_of[r.target.id]]
            context.setdefault(k, []).append(frame.units[target.stop - 1].unit_id)
    return Relations(final, tuple(new_items), dependencies, redundancies, context, adds)
