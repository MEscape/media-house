"""Adapters from other modules' PUBLIC contracts to this module's small input types.

Nothing here probes media or recomputes an upstream result: it only reshapes what the owners
already produced. A field an upstream result does not provide stays absent (never guessed).
"""

import hashlib

from media_house.modules.audio_intelligence.application.contracts import (
    LAUGHTER,
    AudioIntelligenceTimeline,
    Transcript,
    WordTiming,
)
from media_house.modules.content_intelligence.domain.context import (
    AudioCues,
    VisualContext,
    VisualShot,
)
from media_house.modules.content_intelligence.domain.source import (
    SourceSegment,
    SourceTranscript,
    SourceWord,
)
from media_house.modules.content_intelligence.domain.values import SpeechTiming
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis

_EMPHASIS_FLOOR = 0.7

_TIMING = {
    WordTiming.ALIGNED: SpeechTiming.ALIGNED,
    WordTiming.INTERPOLATED: SpeechTiming.INTERPOLATED,
    WordTiming.SEGMENT_ESTIMATE: SpeechTiming.SEGMENT_ESTIMATE,
}


def source_from_transcript(
    transcript: Transcript, *, transcript_asset_id: str | None = None
) -> SourceTranscript:
    meta = transcript.metadata
    segments = tuple(
        SourceSegment(
            id=s.id,
            text=s.text,
            start=s.start,
            end=s.end,
            speaker=s.speaker,
            words=tuple(
                SourceWord(
                    text=w.raw_word,
                    start=w.start,
                    end=w.end,
                    timing=_TIMING[w.timing],
                    confidence=w.confidence,
                )
                for w in s.words
            ),
        )
        for s in transcript.segments
    )
    return SourceTranscript(
        segments=segments,
        language=meta.language,
        duration=meta.duration,
        origin="audio_intelligence",
        asset_id=meta.source_asset_id,
        transcript_asset_id=transcript_asset_id,
    )


def cues_from_timeline(timeline: AudioIntelligenceTimeline, asset_id: str) -> AudioCues:
    emphasis = tuple(
        (w.start, w.end, w.emphasis_score)
        for w in timeline.words
        if w.emphasis_score is not None and w.emphasis_score >= _EMPHASIS_FLOOR
    )
    laughter = tuple((e.start, e.end) for e in timeline.events if e.kind == LAUGHTER)
    digest = hashlib.sha256(repr((emphasis, laughter)).encode()).hexdigest()[:16]
    return AudioCues(asset_id=asset_id, fingerprint=digest, emphasis=emphasis, laughter=laughter)


def context_from_video(analysis: VideoAnalysis) -> VisualContext:
    """Shots with the description, objects and on-screen text the visual analysis already holds."""
    overlays = [
        (o.first.seconds, o.last.seconds, o.text) for o in analysis.overlays if o.ok and o.text
    ]
    shots: list[VisualShot] = []
    for shot in analysis.shots:
        start, end = shot.range.start.seconds, shot.range.end.seconds
        text = [i.text for i in shot.text.items] if shot.text.ok else []
        text += [t for a, b, t in overlays if b > start and a < end]
        meaning = shot.meaning
        shots.append(
            VisualShot(
                shot_id=shot.shot_id,
                start=start,
                end=end,
                scene_id=shot.scene_id,
                description=meaning.description if meaning.ok else None,
                objects=tuple(meaning.objects) if meaning.ok else (),
                screen_text=tuple(text),
            )
        )
    p = analysis.provenance
    digest = hashlib.sha256(
        repr(
            (
                analysis.asset_checksum,
                p.processing_version,
                p.profile_name,
                p.profile_version,
                sorted(p.analyzer_versions.items()),
            )
        ).encode()
    ).hexdigest()[:16]
    return VisualContext(
        asset_id=analysis.asset_id,
        fingerprint=digest,
        duration=analysis.source.duration_seconds,
        shots=tuple(shots),
    )
