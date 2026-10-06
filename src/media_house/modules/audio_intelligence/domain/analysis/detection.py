"""Frame-level event detection: sudden pitch/energy changes and stretches of silence."""

import math
from collections.abc import Sequence

from media_house.modules.audio_intelligence.domain.analysis import stats
from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    ENERGY_DROP,
    ENERGY_SPIKE,
    PITCH_FALL,
    PITCH_RISE,
    SILENCE,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import Baseline
from media_house.modules.audio_intelligence.domain.analysis.config import AnalysisConfig

_SILENCE_FULL_SCALE_SECONDS = 1.5


def detect_acoustic_events(
    track: AcousticTrack,
    baseline: Baseline,
    config: AnalysisConfig,
) -> tuple[AudioEvent, ...]:
    """Pitch rises/falls, energy spikes/drops and silences, sorted by start time."""
    events = [
        *_pitch_events(track, baseline, config),
        *_energy_events(track, baseline, config),
        *_silence_events(track, config),
    ]
    return tuple(sorted(events, key=lambda e: (e.start, e.kind)))


def _pitch_events(
    track: AcousticTrack,
    baseline: Baseline,
    config: AnalysisConfig,
) -> list[AudioEvent]:
    reference = baseline.pitch_median_hz
    if reference is None:
        return []
    events: list[AudioEvent] = []
    for first, last in _voiced_runs(track, config):
        contour = [stats.semitones(track.f0[i], reference) for i in range(first, last + 1)]
        smooth = stats.moving_median(contour, config.smoothing_frames)
        for a, b, delta in stats.swings(smooth, config.pitch_event_min_st):
            if (b - a) * track.hop > config.pitch_event_max_duration:
                continue  # a slow glide is a trend, not a sudden change
            frames = range(first + a, first + b + 1)
            events.append(
                AudioEvent(
                    kind=PITCH_RISE if delta > 0 else PITCH_FALL,
                    start=track.frame_start(first + a),
                    end=track.frame_start(first + b) + track.hop,
                    strength=stats.saturate(abs(delta), config.pitch_full_scale_st) or 0.0,
                    confidence=stats.mean([track.pitch_confidence[i] for i in frames]),
                    source="pitch",
                    details={"delta_st": delta},
                ),
            )
    return events


def _voiced_runs(track: AcousticTrack, config: AnalysisConfig) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(len(track)):
        if track.is_voiced(i, config.min_pitch_confidence):
            start = i if start is None else start
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(track) - 1))
    return [r for r in runs if r[1] - r[0] + 1 >= config.smoothing_frames]


def _energy_events(
    track: AcousticTrack,
    baseline: Baseline,
    config: AnalysisConfig,
) -> list[AudioEvent]:
    if baseline.energy_db is None:
        return []
    # Clamp below the speech floor so the fall into silence is one capped drop, not a cliff.
    floor = baseline.energy_db.p10
    typical = baseline.energy_db.median
    levels = stats.moving_median([max(v, floor) for v in track.rms_db], config.smoothing_frames)
    events: list[AudioEvent] = []
    for a, b, delta in stats.swings(levels, config.energy_event_min_db):
        if (b - a) * track.hop > config.energy_event_max_duration:
            continue
        if not (track.speech[a] >= 0.5 or track.speech[b] >= 0.5):
            continue
        # Syllable-level wobble is normal speech: a spike must reach the speaker's typical
        # level or above, a drop must start from at least that level.
        if (delta > 0 and levels[b] < typical) or (delta < 0 and levels[a] < typical):
            continue
        events.append(
            AudioEvent(
                kind=ENERGY_SPIKE if delta > 0 else ENERGY_DROP,
                start=track.frame_start(a),
                end=track.frame_start(b) + track.hop,
                strength=stats.saturate(abs(delta), config.energy_full_scale_db) or 0.0,
                source="energy",
                details={"delta_db": delta},
            ),
        )
    return events


def _silence_events(track: AcousticTrack, config: AnalysisConfig) -> list[AudioEvent]:
    events: list[AudioEvent] = []
    start: int | None = None
    for i in range(len(track) + 1):
        silent = i < len(track) and track.speech[i] < 0.5
        if silent and start is None:
            start = i
        elif not silent and start is not None:
            duration = (i - start) * track.hop
            if duration >= config.min_silence:
                events.append(
                    AudioEvent(
                        kind=SILENCE,
                        start=track.frame_start(start),
                        end=track.frame_start(i),
                        strength=stats.saturate(duration, _SILENCE_FULL_SCALE_SECONDS) or 0.0,
                        source="activity",
                    ),
                )
            start = None
    return events


def overlapping(
    events: Sequence[AudioEvent],
    start: float,
    end: float,
    kinds: frozenset[str] | None = None,
) -> list[AudioEvent]:
    """Events whose MIDPOINT lies in ``[start, end)`` (an event belongs to one word)."""
    return [
        e
        for e in events
        if (kinds is None or e.kind in kinds)
        and start <= e.midpoint < end
        and math.isfinite(e.start)
    ]
