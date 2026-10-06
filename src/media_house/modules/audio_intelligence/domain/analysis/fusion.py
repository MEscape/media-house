"""Fusion: transcript + measured frames -> one Audio Intelligence Timeline.

Pipeline (each step only reads the previous ones):

    measured frames --> baseline --> local rates --> events + pauses
        --> per-word / per-segment evidence --> scorer --> editing signals --> validation

Pure and deterministic: the same inputs and configuration always give the same timeline.
"""

from dataclasses import replace
from datetime import datetime

from media_house.modules.audio_intelligence.domain.analysis import stats
from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    PAUSE,
    AcousticMeasurements,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import (
    describe,
    estimate_baseline,
)
from media_house.modules.audio_intelligence.domain.analysis.config import (
    ANALYSIS_VERSION,
    AudioIntelligenceConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.detection import (
    detect_acoustic_events,
)
from media_house.modules.audio_intelligence.domain.analysis.features import (
    local_rates,
    segment_acoustics,
    word_acoustics,
)
from media_house.modules.audio_intelligence.domain.analysis.pauses import detect_pauses
from media_house.modules.audio_intelligence.domain.analysis.scoring import Scorer, ScoringInput
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AnalysisMetadata,
    AudioIntelligenceTimeline,
    TimelineSegment,
    TimelineWord,
)
from media_house.modules.audio_intelligence.domain.analysis.validation import (
    summarize,
    validate_timeline,
)
from media_house.modules.audio_intelligence.domain.transcript import Transcript


def build_timeline(
    *,
    transcript: Transcript,
    measurements: AcousticMeasurements,
    config: AudioIntelligenceConfig,
    scorer: Scorer,
    audio_offset: float,
    created_at: datetime,
) -> AudioIntelligenceTimeline:
    """``measurements`` are in PREPARED-audio time; ``audio_offset`` maps them to the source."""
    analysis = config.analysis
    track = replace(measurements.track, origin=measurements.track.origin + audio_offset)
    measured_events = tuple(
        replace(e, start=e.start + audio_offset, end=e.end + audio_offset)
        for e in measurements.events
    )
    words = transcript.words

    rates = local_rates(words, transcript.duration, analysis)
    baseline = replace(
        estimate_baseline(track, analysis),
        speech_rate=describe([r.words_per_second for r in rates]),
        word_duration_per_char=describe(
            [
                w.duration / len(w.normalized_word or w.raw_word)
                for w in words
                if w.duration > 0 and (w.normalized_word or w.raw_word)
            ],
        ),
    )
    pauses = detect_pauses(transcript, track, analysis)
    pause_scale = config.scoring.full_scale["pause_seconds"]
    pause_events = tuple(
        AudioEvent(
            kind=PAUSE,
            start=p.start,
            end=p.end,
            strength=stats.saturate(p.duration, pause_scale) or 0.0,
            source="pauses",
            details={"duration": p.duration},
        )
        for p in pauses
    )
    events = tuple(
        sorted(
            [*measured_events, *detect_acoustic_events(track, baseline, analysis), *pause_events],
            key=lambda e: (e.start, e.kind),
        ),
    )
    acoustics = word_acoustics(words, track, baseline, events, rates, analysis)
    segments = segment_acoustics(transcript.segments, track, baseline, rates, pauses, analysis)
    scored = scorer.score(
        ScoringInput(transcript, acoustics, segments, pauses, events, baseline, analysis),
    )

    metadata = AnalysisMetadata(
        source_asset_id=transcript.metadata.source_asset_id,
        audio_asset_id=transcript.metadata.audio_asset_id,
        duration=transcript.duration,
        audio_offset=audio_offset,
        created_at=created_at,
        analysis_version=ANALYSIS_VERSION,
        scoring=scorer.identity,
        min_pitch_confidence=analysis.min_pitch_confidence,
        analyzers=dict(measurements.identity),
        parameters=dict(measurements.parameters),
        settings={"analysis": analysis.to_config(), "scoring": config.scoring.to_config()},
        warnings=measurements.warnings,
    )
    timeline = AudioIntelligenceTimeline(
        metadata=metadata,
        transcript=transcript,
        track=track,
        baseline=baseline,
        events=events,
        pauses=pauses,
        words=tuple(
            TimelineWord(w, a, s) for w, a, s in zip(words, acoustics, scored.words, strict=True)
        ),
        segments=tuple(
            TimelineSegment(s, a) for s, a in zip(transcript.segments, scored.segments, strict=True)
        ),
        editing_signals=scored.editing_signals,
    )
    findings = summarize(validate_timeline(timeline))
    return replace(timeline, metadata=replace(metadata, warnings=(*metadata.warnings, *findings)))
