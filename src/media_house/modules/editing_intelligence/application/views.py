"""The upstream results, read through their public contracts, as the editor's own facts.

Nothing is computed or recovered here: each field is copied from what the owning module already
decided. A value that is absent upstream is ``None`` in the fact, never a default that looks like a
measurement. Each ``*_identity`` is a short digest of what makes two results the same input, so
the plan's cache key notices a changed analysis.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, replace
from functools import partial

from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentDocument,
    Visibility,
)
from media_house.modules.broll_intelligence.application.contracts import (
    BrollAnalysis,
    Candidate,
    Support,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    RelationKind,
    ScoreName,
    SemanticSegment,
    TimeBasis,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    ContinuityAnalysis,
)
from media_house.modules.editing_intelligence.domain.facts import (
    AlignmentFacts,
    AudioFacts,
    BeatFact,
    BrollFacts,
    BrollNote,
    BrollPlace,
    ConstraintFact,
    ContentFacts,
    ContextFact,
    ContinuityFacts,
    ContinuityNote,
    CutVerdict,
    EventFact,
    KeyInfoFact,
    LinkFact,
    OpeningFact,
    OrderChangeFact,
    PacingAdvice,
    PacingFacts,
    PauseFact,
    PausePacing,
    RefFact,
    RelationFact,
    SectionFact,
    SegmentFact,
    ShotChoice,
    ShotFact,
    SourceFacts,
    SpeechFact,
    StoryFacts,
    UnitFact,
    VideoFacts,
    VisualFact,
    WordFact,
)
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.pacing_intelligence.application.contracts import PacingAnalysis
from media_house.modules.story_architecture.application.contracts import SourceRef, StoryPlan
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerState,
    Score,
    VideoAnalysis,
)

_WEAKEST_FIRST = (
    TimeBasis.MISSING,
    TimeBasis.INTERPOLATED,
    TimeBasis.UPSTREAM_ESTIMATE,
    TimeBasis.SOURCE,
    TimeBasis.WORDS,
)
_DEPENDENCY_KINDS = frozenset(
    {
        RelationKind.DEPENDS_ON,
        RelationKind.REFERENCES,
        RelationKind.CONTINUES,
        RelationKind.QUALIFIES,
        RelationKind.ANSWERS,
    }
)
_REPEAT_KINDS = frozenset({RelationKind.REPEATS, RelationKind.OVERLAPS})


def _digest(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:32]


# --- the story plan ------------------------------------------------------------------------
def _ref(ref: SourceRef) -> RefFact:
    return RefFact(
        asset_id=ref.asset_id,
        segment_ids=ref.segment_ids,
        unit_ids=ref.unit_ids,
        start=ref.start,
        end=ref.end,
        exact_time=ref.exact_time,
    )


def story_facts(plan: StoryPlan, stored_as: str | None) -> StoryFacts:
    return StoryFacts(
        story_plan_id=plan.story_plan_id,
        plan_version=plan.plan_version,
        stored_as=stored_as,
        target_format=plan.target_format,
        asset_ids=tuple(a.asset_id for a in plan.source_assets),
        sections=tuple(
            SectionFact(
                section_id=s.section_id,
                order=s.order,
                label=s.label,
                priority=s.priority.value,
                importance=s.importance,
                sources=tuple(_ref(r) for r in s.sources),
                requires=s.requires,
            )
            for s in plan.sections
        ),
        beats=tuple(
            BeatFact(
                beat_id=b.beat_id,
                section_id=b.section_id,
                function=b.function.value,
                source=_ref(b.source),
                qualified_by=b.qualified_by,
            )
            for b in plan.beats
        ),
        key_info=tuple(
            KeyInfoFact(
                info_id=k.info_id,
                section_id=k.section_id,
                kind=k.kind.value,
                text=k.text,
                priority=k.priority.value,
                source=_ref(k.source),
                qualified_by=k.qualified_by,
            )
            for k in plan.key_information
        ),
        constraints=tuple(
            ConstraintFact(
                constraint_id=c.constraint_id,
                kind=c.kind.value,
                first=c.first,
                second=c.second,
                strength=c.strength.value,
                reason=c.reason,
            )
            for c in plan.ordering_constraints
        ),
        contexts=tuple(
            ContextFact(
                section_id=c.section_id,
                requires_section_id=c.requires_section_id,
                reason=c.reason,
                needed=None if c.needed is None else _ref(c.needed),
            )
            for c in plan.context_dependencies
        ),
        links=tuple(
            LinkFact(
                link_id=link.link_id,
                kind=link.kind.value,
                setup=_ref(link.setup),
                payoff=None if link.payoff is None else _ref(link.payoff),
            )
            for link in plan.setup_payoff_links
        ),
        visuals=tuple(
            VisualFact(
                visual_id=v.visual_id,
                section_id=v.section_id,
                purpose=v.purpose.value,
                necessity=v.necessity.value,
                concept=v.concept,
                source=_ref(v.source),
                strength=v.strength,
                already_covered=v.already_covered,
                shots=tuple(
                    ShotChoice(asset_id=s.asset_id, shot_id=s.shot_id, grade=s.grade, rank=s.rank)
                    for s in v.available
                ),
            )
            for v in plan.visual_opportunities
        ),
        openings=tuple(
            OpeningFact(
                candidate_id=o.candidate_id,
                strategy=o.strategy.value,
                source=_ref(o.source),
                excerpt=o.excerpt,
                section_id=o.section_id,
                risks=o.risks,
                needs_context=o.context_required is not None,
            )
            for o in plan.candidate_openings
        ),
        recommended_opening=plan.recommended_opening,
        order_changes=tuple(
            OrderChangeFact(
                section_id=c.section_id,
                from_index=c.from_index,
                to_index=c.to_index,
                reason=c.reason,
                constraint_ids=c.constraint_ids,
            )
            for c in plan.narrative_pattern.changes
        ),
        relations=tuple(
            RelationFact(
                certainty=r.certainty.value,
                treatment=r.treatment.value,
                affected_sections=r.affected_sections,
            )
            for r in plan.asset_relations
        ),
        target_low=plan.estimated_duration.target_low,
        target_high=plan.estimated_duration.target_high,
        warnings=plan.warnings,
    )


# --- the pacing analysis -------------------------------------------------------------------
def pacing_facts(analysis: PacingAnalysis) -> PacingFacts:
    """What the editor reads of the pacing analysis. Its identity covers all of it, so any change
    the editor could notice (not only a new analysis id) misses the plan cache."""
    facts = PacingFacts(
        analysis_id=analysis.pacing_analysis_id,
        profile=analysis.profile,
        strategy=analysis.strategy.value,
        pauses=tuple(
            PausePacing(
                asset_id=p.span.asset_id,
                start=p.span.start.value,
                end=p.span.end.value,
                verdict=p.verdict.value,
                keep=p.recommended_keep,
                reasons=tuple(c.value for c in p.reason_codes),
                confidence=p.confidence,
            )
            for p in analysis.pauses
        ),
        advice=tuple(
            PacingAdvice(
                recommendation_id=r.recommendation_id,
                kind=r.type.value,
                asset_id=r.target.asset_id,
                section_id=r.section_id,
                beat_id=r.beat_id,
                proposal=r.proposal,
                delta_seconds=r.delta_seconds,
                duration=r.recommended_duration,
                confidence=r.confidence,
                priority=r.priority,
            )
            for r in analysis.recommendations
        ),
        identity="",
    )
    return replace(facts, identity=_digest(analysis.pacing_analysis_id, asdict(facts)))


# --- continuity ------------------------------------------------------------------------------
def continuity_facts(analysis: ContinuityAnalysis) -> ContinuityFacts:
    """What the editor reads of the continuity analysis: a verdict per cut it judged and the
    findings worth a look (surfaced, not rejected), each with the best repair it examined. Its
    identity covers all of it, so any change the editor could notice misses the plan cache."""
    cuts = []
    for record in analysis.transitions:
        risk = analysis.risk_at(
            record.outgoing_asset,
            record.outgoing_at.value,
            record.incoming_asset,
            record.incoming_at.value,
        )
        if risk is None:
            continue
        cuts.append(
            CutVerdict(
                out_asset=record.outgoing_asset,
                leaves=record.outgoing_at.value,
                in_asset=record.incoming_asset,
                resumes=record.incoming_at.value,
                risk=risk[0],
                reason=risk[1],
            )
        )
    notes = []
    for issue in analysis.surfaced():
        best = next((r for r in issue.repairs if r.availability.value != "unavailable"), None)
        fix = (
            f"; suggested: {best.description} ({best.owner.value}, {best.availability.value})"
            if best
            else ""
        )
        place = f", at {issue.timeline_at.value:.1f} s" if issue.timeline_at else ""
        notes.append(
            ContinuityNote(
                subject=issue.type.value,
                detail=f"{issue.explanation} [{issue.level.value}{place}]{fix}",
            )
        )
    facts = ContinuityFacts(
        analysis_id=analysis.continuity_analysis_id,
        profile=analysis.profile,
        cuts=tuple(cuts),
        notes=tuple(notes),
        identity="",
    )
    return replace(facts, identity=_digest(analysis.continuity_analysis_id, asdict(facts)))


# --- b-roll ----------------------------------------------------------------------------------
def _choice(c: Candidate) -> ShotChoice:
    return ShotChoice(
        asset_id=c.asset_id,
        shot_id=c.shot_id,
        grade="broll_direct" if c.support is Support.SATISFIES else "broll_illustrative",
        rank=c.rank,
        hint_start=c.source.start.value,
        hint_end=c.source.end.value,
        warnings=c.warnings,
    )


def broll_facts(analysis: BrollAnalysis) -> BrollFacts:
    """What the editor reads of the B-roll analysis: the candidates of each story visual (best
    first, illustrative ones labelled as such) and the satisfied places the story did not name.
    Its identity covers all of it, so any change the editor could notice misses the plan cache."""
    places: list[BrollPlace] = []
    notes: list[BrollNote] = []
    analysed: dict[str, str] = {}
    for rec in analysis.recommendations:
        if rec.story_visual_id is not None:
            places.append(
                BrollPlace(
                    visual_id=rec.story_visual_id,
                    choices=tuple(_choice(c) for c in rec.candidates),
                    start=rec.placement.target_start.value,
                    end=rec.placement.target_end.value,
                    missing=rec.missing_coverage,
                )
            )
            for c in rec.candidates:
                analysed.setdefault(c.asset_id, c.analyzed_asset_id)
        elif rec.best is not None and not rec.missing_coverage:
            best = rec.best
            notes.append(
                BrollNote(
                    asset_id=rec.asset_id,
                    start=rec.placement.target_start.value,
                    end=rec.placement.target_end.value,
                    need=rec.need.value,
                    detail=f"{rec.purpose.value} footage {best.asset_name} "
                    f"({best.role.value.replace('_', ' ')}) is available",
                )
            )
    facts = BrollFacts(
        analysis_id=analysis.broll_analysis_id,
        profile=analysis.profile,
        places=tuple(places),
        notes=tuple(notes),
        analysed=tuple(sorted(analysed.items())),
        identity="",
    )
    return replace(facts, identity=_digest(analysis.broll_analysis_id, asdict(facts)))


# --- content analysis ------------------------------------------------------------------------
def content_identity(analysis: ContentAnalysis) -> str:
    return _digest(
        analysis.analysis_id,
        analysis.source.transcript_fingerprint,
        analysis.provenance.configuration_fingerprint,
        analysis.schema_version,
        analysis.provenance.processing_version,
        len(analysis.segments),
    )


def _basis(units_basis: Sequence[TimeBasis]) -> str:
    return min(units_basis, key=_WEAKEST_FIRST.index).value


def _segment(analysis: ContentAnalysis, seg: SemanticSegment, place: dict[str, str]) -> SegmentFact:
    context: list[str] = []
    for name in (ScoreName.STANDALONE, ScoreName.HOOK):
        score = seg.score(name)
        if score is not None:
            context.extend(c for c in score.requires_context if c not in context)
    depends: list[str] = []
    repeats: str | None = None
    for rel in analysis.relationships:
        source = _place(rel.source.kind.value, rel.source.id, place)
        target = _place(rel.target.kind.value, rel.target.id, place)
        if source != seg.segment_id or target is None or target == seg.segment_id:
            continue
        if rel.kind in _DEPENDENCY_KINDS and target not in depends:
            depends.append(target)
        if rel.kind in _REPEAT_KINDS and rel.adds is not None:
            repeats = rel.adds.value
    return SegmentFact(
        segment_id=seg.segment_id,
        unit_ids=seg.unit_ids,
        start=seg.span.start,
        end=seg.span.end,
        importance=seg.value(ScoreName.IMPORTANCE),
        removal=seg.value(ScoreName.REMOVAL_CANDIDATE),
        highlight=seg.value(ScoreName.HIGHLIGHT),
        hook=seg.value(ScoreName.HOOK),
        requires_context=tuple(context),
        depends_on=tuple(depends),
        repeats=repeats,
    )


def _place(kind: str, ref_id: str, place: dict[str, str]) -> str | None:
    """The content segment a reference points at (units, segments; others are not placed)."""
    if kind == "segment":
        return ref_id
    if kind == "unit":
        return place.get(ref_id)
    return None


def content_facts(analysis: ContentAnalysis, asset_id: str) -> ContentFacts:
    place = {u: s.segment_id for s in analysis.segments for u in s.unit_ids}
    return ContentFacts(
        asset_id=asset_id,
        units=tuple(
            UnitFact(
                unit_id=u.unit_id,
                segment_id=place.get(u.unit_id, ""),
                start=u.span.start,
                end=u.span.end,
                time_basis=_basis((u.span.start_basis, u.span.end_basis)),
                text=u.text,
                words=u.words,
                starts_clean=u.starts_clean,
                ends_clean=u.ends_clean,
            )
            for u in analysis.units
        ),
        segments=tuple(_segment(analysis, s, place) for s in analysis.segments),
        identity=content_identity(analysis),
    )


# --- audio -------------------------------------------------------------------------------------
def audio_identity(timeline: AudioIntelligenceTimeline) -> str:
    m = timeline.metadata
    return _digest(
        m.source_asset_id,
        m.audio_asset_id,
        m.analysis_version,
        m.scoring,
        m.created_at.isoformat(),
        sorted(m.analyzers.items()),
        len(timeline.words),
        len(timeline.pauses),
    )


def audio_facts(timeline: AudioIntelligenceTimeline, asset_id: str) -> AudioFacts:
    return AudioFacts(
        asset_id=asset_id,
        duration=timeline.duration,
        alignment_method=timeline.transcript.metadata.alignment_method,
        words=tuple(
            WordFact(
                index=w.index,
                start=w.start,
                end=w.end,
                text=w.normalized_word.casefold(),
                confidence=w.confidence,
                emphasis=w.emphasis_score,
                expressiveness=w.expressiveness_score,
            )
            for w in timeline.words
        ),
        pauses=tuple(
            PauseFact(
                start=p.start,
                end=p.end,
                silence_ratio=p.silence_ratio,
            )
            for p in timeline.pauses
        ),
        events=tuple(
            EventFact(kind=e.kind, start=e.start, end=e.end, strength=e.strength)
            for e in timeline.events
        ),
        identity=audio_identity(timeline),
    )


# --- video -------------------------------------------------------------------------------------
def video_identity(analysis: VideoAnalysis) -> str:
    p = analysis.provenance
    return _digest(
        analysis.asset_id,
        analysis.asset_checksum,
        p.processing_version,
        p.profile_name,
        p.profile_version,
        sorted(p.analyzer_versions.items()),
        [(s.shot_id, s.range.start.frame, s.range.end.frame) for s in analysis.shots],
    )


def _score(scores: dict[str, Score], name: str) -> float | None:
    found = scores.get(name)
    return found.value if found is not None and found.state is AnalyzerState.OK else None


def video_facts(
    analysis: VideoAnalysis, asset_id: str, correction: float, corrected: bool
) -> VideoFacts:
    shots: list[ShotFact] = []
    for s in analysis.shots:
        score = {sc.name.value: sc for sc in s.scores}
        value = partial(_score, score)
        cont = s.continuity
        usable = cont.state is AnalyzerState.OK
        shots.append(
            ShotFact(
                shot_id=s.shot_id,
                index=s.index,
                start=s.range.start.seconds + correction,
                end=s.range.end.seconds + correction,
                black_in=s.boundary_in.black_adjacent,
                camera=s.camera.movement.value
                if s.camera.ok and s.camera.movement is not None
                else None,
                usability=value("technical_usability"),
                previous_shot_id=cont.previous_shot_id if usable else None,
                jump_cut_likelihood=cont.jump_cut_likelihood if usable else None,
                luma_delta=cont.luma_delta if usable else None,
                color_delta=cont.color_balance_delta if usable else None,
                indicators=tuple(s.indicators.reasons) if s.indicators.ok else (),
                head_seconds=s.handles.head_seconds if s.handles.ok else None,
            )
        )
    return VideoFacts(
        asset_id=asset_id,
        shots=tuple(shots),
        identity=video_identity(analysis),
    )


# --- alignment ---------------------------------------------------------------------------
def alignment_identity(document: AlignmentDocument) -> str:
    p = document.provenance
    return _digest(
        document.alignment_id,
        document.asset_id,
        p.alignment_version,
        p.scoring_version,
        p.schema_version,
        len(document.text_matches),
    )


def alignment_facts(document: AlignmentDocument) -> AlignmentFacts:
    return AlignmentFacts(
        asset_id=document.asset_id,
        speech=tuple(
            SpeechFact(
                start=s.start,
                end=s.end,
                visibility=s.visibility.value,
                shot_ids=tuple(
                    x.shot_id for x in s.shots if x.visibility is Visibility.SPEAKING_FACE
                ),
            )
            for s in document.speech_sources
        ),
        identity=alignment_identity(document),
    )


# --- the recording -------------------------------------------------------------------------
def source_facts(
    asset: MediaAssetDto, inspection: InspectionResult | None, fallback_duration: float | None
) -> SourceFacts:
    """Technical facts from the STORED inspection; nothing is probed."""
    duration = asset.duration_seconds if asset.duration_seconds is not None else fallback_duration
    if inspection is None:
        return SourceFacts(
            asset_id=asset.id,
            name=asset.display_name,
            duration=duration,
            frame_rate=None,
            variable_frame_rate=None,
            has_video=None,
            has_audio=None,
            sample_rate=None,
            usable=None,
            identity=_digest(asset.id, asset.checksum, "uninspected"),
            notes=("no stored inspection: stream facts and frame rate are unknown",),
        )
    media = inspection.inspection
    video = media.primary_video
    audio = media.audio_stream()
    rate = None
    vfr: bool | None = None
    if video is not None:
        rate = video.frame_rate.value or video.average_frame_rate.value
        if video.timing is not None:
            vfr = {"constant": False, "variable": True}.get(video.timing.mode.value)
        duration = media.container.duration or video.duration or duration
    elif audio is not None:
        duration = media.container.duration or audio.duration or duration
    return SourceFacts(
        asset_id=asset.id,
        name=asset.display_name,
        duration=duration,
        frame_rate=rate,
        variable_frame_rate=vfr,
        has_video=video is not None,
        has_audio=audio is not None,
        sample_rate=audio.sample_rate if audio is not None else None,
        usable=media.status.usable,
        identity=_digest(
            asset.id,
            asset.checksum,
            media.inspection_version,
            duration,
            rate,
            vfr,
            video is not None,
            audio is not None,
        ),
    )
