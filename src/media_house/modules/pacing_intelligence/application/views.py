"""The upstream results, read through their public contracts, as the analyser's own facts.

Nothing is computed or recovered here except the energy rhythm, which audio intelligence derives
from the track it already holds (``timeline.energy_rhythm()``). A value that is absent upstream is
``None`` in the fact. Each ``*_identity`` is a short digest of what makes two results the same
input, so the analysis cache key notices a changed analysis.
"""

import hashlib
from functools import partial

from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentDocument
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    ScoreName,
    TimeBasis,
)
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.pacing_intelligence.domain.facts import (
    AlignmentFacts,
    AudioFacts,
    BeatFact,
    ContentFacts,
    EventFact,
    PauseFact,
    RefFact,
    RhythmFact,
    SectionFact,
    SegmentFact,
    ShotFact,
    SourceFacts,
    SpeechFact,
    StoryFacts,
    UnitFact,
    VideoFacts,
    VisualFact,
    WordFact,
)
from media_house.modules.story_architecture.application.contracts import SourceRef, StoryPlan
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerState,
    Score,
    VideoAnalysis,
)
from media_house.modules.video_intelligence.application.contracts import (
    ScoreName as VideoScore,
)

_WEAKEST_FIRST = (
    TimeBasis.MISSING,
    TimeBasis.INTERPOLATED,
    TimeBasis.UPSTREAM_ESTIMATE,
    TimeBasis.SOURCE,
    TimeBasis.WORDS,
)


def _digest(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:32]


# --- the story plan -------------------------------------------------------------------------
def _ref(ref: SourceRef) -> RefFact:
    return RefFact(asset_id=ref.asset_id, unit_ids=ref.unit_ids, start=ref.start, end=ref.end)


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
                sources=tuple(_ref(r) for r in s.sources),
                duration_seconds=s.duration.seconds,
            )
            for s in plan.sections
        ),
        beats=tuple(
            BeatFact(
                beat_id=b.beat_id,
                section_id=b.section_id,
                function=b.function.value,
                source=_ref(b.source),
            )
            for b in plan.beats
        ),
        visuals=tuple(
            VisualFact(
                visual_id=v.visual_id,
                section_id=v.section_id,
                purpose=v.purpose.value,
                necessity=v.necessity.value,
                concept=v.concept,
                source=_ref(v.source),
            )
            for v in plan.visual_opportunities
        ),
    )


# --- audio ----------------------------------------------------------------------------------
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
                speaker=w.word.speaker,
                wpm=w.rate.words_per_minute if w.rate is not None else None,
                emphasis=w.emphasis_score,
            )
            for w in timeline.words
        ),
        pauses=tuple(
            PauseFact(
                start=p.start,
                end=p.end,
                silence_ratio=p.silence_ratio,
                before_word=p.before_word,
                after_word=p.after_word,
                at_segment_boundary=p.at_segment_boundary,
            )
            for p in timeline.pauses
        ),
        events=tuple(
            EventFact(kind=e.kind, start=e.start, end=e.end, strength=e.strength)
            for e in timeline.events
        ),
        rhythm=tuple(
            RhythmFact(
                start=r.start,
                end=r.end,
                tempo_bpm=r.tempo_bpm,
                strength=r.strength,
                beats=r.beats,
            )
            for r in timeline.energy_rhythm()
        ),
        identity=audio_identity(timeline),
    )


# --- content analysis -----------------------------------------------------------------------
def content_identity(analysis: ContentAnalysis) -> str:
    return _digest(
        analysis.analysis_id,
        analysis.source.transcript_fingerprint,
        analysis.provenance.configuration_fingerprint,
        analysis.schema_version,
        analysis.provenance.processing_version,
        len(analysis.segments),
    )


def _basis(*bases: TimeBasis) -> str:
    return min(bases, key=_WEAKEST_FIRST.index).value


def content_facts(analysis: ContentAnalysis, asset_id: str) -> ContentFacts:
    place = {u: s.segment_id for s in analysis.segments for u in s.unit_ids}
    depends: dict[str, list[str]] = {}
    repeats: dict[str, str] = {}
    for rel in analysis.relationships:
        source = _segment_of(rel.source.kind.value, rel.source.id, place)
        target = _segment_of(rel.target.kind.value, rel.target.id, place)
        if source is None or target is None or source == target:
            continue
        if rel.kind.value in ("depends_on", "references", "continues", "qualifies", "answers"):
            depends.setdefault(source, []).append(target)
        if rel.kind.value in ("repeats", "overlaps") and rel.adds is not None:
            repeats[source] = rel.adds.value
    return ContentFacts(
        asset_id=asset_id,
        units=tuple(
            UnitFact(
                unit_id=u.unit_id,
                segment_id=place.get(u.unit_id, ""),
                start=u.span.start,
                end=u.span.end,
                time_basis=_basis(u.span.start_basis, u.span.end_basis),
                text=u.text,
                words=u.words,
            )
            for u in analysis.units
        ),
        segments=tuple(
            SegmentFact(
                segment_id=s.segment_id,
                unit_ids=s.unit_ids,
                start=s.span.start,
                end=s.span.end,
                importance=s.value(ScoreName.IMPORTANCE),
                density=s.value(ScoreName.INFORMATION_DENSITY),
                repeats=repeats.get(s.segment_id),
                requires_context=sum(
                    len(score.requires_context)
                    for score in (s.score(ScoreName.STANDALONE), s.score(ScoreName.HOOK))
                    if score is not None
                ),
                depends_on=tuple(dict.fromkeys(depends.get(s.segment_id, ()))),
                key_items=len(s.key_item_ids),
                topic_ids=s.topic_ids,
            )
            for s in analysis.segments
        ),
        identity=content_identity(analysis),
    )


def _segment_of(kind: str, ref_id: str, place: dict[str, str]) -> str | None:
    if kind == "segment":
        return ref_id
    if kind == "unit":
        return place.get(ref_id)
    return None


# --- video ----------------------------------------------------------------------------------
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


def video_facts(analysis: VideoAnalysis, asset_id: str, correction: float) -> VideoFacts:
    shots: list[ShotFact] = []
    for s in analysis.shots:
        scores = {sc.name.value: sc for sc in s.scores}
        value = partial(_score, scores)
        shots.append(
            ShotFact(
                shot_id=s.shot_id,
                index=s.index,
                start=s.range.start.seconds + correction,
                end=s.range.end.seconds + correction,
                start_frame=s.range.start.frame,
                end_frame=s.range.end.frame,
                camera=s.camera.movement.value
                if s.camera.ok and s.camera.movement is not None
                else None,
                static_fraction=s.motion.static_fraction if s.motion.ok else None,
                motion_energy=s.motion.mean_energy if s.motion.ok else None,
                visual_interest=value(VideoScore.VISUAL_INTEREST.value),
            )
        )
    return VideoFacts(
        asset_id=asset_id,
        frame_rate=analysis.source.frame_rate,
        shots=tuple(shots),
        identity=video_identity(analysis),
    )


# --- alignment ------------------------------------------------------------------------------
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
            SpeechFact(start=s.start, end=s.end, visibility=s.visibility.value)
            for s in document.speech_sources
        ),
        identity=alignment_identity(document),
    )


# --- the recording --------------------------------------------------------------------------
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
            identity=_digest(asset.id, asset.checksum, "uninspected", duration),
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
        identity=_digest(asset.id, asset.checksum, media.inspection_version, duration, rate, vfr),
    )
