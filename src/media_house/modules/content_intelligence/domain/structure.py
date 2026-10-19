"""Segments, topics and sections: the shape of the recording, with readable labels."""

from dataclasses import dataclass, replace

from media_house.modules.content_intelligence.domain.config import Thresholds
from media_house.modules.content_intelligence.domain.ids import segment_id, topic_id
from media_house.modules.content_intelligence.domain.lexical import TextStats
from media_house.modules.content_intelligence.domain.model import Boundary, Topic, excerpt
from media_house.modules.content_intelligence.domain.segmentation import (
    Candidate,
    Segmentation,
    ranges,
)
from media_house.modules.content_intelligence.domain.similarity import (
    EMBEDDING,
    SimilarityProvider,
)
from media_house.modules.content_intelligence.domain.span import Span
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.values import Method, SegmentLevel

_LABEL_TERMS = 3
_LABEL_CHARS = 60
_RELATED = 3
#: Above this many topics, lexical relatedness is only computed between topics that share a term.
_FULL_COMPARISON_LIMIT = 40


@dataclass(frozen=True, slots=True)
class Structure:
    segments: list[range]
    #: The boundary in front of each segment (``None`` for the first one).
    boundaries: list[Boundary | None]
    topics: list[Topic]
    topic_ids: list[tuple[str, ...]]
    segment_of_unit: tuple[int, ...]


def _span(units: tuple[Unit, ...], rng: range) -> Span:
    return Span.cover(units[rng.start].span, units[rng.stop - 1].span)


def _label(
    stats: TextStats,
    similarity: SimilarityProvider,
    units: tuple[Unit, ...],
    rng: range,
) -> tuple[str, tuple[str, ...]]:
    """Terms the passage keeps coming back to; else its most representative sentence."""
    terms = tuple(stats.key_terms(list(rng), _LABEL_TERMS, min_count=2))
    if terms:
        return ", ".join(terms), terms
    group = list(rng)
    best = max(group, key=lambda i: (similarity.group_similarity([i], group), -i))
    return excerpt(units[best].text, _LABEL_CHARS), ()


def _group(
    candidates: tuple[Candidate, ...], units: tuple[Unit, ...], leaf: list[range]
) -> list[tuple[range, list[int], Boundary | None]]:
    """Unit ranges cut by ``candidates``, their leaf segments and the leading boundary."""
    groups = ranges(len(units), candidates)
    by_gap = {c.gap: c for c in candidates}
    out: list[tuple[range, list[int], Boundary | None]] = []
    for group in groups:
        inside = [k for k, seg in enumerate(leaf) if group.start <= seg.start < group.stop]
        cand = by_gap.get(group.start)
        out.append((group, inside, cand.to_boundary(units) if cand else None))
    return out


def _confidence(boundary: Boundary | None, following: Boundary | None) -> float | None:
    values = [b.strength for b in (boundary, following) if b is not None]
    return min(values) if values else None


def build_structure(
    units: tuple[Unit, ...],
    stats: TextStats,
    similarity: SimilarityProvider,
    segmentation: Segmentation,
    thresholds: Thresholds,
) -> Structure:
    leaf = ranges(len(units), segmentation.leaf)
    leaf_by_gap = {c.gap: c for c in segmentation.leaf}
    boundaries = [
        leaf_by_gap[seg.start].to_boundary(units) if seg.start in leaf_by_gap else None
        for seg in leaf
    ]
    segment_of = [0] * len(units)
    for k, seg in enumerate(leaf):
        for i in seg:
            segment_of[i] = k

    topics: list[Topic] = []
    section_groups = _group(segmentation.section, units, leaf) if segmentation.section else []
    topic_groups = _group(segmentation.topic, units, leaf)
    seg_topics: list[list[str]] = [[] for _ in leaf]
    counter = 0

    sections: list[Topic] = []
    for rng, inside, boundary in section_groups:
        label, terms = _label(stats, similarity, units, rng)
        following = next((b for r, _, b in section_groups if r.start == rng.stop), None)
        topic = Topic(
            topic_id=topic_id(counter),
            level=SegmentLevel.SECTION,
            label=label,
            label_terms=terms,
            segment_ids=tuple(segment_id(k) for k in inside),
            span=_span(units, rng),
            method=Method.HEURISTIC,
            boundary=boundary,
            confidence=_confidence(boundary, following),
        )
        counter += 1
        sections.append(topic)
        for k in inside:
            seg_topics[k].append(topic.topic_id)

    for rng, inside, boundary in topic_groups:
        label, terms = _label(stats, similarity, units, rng)
        following = next((b for r, _, b in topic_groups if r.start == rng.stop), None)
        parent = next(
            (
                s.topic_id
                for s, (r, _, _) in zip(sections, section_groups, strict=True)
                if r.start <= rng.start < r.stop
            ),
            None,
        )
        topic = Topic(
            topic_id=topic_id(counter),
            level=SegmentLevel.TOPIC,
            label=label,
            label_terms=terms,
            segment_ids=tuple(segment_id(k) for k in inside),
            span=_span(units, rng),
            method=Method.HEURISTIC,
            parent_id=parent,
            boundary=boundary,
            confidence=_confidence(boundary, following),
        )
        counter += 1
        topics.append(topic)
        for k in inside:
            seg_topics[k].append(topic.topic_id)

    topics = _with_related(topics, topic_groups, similarity, thresholds)
    ordered = [*sections, *topics]
    return Structure(
        leaf,
        boundaries,
        ordered,
        [tuple(sorted(t)) for t in seg_topics],
        tuple(segment_of),
    )


def _with_related(
    topics: list[Topic],
    groups: list[tuple[range, list[int], Boundary | None]],
    similarity: SimilarityProvider,
    thresholds: Thresholds,
) -> list[Topic]:
    if len(topics) < 3:
        return topics
    floor = 0.6 * (
        thresholds.overlap_embedding if similarity.kind == EMBEDDING else thresholds.overlap_lexical
    )
    out: list[Topic] = []
    terms = [set(t.label_terms) for t in topics]
    bounded = len(topics) > _FULL_COMPARISON_LIMIT and similarity.kind != EMBEDDING
    for a, (ra, _, _) in enumerate(groups):
        scored = [
            (similarity.group_similarity(list(ra), list(rb)), topics[b].topic_id)
            for b, (rb, _, _) in enumerate(groups)
            if abs(a - b) > 1 and (not bounded or terms[a] & terms[b])
        ]
        near = [t for s, t in sorted(scored, key=lambda x: (-x[0], x[1])) if s >= floor][:_RELATED]
        out.append(replace(topics[a], related_topic_ids=tuple(near)))
    return out
