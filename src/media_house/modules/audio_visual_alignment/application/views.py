"""Upstream results -> the facts alignment reads. The only place that knows upstream shapes.

Everything is read through the owners' public contracts. Nothing here probes, decodes or
recomputes: a value an owner did not provide stays absent.
"""

from media_house.modules.audio_intelligence.application.contracts import (
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.domain.inputs import (
    AudioEventFact,
    AudioFacts,
    ClockFacts,
    ContentFacts,
    ContentOpportunityFact,
    MouthSample,
    Passage,
    ShotFacts,
    SpeechActivity,
    SpeechSegment,
    VideoEventFact,
    VideoFacts,
)
from media_house.modules.content_intelligence.application.contracts import ContentAnalysis
from media_house.modules.media_inspection.application.contracts import MediaInspection
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerId,
    AnalyzerState,
    VideoAnalysis,
)

_MOUTH_CURVE = "mouth_activity"
_PERSON = "person"


def audio_facts(
    timeline: AudioIntelligenceTimeline, asset_id: str, document_id: str | None
) -> AudioFacts:
    track = timeline.track
    events = sorted(timeline.events, key=lambda e: (e.start, e.end, e.kind))
    return AudioFacts(
        asset_id=asset_id,
        document_id=document_id,
        duration=timeline.duration,
        language=timeline.language or None,
        segments=tuple(
            SpeechSegment(
                segment_id=str(s.id),
                start=s.start,
                end=s.end,
                text=s.text,
                speaker=s.segment.speaker,
            )
            for s in timeline.segments
        ),
        activity=SpeechActivity(track.origin, track.hop, tuple(track.speech))
        if len(track)
        else None,
        events=tuple(
            AudioEventFact(
                event_id=f"ae{i:05d}_{e.kind}",
                kind=e.kind,
                start=e.start,
                end=e.end,
                strength=e.strength,
            )
            for i, e in enumerate(events)
        ),
    )


def video_facts(analysis: VideoAnalysis, document_id: str | None) -> VideoFacts:
    identity_of = {t.track_id: t.identity_cluster for t in analysis.tracks}
    faces_ran = any(
        r.analyzer is AnalyzerId.FACES and r.state is AnalyzerState.OK for r in analysis.analyzers
    )
    shots = tuple(
        ShotFacts(
            shot_id=s.shot_id,
            range=s.range,
            faces_measured=faces_ran and s.faces.state is AnalyzerState.OK,
            face_frames=s.faces.face_frames if s.faces.state is AnalyzerState.OK else 0,
            people=s.entities.counts.get(_PERSON) if s.entities.state is AnalyzerState.OK else None,
            main_track_id=s.entities.main_subject_track_id,
            identity_id=identity_of.get(s.entities.main_subject_track_id or ""),
            screen_text=tuple(i.text for i in s.text.items)
            if s.text.state is AnalyzerState.OK
            else (),
            objects=tuple(s.meaning.objects) if s.meaning.state is AnalyzerState.OK else (),
            description=s.meaning.description if s.meaning.state is AnalyzerState.OK else None,
            scene_id=s.scene_id,
        )
        for s in analysis.shots
    )
    curve = next((c for c in analysis.curves if c.name == _MOUTH_CURVE), None)
    mouth = (
        tuple(
            MouthSample(seconds=pts * curve.timebase.value, jaw=value)
            for pts, value in zip(curve.pts, curve.values, strict=True)
        )
        if curve
        else ()
    )
    return VideoFacts(
        asset_id=analysis.asset_id,
        document_id=document_id,
        duration=analysis.source.duration_seconds,
        first_frame_seconds=analysis.shots[0].range.start.seconds if analysis.shots else None,
        shots=shots,
        mouth=mouth,
        events=tuple(
            VideoEventFact(
                event_id=e.event_id,
                kind=e.kind.value,
                shot_id=e.shot_id,
                time=e.time,
                end=e.end,
                strength=e.strength,
            )
            for e in analysis.events
        ),
    )


def content_facts(analysis: ContentAnalysis, document_id: str | None) -> ContentFacts:
    passages: list[Passage] = []
    opportunities: list[ContentOpportunityFact] = []
    for segment in analysis.segments:
        span = segment.span
        if span.start is None or span.end is None:
            continue
        text = " ".join(u.text for uid in segment.unit_ids if (u := analysis.unit(uid)) is not None)
        passages.append(Passage(segment.segment_id, span.start, span.end, text))
        for item in (*segment.broll, *segment.graphics):
            if item.span.start is None or item.span.end is None:
                continue
            opportunities.append(
                ContentOpportunityFact(
                    opportunity_id=item.opportunity_id,
                    kind=item.kind.value,
                    concept=item.concept,
                    passage_id=segment.segment_id,
                    start=item.span.start,
                    end=item.span.end,
                    strength=item.strength,
                    already_covered=item.already_covered,
                    visual_evidence=tuple(item.visual_evidence),
                )
            )
    return ContentFacts(document_id, tuple(passages), tuple(opportunities))


def clock_facts(inspection: MediaInspection) -> ClockFacts:
    video = inspection.video_streams[0] if inspection.video_streams else None
    audio = inspection.audio_streams[0] if inspection.audio_streams else None
    first_pts = None
    if video is not None:
        first_pts = video.start_time
        if first_pts is None and video.timing is not None:
            first_pts = video.timing.first_pts
    return ClockFacts(
        container_start=inspection.container.start_time,
        video_first_pts=first_pts,
        audio_start=audio.start_time if audio is not None else None,
    )
