"""Questions asked of a stored analysis. Pure functions: nothing is recomputed."""

from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.model import (
    ContentAnalysis,
    KeyItem,
    Opportunity,
    Relationship,
    SemanticSegment,
    Topic,
)
from media_house.modules.content_intelligence.domain.values import (
    KeyKind,
    OpportunityKind,
    RefKind,
    RelationKind,
    ScoreName,
)


def segments_between(
    analysis: ContentAnalysis, start: float, end: float
) -> tuple[SemanticSegment, ...]:
    """Segments overlapping ``[start, end]`` seconds (segments without times never match)."""
    return tuple(s for s in analysis.segments if s.span.overlaps(start, end))


def segments_of_topic(analysis: ContentAnalysis, topic_id: str) -> tuple[SemanticSegment, ...]:
    topic = analysis.topic(topic_id)
    if topic is None:
        return ()
    wanted = set(topic.segment_ids)
    return tuple(s for s in analysis.segments if s.segment_id in wanted)


def topics_at(analysis: ContentAnalysis, seconds: float) -> tuple[Topic, ...]:
    return tuple(t for t in analysis.topics if t.span.overlaps(seconds, seconds + 1e-9))


def ranked(
    analysis: ContentAnalysis, name: ScoreName, minimum: float = 0.0, limit: int | None = None
) -> tuple[SemanticSegment, ...]:
    """Segments scoring at least ``minimum`` on ``name``, best first (missing scores skipped)."""
    scored = [(s, s.value(name)) for s in analysis.segments]
    keep = sorted(
        ((s, v) for s, v in scored if v is not None and v >= minimum),
        key=lambda sv: (-sv[1], sv[0].segment_id),
    )
    return tuple(s for s, _ in keep[:limit])


def highlights(
    analysis: ContentAnalysis, minimum: float = 0.5, limit: int | None = None
) -> tuple[SemanticSegment, ...]:
    return ranked(analysis, ScoreName.HIGHLIGHT, minimum, limit)


def standalone_candidates(
    analysis: ContentAnalysis, minimum: float = 0.8
) -> tuple[SemanticSegment, ...]:
    return ranked(analysis, ScoreName.STANDALONE, minimum)


def removal_candidates(
    analysis: ContentAnalysis, minimum: float = 0.4
) -> tuple[SemanticSegment, ...]:
    """Advisory only: each result carries the reasons AGAINST removing it as counter-evidence."""
    return ranked(analysis, ScoreName.REMOVAL_CANDIDATE, minimum)


def relationships_of(
    analysis: ContentAnalysis, segment_id: str, kinds: frozenset[RelationKind] | None = None
) -> tuple[Relationship, ...]:
    return tuple(
        r
        for r in analysis.relationships
        if (kinds is None or r.kind in kinds)
        and any(e.kind is RefKind.SEGMENT and e.id == segment_id for e in (r.source, r.target))
    )


def related_segments(
    analysis: ContentAnalysis, segment_id: str
) -> tuple[tuple[SemanticSegment, Relationship], ...]:
    """The other segments linked to ``segment_id`` and the link that connects them."""
    out: list[tuple[SemanticSegment, Relationship]] = []
    for rel in relationships_of(analysis, segment_id):
        other = rel.target if rel.source.id == segment_id else rel.source
        found = analysis.segment(other.id) if other.kind is RefKind.SEGMENT else None
        if found is not None:
            out.append((found, rel))
    return tuple(out)


def redundancy_links(analysis: ContentAnalysis) -> tuple[Relationship, ...]:
    return tuple(
        r for r in analysis.relationships if r.kind in {RelationKind.REPEATS, RelationKind.OVERLAPS}
    )


def opportunities(
    analysis: ContentAnalysis, kind: OpportunityKind | None = None, *, include_covered: bool = False
) -> tuple[Opportunity, ...]:
    found = [
        o
        for s in analysis.segments
        for o in (*s.broll, *s.graphics)
        if (kind is None or o.kind is kind) and (include_covered or not o.already_covered)
    ]
    return tuple(sorted(found, key=lambda o: (-o.strength, o.opportunity_id)))


def key_items(analysis: ContentAnalysis, *kinds: KeyKind) -> tuple[KeyItem, ...]:
    return tuple(k for k in analysis.key_items if not kinds or k.kind in kinds)


@dataclass(frozen=True, slots=True)
class SegmentEvidence:
    """Everything that explains one segment, gathered from the stored analysis."""

    segment: SemanticSegment
    text: str
    items: tuple[KeyItem, ...]
    relationships: tuple[Relationship, ...]
    #: Sentences a reader needs to interpret the segment (from its scores), with their text.
    context: tuple[tuple[str, str], ...]


def evidence_for(analysis: ContentAnalysis, segment_id: str) -> SegmentEvidence | None:
    segment = analysis.segment(segment_id)
    if segment is None:
        return None
    texts = {u.unit_id: u.text for u in analysis.units}
    needed = dict.fromkeys(u for s in segment.scores for u in s.requires_context)
    return SegmentEvidence(
        segment=segment,
        text=" ".join(texts[u] for u in segment.unit_ids),
        items=tuple(k for k in analysis.key_items if k.item_id in segment.key_item_ids),
        relationships=relationships_of(analysis, segment_id),
        context=tuple((u, texts[u]) for u in needed if u in texts),
    )
