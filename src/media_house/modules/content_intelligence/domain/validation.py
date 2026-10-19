"""The invariants every analysis must satisfy before it is stored or returned.

These are the grounding checks that do not depend on how a value was produced: references point
at things that exist, quotations occur in the source, ranges are sane. A failure here is a bug in
this module (or a damaged stored document), never a user error, and the analysis is not stored.
"""

from collections.abc import Iterable

from media_house.modules.content_intelligence.domain.errors import InvalidAnalysis
from media_house.modules.content_intelligence.domain.model import (
    ContentAnalysis,
    Evidence,
    Ref,
)
from media_house.modules.content_intelligence.domain.span import Span
from media_house.modules.content_intelligence.domain.values import (
    OpportunityKind,
    Origin,
    RefKind,
)


def normalise(text: str) -> str:
    """Whitespace-collapsed, case-folded text, for comparing quotations with their source."""
    return " ".join(text.casefold().split())


def _unique(ids: Iterable[str], what: str) -> set[str]:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise InvalidAnalysis(f"duplicate {what} id {i}")
        seen.add(i)
    return seen


def _check_span(span: Span, label: str) -> None:
    if span.start is not None and span.end is not None and span.end < span.start:
        raise InvalidAnalysis(f"{label}: span ends before it starts")


def validate_analysis(analysis: ContentAnalysis) -> None:
    unit_ids = _unique((u.unit_id for u in analysis.units), "unit")
    texts = {u.unit_id: u.text for u in analysis.units}
    segment_ids = _unique((s.segment_id for s in analysis.segments), "segment")
    topic_ids = _unique((t.topic_id for t in analysis.topics), "topic")
    item_ids = _unique((k.item_id for k in analysis.key_items), "key item")
    rel_ids = _unique((r.rel_id for r in analysis.relationships), "relationship")
    _unique(
        (o.opportunity_id for s in analysis.segments for o in (*s.broll, *s.graphics)),
        "opportunity",
    )

    def need(ids: Iterable[str], known: set[str], where: str) -> None:
        for i in ids:
            if i not in known:
                raise InvalidAnalysis(f"{where} refers to unknown id {i}")

    def evidence(items: Iterable[Evidence], where: str) -> None:
        for e in items:
            need(e.unit_ids, unit_ids, f"{where} evidence")

    for unit in analysis.units:
        _check_span(unit.span, unit.unit_id)

    covered: list[str] = []
    for segment in analysis.segments:
        where = f"segment {segment.segment_id}"
        need(segment.unit_ids, unit_ids, where)
        need(segment.topic_ids, topic_ids, where)
        need(segment.key_item_ids, item_ids, where)
        need(segment.dependency_ids, rel_ids, where)
        need(segment.redundancy_ids, rel_ids, where)
        _check_span(segment.span, where)
        covered.extend(segment.unit_ids)
        for score in segment.scores:
            evidence((*score.evidence, *score.counter_evidence), f"{where} {score.name.value}")
            need(score.requires_context, unit_ids, f"{where} {score.name.value} context")
        for role in segment.roles:
            evidence(role.evidence, where)
        for marker in segment.rhetoric:
            need(marker.unit_ids, unit_ids, where)
        for opp in (*segment.broll, *segment.graphics):
            need(opp.unit_ids, unit_ids, f"opportunity {opp.opportunity_id}")
            if opp.segment_id != segment.segment_id:
                raise InvalidAnalysis(f"opportunity {opp.opportunity_id} is in the wrong segment")
            if opp.kind is OpportunityKind.GRAPHIC:
                parts = opp.concept.split(", ") if opp.category == "key_number" else [opp.concept]
                source = normalise(" ".join(texts[u] for u in opp.unit_ids))
                if not all(normalise(p).rstrip("…").strip() in source for p in parts):
                    raise InvalidAnalysis(
                        f"graphic {opp.opportunity_id} text is not in the transcript"
                    )
        if segment.summary:
            _check_summary(
                segment.summary.unit_ids,
                segment.summary.origin,
                segment.summary.text,
                texts,
                unit_ids,
                where,
            )
    if covered != [u.unit_id for u in analysis.units]:
        raise InvalidAnalysis("segments must cover every sentence exactly once, in order")

    for topic in analysis.topics:
        where = f"topic {topic.topic_id}"
        need(topic.segment_ids, segment_ids, where)
        need(topic.key_item_ids, item_ids, where)
        need(topic.related_topic_ids, topic_ids, where)
        if topic.parent_id is not None:
            need([topic.parent_id], topic_ids, where)
        _check_span(topic.span, where)
        if topic.summary:
            _check_summary(
                topic.summary.unit_ids,
                topic.summary.origin,
                topic.summary.text,
                texts,
                unit_ids,
                where,
            )

    for item in analysis.key_items:
        where = f"key item {item.item_id}"
        need(item.unit_ids, unit_ids, where)
        need(item.qualified_by, unit_ids, where)
        if item.segment_id is not None:
            need([item.segment_id], segment_ids, where)
        evidence(item.evidence, where)
        if item.origin is Origin.DIRECT and normalise(item.text) not in normalise(
            " ".join(texts[u] for u in item.unit_ids)
        ):
            raise InvalidAnalysis(f"{where}: a direct item must be verbatim from its sentences")

    refs = {
        RefKind.UNIT: unit_ids,
        RefKind.SEGMENT: segment_ids,
        RefKind.TOPIC: topic_ids,
        RefKind.ITEM: item_ids,
    }

    def ref(r: Ref, where: str) -> None:
        if r.id not in refs[r.kind]:
            raise InvalidAnalysis(f"{where} refers to unknown {r.kind.value} {r.id}")

    for rel in analysis.relationships:
        ref(rel.source, f"relationship {rel.rel_id}")
        ref(rel.target, f"relationship {rel.rel_id}")
        evidence(rel.evidence, f"relationship {rel.rel_id}")
        if rel.source == rel.target:
            raise InvalidAnalysis(f"relationship {rel.rel_id} links something to itself")

    for summary in (analysis.summary, *analysis.key_takeaways):
        if summary is not None:
            _check_summary(
                summary.unit_ids, summary.origin, summary.text, texts, unit_ids, "summary"
            )


def _check_summary(
    summary_units: tuple[str, ...],
    origin: Origin,
    text: str,
    texts: dict[str, str],
    known: set[str],
    where: str,
) -> None:
    for u in summary_units:
        if u not in known:
            raise InvalidAnalysis(f"{where}: summary refers to unknown sentence {u}")
    if origin is Origin.DIRECT and normalise(text) != normalise(
        " ".join(texts[u] for u in summary_units)
    ):
        raise InvalidAnalysis(f"{where}: a direct summary must be made of the sentences it names")
