"""The scores. Each is a documented formula over named components, in [0, 1], advisory only.

* importance      How much a reader needs the passage to understand the recording: it is central
                  to the subject, carries key content, others depend on it, only it says it, or
                  the speaker flags it. Delivery (volume, emphasis, pace) is NOT an input.
* information_density  Distinct meaningful content per word: content-word share, share of terms
                  new to the passage, discounted by filler and internal repetition. Speaking
                  speed and pauses are not inputs; terms per minute are reported, not scored.
* uniqueness      Share of the passage not already said elsewhere IN THIS RECORDING.
* standalone      Whether the passage can be understood alone; penalties name what is missing.
* highlight / hook  How well a passage works as a memorable, self-contained clip / an opening.
                  Text meaning decides; measured delivery adds at most a small corroboration.
* removal_candidate  How much a passage looks redundant, tangential and low-value, REDUCED by
                  everything that argues for keeping it. Pauses and hesitations never count.

Missing inputs make a score ``None`` with a reason, never zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.frame import Frame, scale
from media_house.modules.content_intelligence.domain.keyinfo import is_question, is_statistic
from media_house.modules.content_intelligence.domain.lexical import containment, tokens
from media_house.modules.content_intelligence.domain.model import (
    Evidence,
    KeyItem,
    Relationship,
    Score,
    excerpt,
)
from media_house.modules.content_intelligence.domain.relations import Relations
from media_house.modules.content_intelligence.domain.structure import Structure
from media_house.modules.content_intelligence.domain.summarize import KIND_WEIGHT
from media_house.modules.content_intelligence.domain.values import (
    Adds,
    KeyKind,
    Method,
    RefKind,
    RelationKind,
    Role,
    ScoreName,
)

_DEPENDENT = frozenset(
    {
        RelationKind.DEPENDS_ON,
        RelationKind.REFERENCES,
        RelationKind.EXPLAINS,
        RelationKind.SUPPORTS,
        RelationKind.QUALIFIES,
        RelationKind.ILLUSTRATES,
        RelationKind.CONTRADICTS,
    }
)
_PROTECTED = frozenset(
    {
        Role.TRANSITION,
        Role.RECAP,
        Role.HUMOR,
        Role.ANECDOTE,
        Role.CAVEAT,
        Role.CONCLUSION,
        Role.CALL_TO_ACTION,
        Role.ANSWER,
        Role.CONTEXT_SETTING,
        Role.INTRODUCTION,
    }
)
_MIN_WORDS_FOR_DENSITY = 4
#: A sentence is compared with this many sentences after it when looking for repetition.
_REPEAT_LOOKAHEAD = 6
_INTRO_IDF = 2.0


def _ev(kind: str, units: tuple[str, ...], weight: float | None, detail: str) -> Evidence:
    return Evidence(
        kind=kind, method=Method.DETERMINISTIC, unit_ids=units, weight=weight, detail=detail
    )


@dataclass(frozen=True, slots=True)
class _Seg:
    k: int
    rng: range
    unit_ids: tuple[str, ...]
    words: int
    stems: frozenset[str]


def _segments(frame: Frame, structure: Structure) -> list[_Seg]:
    out: list[_Seg] = []
    for k, rng in enumerate(structure.segments):
        stems = frozenset(t for i in rng for t in frame.stats.stems[i])
        out.append(
            _Seg(
                k,
                rng,
                tuple(frame.units[i].unit_id for i in rng),
                sum(len(frame.stats.words[i]) for i in rng),
                stems,
            )
        )
    return out


def _density(frame: Frame, seg: _Seg, previous: list[_Seg]) -> Score:
    if seg.words < _MIN_WORDS_FOR_DENSITY:
        return Score(
            name=ScoreName.INFORMATION_DENSITY,
            value=None,
            method=Method.HEURISTIC,
            reason="fewer than four words: too short to estimate",
        )
    content = sum(len(frame.stats.stems[i]) for i in seg.rng)
    lexical = content / seg.words
    seen = frozenset().union(*(p.stems for p in previous[-2:])) if previous else frozenset()
    novelty = len(seg.stems - seen) / len(seg.stems) if seg.stems else 0.0
    fillers = frame.lexicon.fillers
    filler = sum(1 for i in seg.rng for w in frame.stats.words[i] if w in fillers) / seg.words
    repeated = 0
    pairs = list(seg.rng)
    for a in range(len(pairs)):
        for b in range(a + 1, min(len(pairs), a + 1 + _REPEAT_LOOKAHEAD)):
            if (
                containment(frame.stats.words[pairs[a]], frame.stats.words[pairs[b]])
                >= frame.thresholds.near_verbatim
            ):
                repeated += 1
                break
    repeat = repeated / max(1, len(pairs))
    value = (
        0.45 * scale(lexical, 0.30, 0.65)
        + 0.35 * novelty
        + 0.20 * (1 - min(1.0, 2 * filler + repeat))
    )
    span = frame.units[seg.rng.start].span
    last = frame.units[seg.rng.stop - 1].span
    duration = (last.end - span.start) if span.start is not None and last.end is not None else None
    evidence = [
        _ev("content_word_share", seg.unit_ids, lexical, f"{lexical:.2f} of words carry content"),
        _ev("novelty", seg.unit_ids, novelty, f"{novelty:.2f} of its terms are new to the passage"),
    ]
    if duration and duration > 0:
        per_minute = len(seg.stems) / (duration / 60)
        evidence.append(
            _ev(
                "terms_per_minute",
                seg.unit_ids,
                None,
                f"{per_minute:.1f} distinct terms per minute (reported, not scored)",
            )
        )
    else:
        evidence.append(
            _ev("duration", seg.unit_ids, None, "duration-normalised density unavailable: no times")
        )
    return Score(
        name=ScoreName.INFORMATION_DENSITY,
        value=round(value, 4),
        method=Method.HEURISTIC,
        evidence=tuple(evidence),
        components={
            "content_share": round(lexical, 4),
            "novelty": round(novelty, 4),
            "filler": round(filler, 4),
            "repeat": round(repeat, 4),
        },
    )


def _uniqueness(frame: Frame, seg: _Seg, single: bool) -> Score:
    if single:
        return Score(
            name=ScoreName.UNIQUENESS,
            value=1.0,
            method=Method.DETERMINISTIC,
            evidence=(
                _ev(
                    "scope", seg.unit_ids, None, "the recording has one passage: nothing to repeat"
                ),
            ),
        )
    threshold = frame.overlap_threshold
    low = 0.5 * threshold
    weighted = total = 0.0
    worst: tuple[float, int, int] | None = None
    for i in seg.rng:
        words = max(1, len(frame.stats.words[i]))
        best = 0.0
        other = -1
        for j, s in frame.neighbours.get(i, ()):
            if frame.segment_of[j] != seg.k:
                best, other = s, j
                break
        redundancy = scale(best, low, threshold)
        weighted += redundancy * words
        total += words
        if other >= 0 and (worst is None or best > worst[0]):
            worst = (best, i, other)
    value = 1.0 - (weighted / total if total else 0.0)
    evidence = [_ev("scope", seg.unit_ids, None, "compared with the rest of this recording only")]
    if worst and worst[0] >= low:
        evidence.append(
            Evidence(
                kind="closest_other_passage",
                method=Method.DETERMINISTIC,
                unit_ids=(frame.units[worst[1]].unit_id, frame.units[worst[2]].unit_id),
                excerpt=excerpt(frame.units[worst[2]].text, 100),
                weight=min(1.0, worst[0]),
                detail=f"{frame.similarity.identity} similarity {worst[0]:.2f}",
            )
        )
    return Score(
        name=ScoreName.UNIQUENESS,
        value=round(value, 4),
        method=Method.HEURISTIC,
        evidence=tuple(evidence),
        components={"redundant_share": round(1 - value, 4)},
    )


def _centrality(frame: Frame, seg: _Seg, topic_range: range | None) -> tuple[float, float]:
    group = list(seg.rng)
    recording = frame.centrality_scale(frame.similarity.group_similarity(group, frame.sample()))
    if topic_range is None or len(topic_range) == len(frame.units):
        return recording, recording
    topic = frame.centrality_scale(frame.similarity.group_similarity(group, list(topic_range)))
    return recording, topic


def score_all(
    frame: Frame,
    structure: Structure,
    items: list[KeyItem],
    relations: Relations,
    roles: list[set[Role]],
) -> list[list[Score]]:
    segs = _segments(frame, structure)
    n = len(segs)
    by_segment: dict[int, list[KeyItem]] = {}
    seg_index = {f"s{k + 1:05d}": k for k in range(n)}
    for item in items:
        if item.segment_id in seg_index:
            by_segment.setdefault(seg_index[item.segment_id], []).append(item)
    first_seen: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    for seg in segs:
        for t in seg.stems:
            first_seen.setdefault(t, seg.k)
            last_seen[t] = seg.k
    in_degree = _in_degree(relations.relationships, seg_index)
    topic_range = _topic_ranges(structure)

    all_scores: list[list[Score]] = []
    for seg in segs:
        k = seg.k
        density = _density(frame, seg, segs[:k])
        unique = _uniqueness(frame, seg, single=n == 1)
        intro = [
            t for t in seg.stems if first_seen[t] == k and frame.stats.idf.get(t, 0) >= _INTRO_IDF
        ]
        reuse = sum(1 for t in intro if last_seen[t] > k) / len(intro) if intro else 0.0
        importance = _importance(
            frame, seg, by_segment.get(k, []), in_degree.get(k, 0.0), reuse, unique, topic_range[k]
        )
        standalone = _standalone(frame, seg, by_segment.get(k, []), relations, seg_index, items)
        hook = _hook(frame, seg, importance, standalone, roles[k])
        highlight = _highlight(frame, seg, by_segment.get(k, []), importance, standalone)
        removal = _removal(
            frame,
            seg,
            importance,
            density,
            unique,
            in_degree.get(k, 0.0),
            roles[k],
            by_segment.get(k, []),
            relations,
            topic_range[k],
        )
        all_scores.append([importance, density, unique, standalone, highlight, hook, removal])
    return all_scores


def _topic_ranges(structure: Structure) -> list[range | None]:
    from media_house.modules.content_intelligence.domain.values import SegmentLevel

    by_id = {t.topic_id: t for t in structure.topics if t.level is SegmentLevel.TOPIC}
    out: list[range | None] = []
    for k in range(len(structure.segments)):
        topic = next((by_id[t] for t in structure.topic_ids[k] if t in by_id), None)
        if topic is None:
            out.append(None)
            continue
        members = [seg_k for seg_k, ids in enumerate(structure.topic_ids) if topic.topic_id in ids]
        out.append(
            range(structure.segments[members[0]].start, structure.segments[members[-1]].stop)
        )
    return out


def _in_degree(
    relationships: tuple[Relationship, ...], seg_index: Mapping[str, int]
) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in relationships:
        if r.kind in _DEPENDENT and r.target.kind is RefKind.SEGMENT and r.target.id in seg_index:
            k = seg_index[r.target.id]
            out[k] = out.get(k, 0.0) + (r.confidence or 0.5)
    return out


def _importance(
    frame: Frame,
    seg: _Seg,
    items: list[KeyItem],
    dependents: float,
    reuse: float,
    unique: Score,
    topic_range: range | None,
) -> Score:
    recording, topic = _centrality(frame, seg, topic_range)
    centrality = 0.6 * recording + 0.4 * topic
    key = min(1.0, sum(KIND_WEIGHT.get(i.kind, 0.0) * (i.confidence or 0.5) for i in items) / 1.5)
    downstream = 0.6 * min(1.0, dependents / 3.0) + 0.4 * reuse
    marker = (
        1.0 if any(frame.lexicon.weight("emphasis", frame.units[i].text) for i in seg.rng) else 0.0
    )
    parts: dict[str, tuple[float, float]] = {
        "centrality": (0.30, centrality),
        "key_content": (0.25, key),
        "others_depend_on_it": (0.25, downstream),
        "marked_by_speaker": (0.10, marker),
    }
    if unique.value is not None:
        parts["uniqueness"] = (0.10, unique.value)
    total_weight = sum(w for w, _ in parts.values())
    value = sum(w * v for w, v in parts.values()) / total_weight
    ordered = sorted(parts.items(), key=lambda kv: -kv[1][0] * kv[1][1])
    evidence = tuple(
        _ev(f"component:{name}", seg.unit_ids, v, f"{name} {v:.2f} (weight {w:.2f})")
        for name, (w, v) in ordered[:3]
    )
    return Score(
        name=ScoreName.IMPORTANCE,
        value=round(value, 4),
        method=Method.HEURISTIC,
        evidence=evidence,
        components={name: round(v, 4) for name, (_, v) in parts.items()},
    )


def _standalone(
    frame: Frame,
    seg: _Seg,
    items: list[KeyItem],
    relations: Relations,
    seg_index: Mapping[str, int],
    all_items: list[KeyItem],
) -> Score:
    lex = frame.lexicon
    first, last = frame.units[seg.rng.start], frame.units[seg.rng.stop - 1]
    penalties: list[tuple[float, Evidence]] = []
    needs = list(relations.context.get(seg.k, []))

    def penalise(amount: float, kind: str, detail: str, units: tuple[str, ...]) -> None:
        penalties.append((amount, _ev(kind, units, amount, detail)))

    if not first.starts_clean:
        penalise(
            0.25,
            "begins_mid_sentence",
            "the passage starts in the middle of a sentence",
            (first.unit_id,),
        )
    if not last.ends_clean:
        penalise(
            0.20,
            "ends_mid_sentence",
            "the passage stops in the middle of a sentence",
            (last.unit_id,),
        )
    lead = tokens(first.text)[:1]
    if seg.k > 0 and lead and lead[0] in lex.pronouns:
        penalise(
            0.25,
            "opens_with_reference",
            "it opens with a pronoun that points back",
            (first.unit_id,),
        )
    elif seg.k > 0 and lex.anaphora and lex.anaphora.search(first.text):
        penalise(
            0.10,
            "opens_with_connector",
            "it opens with a connective that continues earlier talk",
            (first.unit_id,),
        )
    if seg.words < 6:
        penalise(0.30, "too_short", "too few words to hold a complete idea", seg.unit_ids)
    own = {i.item_id for i in items}
    for rel in relations.relationships:
        if rel.kind is RelationKind.ANSWERS and rel.source.id in own:
            question = next((q for q in all_items if q.item_id == rel.target.id), None)
            if question and question.segment_id and seg_index.get(question.segment_id) != seg.k:
                penalise(
                    0.25,
                    "answer_without_question",
                    "it answers a question asked elsewhere",
                    question.unit_ids,
                )
                needs.extend(question.unit_ids)
    deps = [
        r
        for r in relations.relationships
        if r.kind is RelationKind.DEPENDS_ON and r.source.id == f"s{seg.k + 1:05d}"
    ]
    for rel in deps[:2]:
        penalise(
            0.15,
            "depends_on_definition",
            rel.evidence[0].detail if rel.evidence else "uses a term defined elsewhere",
            rel.evidence[0].unit_ids[1:] if rel.evidence else (),
        )
        if rel.evidence:
            needs.extend(rel.evidence[0].unit_ids[1:])
    if lex.weight("caveat", first.text) >= 0.8 and seg.k > 0:
        penalise(
            0.20,
            "qualifies_earlier_passage",
            "it qualifies what was said just before",
            (first.unit_id,),
        )
    total = sum(p for p, _ in penalties)
    return Score(
        name=ScoreName.STANDALONE,
        value=round(max(0.0, 1.0 - total), 4),
        method=Method.HEURISTIC,
        evidence=tuple(e for _, e in penalties)
        or (_ev("no_dependency_found", seg.unit_ids, None, "no missing context was detected"),),
        requires_context=tuple(dict.fromkeys(needs)),
    )


def _hook(frame: Frame, seg: _Seg, importance: Score, standalone: Score, roles: set[Role]) -> Score:
    lex = frame.lexicon
    first = frame.units[seg.rng.start]
    cues: list[tuple[float, str]] = []
    if is_question(first, frame) and seg.rng.stop - seg.rng.start >= 2:
        cues.append((0.6, "opens with a question the passage goes on to answer"))
    if is_statistic(first.text):
        cues.append((0.6, "opens with a concrete number"))
    if lex.weight("surprise", first.text):
        cues.append((0.7, "opens with a surprising or myth-busting statement"))
    if lex.weight("contrast", first.text) or lex.weight("counter", first.text):
        cues.append((0.5, "opens with a contrast"))
    if lex.weight("personal", first.text) or lex.weight("anecdote", first.text):
        cues.append((0.5, "opens with a personal account"))
    if lex.weight("emphasis", first.text):
        cues.append((0.5, "opens with an explicitly important statement"))
    hookiness = max((w for w, _ in cues), default=0.0)
    brevity = 1.0 - scale(seg.words, 60, 200)
    value = (
        0.35 * hookiness
        + 0.30 * (standalone.value or 0.0)
        + 0.20 * (importance.value or 0.0)
        + 0.15 * brevity
    )
    counter: list[Evidence] = []
    if roles & {Role.RECAP, Role.TRANSITION} or lex.weight("filler", first.text):
        value *= 0.3
        counter.append(
            _ev(
                "structural_passage",
                seg.unit_ids,
                None,
                "a recap, transition or filler rarely opens well",
            )
        )
    if standalone.requires_context:
        counter.append(
            _ev(
                "needs_context", standalone.requires_context, None, "it relies on earlier sentences"
            )
        )
    return Score(
        name=ScoreName.HOOK,
        value=round(min(1.0, value), 4),
        method=Method.HEURISTIC,
        evidence=tuple(_ev("hook_cue", (first.unit_id,), w, why) for w, why in cues),
        counter_evidence=tuple(counter),
        components={"hookiness": hookiness, "brevity": round(brevity, 4)},
        requires_context=standalone.requires_context,
    )


def _fit(frame: Frame, seg: _Seg) -> float:
    start, end = frame.units[seg.rng.start].span.start, frame.units[seg.rng.stop - 1].span.end
    if start is not None and end is not None:
        seconds = end - start
        if seconds <= 12:
            return scale(seconds, 5, 12)
        if seconds <= 45:
            return 1.0
        return 1.0 - 0.7 * scale(seconds, 45, 120)
    return scale(seg.words, 8, 30) if seg.words < 30 else 1.0 - 0.7 * scale(seg.words, 120, 320)


def _highlight(
    frame: Frame, seg: _Seg, items: list[KeyItem], importance: Score, standalone: Score
) -> Score:
    notable = {
        KeyKind.QUOTABLE,
        KeyKind.CLAIM,
        KeyKind.CONCLUSION,
        KeyKind.DEFINITION,
        KeyKind.FACT,
        KeyKind.DECISION,
    }
    quotable = max((i.confidence or 0.5 for i in items if i.kind in notable), default=0.0)
    fit = _fit(frame, seg)
    text_value = (
        0.40 * (importance.value or 0.0)
        + 0.25 * (standalone.value or 0.0)
        + 0.20 * quotable
        + 0.15 * fit
    )
    bonus = 0.0
    evidence = [
        _ev(
            "component:importance",
            seg.unit_ids,
            importance.value,
            "importance carries 40 % of the value",
        )
    ]
    audio = frame.audio
    start, end = frame.units[seg.rng.start].span.start, frame.units[seg.rng.stop - 1].span.end
    if audio is not None and start is not None and end is not None and text_value >= 0.4:
        emphasis = audio.emphasis_between(start, end)
        if emphasis is not None:
            bonus += 0.06 * emphasis
            evidence.append(
                Evidence(
                    kind="audio:emphasis",
                    method=Method.OBSERVED,
                    unit_ids=seg.unit_ids,
                    weight=emphasis,
                    detail="corroboration only; capped at +0.06",
                )
            )
        if audio.laughter_between(start, end):
            bonus += 0.02
            evidence.append(
                Evidence(
                    kind="audio:laughter",
                    method=Method.OBSERVED,
                    unit_ids=seg.unit_ids,
                    weight=0.5,
                    detail="corroboration only; capped at +0.02",
                )
            )
    counter = (
        (
            _ev(
                "needs_context",
                standalone.requires_context,
                None,
                "check the context before extracting",
            ),
        )
        if standalone.requires_context or (standalone.value or 0) < 0.6
        else ()
    )
    return Score(
        name=ScoreName.HIGHLIGHT,
        value=round(min(1.0, text_value + bonus), 4),
        method=Method.HEURISTIC,
        evidence=tuple(evidence),
        counter_evidence=counter,
        components={
            "quotable": round(quotable, 4),
            "length_fit": round(fit, 4),
            "delivery_bonus": round(bonus, 4),
        },
        requires_context=standalone.requires_context,
    )


def _removal(
    frame: Frame,
    seg: _Seg,
    importance: Score,
    density: Score,
    unique: Score,
    dependents: float,
    roles: set[Role],
    items: list[KeyItem],
    relations: Relations,
    topic_range: range | None,
) -> Score:
    if importance.value is None or unique.value is None:
        return Score(
            name=ScoreName.REMOVAL_CANDIDATE,
            value=None,
            method=Method.HEURISTIC,
            reason="importance or uniqueness is unavailable",
        )
    redundancy = 1.0 - unique.value
    linked = [a for (later, _), a in relations.adds.items() if later == seg.k]
    if linked and any(a is not Adds.NOTHING_NEW for a in linked):
        redundancy *= 0.5
    recording, topic = _centrality(frame, seg, topic_range)
    tangent = 1.0 - max(recording, topic)
    parts = {
        "redundant": (0.45, redundancy),
        "low_importance": (0.25, 1.0 - importance.value),
        "tangent": (0.15, tangent),
    }
    if density.value is not None:
        parts["low_density"] = (0.15, 1.0 - density.value)
    base = sum(w * v for w, v in parts.values()) / sum(w for w, _ in parts.values())
    reasons = [
        _ev(f"component:{name}", seg.unit_ids, v, f"{name.replace('_', ' ')} {v:.2f}")
        for name, (_, v) in parts.items()
        if v >= 0.5
    ]
    protection: list[tuple[float, Evidence]] = []
    if dependents > 0:
        protection.append(
            (
                min(0.5, 0.25 * dependents),
                _ev(
                    "others_depend_on_it",
                    seg.unit_ids,
                    None,
                    f"{dependents:.1f} dependency weight from other passages",
                ),
            )
        )
    for role in sorted(roles & _PROTECTED, key=lambda r: r.value):
        protection.append(
            (
                0.25,
                _ev(
                    f"role:{role.value}",
                    seg.unit_ids,
                    None,
                    f"serves as {role.value.replace('_', ' ')}",
                ),
            )
        )
    for item in items:
        if item.kind in {KeyKind.DEFINITION, KeyKind.CAVEAT, KeyKind.ANSWER, KeyKind.DECISION}:
            protection.append(
                (
                    0.2,
                    _ev(
                        f"holds:{item.kind.value}",
                        item.unit_ids,
                        None,
                        f"holds a {item.kind.value}",
                    ),
                )
            )
    if any(a is not Adds.NOTHING_NEW for a in linked):
        protection.append(
            (
                0.2,
                _ev(
                    "adds_something",
                    seg.unit_ids,
                    None,
                    "its repeated content adds evidence, an example or a qualification",
                ),
            )
        )
    keep = min(0.9, sum(p for p, _ in protection))
    return Score(
        name=ScoreName.REMOVAL_CANDIDATE,
        value=round(base * (1.0 - keep), 4),
        method=Method.HEURISTIC,
        evidence=tuple(reasons),
        counter_evidence=tuple(e for _, e in protection),
        components={"unprotected": round(base, 4), "protection": round(keep, 4)},
    )
