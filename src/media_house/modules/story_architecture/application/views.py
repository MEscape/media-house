"""The upstream analyses, read through their public contracts, as the planner's own facts.

Nothing is computed or recovered here: each field is copied from what content intelligence and
audio-visual alignment already decided. A relation that names a sentence, item or topic is placed
on the passage that holds it; one that cannot be placed is left out rather than guessed.
"""

import hashlib

from media_house.modules.asset_relationships.application.contracts import RelationshipDto
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentDocument
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    Opportunity,
    ScoreName,
    SemanticSegment,
    Summary,
)
from media_house.modules.story_architecture.domain.facts import (
    AssetFacts,
    AssetLinkFacts,
    ItemFacts,
    RelationFacts,
    SegmentFacts,
    ShotMatchFacts,
    TopicFacts,
    VisualFacts,
)


def analysis_identity(analysis: ContentAnalysis) -> str:
    """What makes two content analyses the same input: their source, settings and versions."""
    parts = (
        analysis.analysis_id,
        analysis.source.transcript_fingerprint,
        analysis.provenance.configuration_fingerprint,
        str(analysis.schema_version),
        str(analysis.provenance.processing_version),
        str(analysis.provenance.vocabulary_version),
        str(len(analysis.segments)),
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def alignment_identity(document: AlignmentDocument) -> str:
    p = document.provenance
    parts = (
        document.alignment_id,
        document.asset_id,
        str(p.alignment_version),
        str(p.scoring_version),
        str(p.schema_version),
        str(len(document.text_matches)),
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def _summary(summary: Summary | None) -> tuple[str, str, tuple[str, ...]] | None:
    if summary is None or not summary.text.strip():
        return None
    return summary.text, summary.origin.value, summary.unit_ids


def _segment(analysis: ContentAnalysis, segment: SemanticSegment) -> SegmentFacts:
    units = [u for uid in segment.unit_ids if (u := analysis.unit(uid)) is not None]
    context: list[str] = []
    for name in (ScoreName.STANDALONE, ScoreName.HOOK):
        score = segment.score(name)
        if score is not None:
            context.extend(c for c in score.requires_context if c not in context)
    summary = segment.summary
    return SegmentFacts(
        segment_id=segment.segment_id,
        unit_ids=segment.unit_ids,
        topic_ids=segment.topic_ids,
        start=segment.span.start,
        end=segment.span.end,
        exact_time=segment.span.exact,
        text=" ".join(u.text for u in units),
        words=sum(u.words for u in units),
        roles=tuple((r.role.value, r.confidence) for r in segment.roles),
        rhetoric=tuple(m.kind.value for m in segment.rhetoric),
        importance=segment.value(ScoreName.IMPORTANCE),
        density=segment.value(ScoreName.INFORMATION_DENSITY),
        standalone=segment.value(ScoreName.STANDALONE),
        hook=segment.value(ScoreName.HOOK),
        removal=segment.value(ScoreName.REMOVAL_CANDIDATE),
        requires_context=tuple(context),
        summary=summary.text if summary else None,
        summary_origin=summary.origin.value if summary else None,
        key_item_ids=segment.key_item_ids,
        warnings=segment.warnings,
    )


def _visual(opportunity: Opportunity) -> VisualFacts:
    return VisualFacts(
        opportunity_id=opportunity.opportunity_id,
        kind=opportunity.kind.value,
        category=opportunity.category,
        concept=opportunity.concept,
        segment_id=opportunity.segment_id,
        strength=opportunity.strength,
        already_covered=opportunity.already_covered,
        rationale=opportunity.rationale,
    )


def _matches(alignment: AlignmentDocument | None, segments: set[str]) -> tuple[ShotMatchFacts, ...]:
    if alignment is None:
        return ()
    return tuple(
        ShotMatchFacts(
            passage_id=m.passage_id,
            shot_id=m.shot.shot_id,
            start=m.shot.range.start.seconds,
            end=m.shot.range.end.seconds,
            grade=m.grade.value,
            rank=m.rank,
        )
        for m in alignment.text_matches
        if m.passage_id in segments
    )


def content_facts(
    analysis: ContentAnalysis,
    asset_id: str,
    name: str,
    alignment: AlignmentDocument | None = None,
) -> AssetFacts:
    """One recording's content analysis (and its alignment, if any) as planner facts."""
    unit_segment = {u: s.segment_id for s in analysis.segments for u in s.unit_ids}
    first_segment = {t.topic_id: t.segment_ids[0] for t in analysis.topics if t.segment_ids}
    item_segment = {k.item_id: k.segment_id for k in analysis.key_items if k.segment_id}

    def place(kind: str, ref_id: str) -> str | None:
        """The passage a reference points at (``kind`` is content intelligence's ``RefKind``)."""
        if kind == "unit":
            return unit_segment.get(ref_id)
        if kind == "segment":
            return ref_id
        if kind == "topic":
            return first_segment.get(ref_id)
        return item_segment.get(ref_id)

    relations: list[RelationFacts] = []
    for rel in analysis.relationships:
        source = place(rel.source.kind.value, rel.source.id)
        target = place(rel.target.kind.value, rel.target.id)
        if source is None or target is None or source == target:
            continue
        relations.append(
            RelationFacts(
                rel_id=rel.rel_id,
                kind=rel.kind.value,
                source_segment=source,
                target_segment=target,
                adds=rel.adds.value if rel.adds else None,
                source_item=rel.source.id if rel.source.kind.value == "item" else None,
                target_item=rel.target.id if rel.target.kind.value == "item" else None,
            )
        )
    segment_ids = {s.segment_id for s in analysis.segments}
    aligned = alignment if alignment is not None and alignment.asset_id == asset_id else None
    return AssetFacts(
        asset_id=asset_id,
        name=name,
        language=analysis.source.language,
        duration=analysis.source.duration,
        state=analysis.state.value,
        analysis_identity=analysis_identity(analysis),
        segments=tuple(_segment(analysis, s) for s in analysis.segments),
        topics=tuple(
            TopicFacts(
                topic_id=t.topic_id,
                level=t.level.value,
                label=t.label,
                segment_ids=t.segment_ids,
                parent_id=t.parent_id,
                summary=t.summary.text if t.summary else None,
                summary_origin=t.summary.origin.value if t.summary else None,
                summary_unit_ids=t.summary.unit_ids if t.summary else (),
            )
            for t in analysis.topics
        ),
        items=tuple(
            ItemFacts(
                item_id=k.item_id,
                kind=k.kind.value,
                text=k.text,
                origin=k.origin.value,
                segment_id=k.segment_id,
                unit_ids=k.unit_ids,
                qualified_by=k.qualified_by,
            )
            for k in analysis.key_items
        ),
        relations=tuple(relations),
        visuals=tuple(_visual(o) for s in analysis.segments for o in (*s.broll, *s.graphics)),
        matches=_matches(aligned, segment_ids),
        summary=_summary(analysis.summary),
        takeaways=tuple(t for k in analysis.key_takeaways if (t := _summary(k)) is not None),
        warnings=analysis.warnings,
        alignment_identity=alignment_identity(aligned) if aligned is not None else None,
    )


def link_facts(dto: RelationshipDto) -> AssetLinkFacts:
    """One relation as the planner reads it. Certainty and evidence are copied, not judged."""
    return AssetLinkFacts(
        relationship_id=dto.relationship_id,
        relation=dto.type.value,
        source_asset=dto.source_asset_id,
        target_asset=dto.target_asset_id,
        status=None if dto.status is None else dto.status.value,
        grade=dto.grade.value,
        contradicted=dto.contradicted,
        outdated=dto.outdated_since is not None,
        evidence=tuple(
            f"{e.kind.value} ({e.provider} {e.provider_version})"
            for e in dto.evidence
            if not e.superseded
        ),
        revision="|".join(
            (
                dto.relationship_id,
                "" if dto.status is None else dto.status.value,
                dto.grade.value,
                dto.updated_at.isoformat(),
                str(dto.contradicted),
            )
        ),
    )
