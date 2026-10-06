"""Measured and baseline-relative features per word and per segment.

Everything here is EVIDENCE: statistics of the continuous frames inside a word/segment, compared
with the speaker baseline ("relative") and with the speech just before ("local"). No judgement and
no editing meaning; scores are produced later by a ``Scorer``. ``None`` always means "not
measurable here" (unvoiced word, no frames, too little context), never zero.
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from media_house.modules.audio_intelligence.domain.analysis import stats
from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    ENERGY_DROP,
    ENERGY_SPIKE,
    PITCH_FALL,
    PITCH_RISE,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import Baseline
from media_house.modules.audio_intelligence.domain.analysis.config import AnalysisConfig
from media_house.modules.audio_intelligence.domain.analysis.detection import overlapping
from media_house.modules.audio_intelligence.domain.analysis.pauses import Pause
from media_house.modules.audio_intelligence.domain.transcript import (
    TranscriptSegment,
    TranscriptWord,
)

_MIN_LOCAL_FRAMES = 10
_MIN_RANGE_FRAMES = 5
_MIN_RATE_SECONDS = 0.5
_MIN_RATE_WORDS = 2
_MIN_CV_WORDS = 3
FLAT = "flat"
RISE = "rise"
FALL = "fall"


@dataclass(frozen=True, slots=True)
class PitchSummary:
    """F0 inside one word. Hz values are measured; ``*_st`` are semitones."""

    voiced_frames: int
    voiced_ratio: float
    #: Everything below is ``None`` when fewer than ``min_word_voiced_frames`` were voiced.
    mean_hz: float | None = None
    median_hz: float | None = None
    min_hz: float | None = None
    max_hz: float | None = None
    range_st: float | None = None
    std_st: float | None = None
    slope_st_per_s: float | None = None
    confidence: float | None = None
    #: Median pitch relative to the speaker baseline median (semitones).
    relative_st: float | None = None
    #: Robust z-score against the speaker's pitch distribution.
    z_score: float | None = None
    #: Share (0..1) of the speaker's voiced frames at or below this word's median pitch.
    percentile: float | None = None
    #: Median pitch minus the median of the preceding ``local_window`` seconds (semitones).
    local_deviation_st: float | None = None


@dataclass(frozen=True, slots=True)
class EnergySummary:
    frames: int
    mean_db: float
    peak_db: float
    loudness_lufs: float | None
    dynamic_range_db: float | None
    std_db: float | None
    slope_db_per_s: float | None
    #: Mean level minus the speaker baseline median (dB).
    relative_db: float | None
    #: Mean level minus the preceding ``local_window`` seconds of speech (dB).
    local_deviation_db: float | None


@dataclass(frozen=True, slots=True)
class PitchChange:
    """Strongest sudden pitch movement centred in the word (0 = none observed)."""

    direction: str
    rise: float
    fall: float

    @property
    def strength(self) -> float:
        return max(self.rise, self.fall)


@dataclass(frozen=True, slots=True)
class EnergyChange:
    rise: float
    drop: float

    @property
    def strength(self) -> float:
        return max(self.rise, self.drop)


@dataclass(frozen=True, slots=True)
class RateSummary:
    """Local speaking rate around a word (window ``rate_window``, pauses excluded)."""

    words_per_second: float
    words_per_minute: float
    #: Rate divided by the speaker's median rate (1 = typical, 2 = twice as fast).
    relative: float | None
    #: Signed acceleration -1..1: tanh(log2(rate after / rate before)); + = speeding up.
    change: float | None
    #: Seconds per character of this word relative to the speaker's median (1 = typical).
    duration_ratio: float | None


@dataclass(frozen=True, slots=True)
class WordAcoustics:
    """Everything measured about one word. Frames are referenced, never copied."""

    first_frame: int | None
    last_frame: int | None
    pitch: PitchSummary | None
    energy: EnergySummary | None
    pitch_change: PitchChange | None
    energy_change: EnergyChange | None
    rate: RateSummary | None


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    """A 0..1 score plus the evidence (each 0..1) that produced it."""

    score: float
    contributors: dict[str, float]


@dataclass(frozen=True, slots=True)
class SegmentAcoustics:
    word_count: int
    pitch_median_hz: float | None
    pitch_range_st: float | None
    pitch_std_st: float | None
    #: Power-domain mean RMS (dBFS) over speech frames; dB values are never averaged directly.
    energy_db: float | None
    loudness_lufs: float | None
    speech_rate_wps: float | None
    rate_cv: float | None
    pause_before: Pause | None
    pause_after: Pause | None
    # derived (filled by the scorer; ``None`` = not enough evidence)
    expressiveness: ScoredSignal | None = None
    monotony: ScoredSignal | None = None
    emphasis_mean: float | None = None
    emphasis_max: float | None = None
    moment_max: float | None = None


# --- speaking rate -------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LocalRate:
    words_per_second: float
    change: float | None


def local_rates(
    words: Sequence[TranscriptWord],
    duration: float,
    config: AnalysisConfig,
) -> list[LocalRate]:
    """Rolling words-per-second per word, excluding long pauses from the speaking time."""
    midpoints = [(w.start + w.end) / 2 for w in words]
    horizon = max([duration, *(w.end for w in words)])
    gaps = [
        (a.end, b.start) for a, b in pairwise(words) if b.start - a.end >= config.articulation_pause
    ]
    gap_starts = [g[0] for g in gaps]
    half = config.rate_window / 2

    def rate(window_start: float, window_end: float) -> tuple[int, float]:
        lo, hi = max(0.0, window_start), min(window_end, horizon)
        count = bisect_left(midpoints, hi) - bisect_left(midpoints, lo)
        silent = 0.0
        first = max(0, bisect_right(gap_starts, lo) - 1)
        for gap_start, gap_end in gaps[first:]:
            if gap_start >= hi:
                break
            silent += max(0.0, min(gap_end, hi) - max(gap_start, lo))
        return count, max(_MIN_RATE_SECONDS, (hi - lo) - silent)

    rates: list[LocalRate] = []
    for m in midpoints:
        count, speaking = rate(m - half, m + half)
        before_n, before_t = rate(m - half, m)
        after_n, after_t = rate(m, m + half)
        change: float | None = None
        if before_n >= _MIN_RATE_WORDS and after_n >= _MIN_RATE_WORDS:
            change = math.tanh(math.log2((after_n / after_t) / (before_n / before_t)))
        rates.append(LocalRate(count / speaking, change))
    return rates


# --- words ---------------------------------------------------------------------------------------
def word_acoustics(
    words: Sequence[TranscriptWord],
    track: AcousticTrack,
    baseline: Baseline,
    events: Sequence[AudioEvent],
    rates: Sequence[LocalRate],
    config: AnalysisConfig,
) -> list[WordAcoustics]:
    result: list[WordAcoustics] = []
    for word, rate in zip(words, rates, strict=True):
        frames = track.span(word.start, word.end)
        if not frames:
            index = track.index_at((word.start + word.end) / 2)
            frames = range(index, index + 1) if index is not None else range(0)
        pitch = _pitch_summary(track, baseline, config, frames, word) if frames else None
        energy = _energy_summary(track, baseline, config, frames, word) if frames else None
        result.append(
            WordAcoustics(
                first_frame=frames[0] if frames else None,
                last_frame=frames[-1] if frames else None,
                pitch=pitch,
                energy=energy,
                pitch_change=_pitch_change(events, word, pitch, config),
                energy_change=_energy_change(events, word, energy),
                rate=_rate_summary(word, rate, baseline),
            ),
        )
    return result


def _pitch_summary(
    track: AcousticTrack,
    baseline: Baseline,
    config: AnalysisConfig,
    frames: range,
    word: TranscriptWord,
) -> PitchSummary:
    voiced = [i for i in frames if track.is_voiced(i, config.min_pitch_confidence)]
    ratio = len(voiced) / len(frames)
    if len(voiced) < config.min_word_voiced_frames:
        return PitchSummary(len(voiced), ratio)
    hz = [track.f0[i] for i in voiced]
    times = [track.frame_start(i) + track.hop / 2 for i in voiced]
    median_hz = stats.median(hz)
    assert median_hz is not None  # noqa: S101  # voiced frames are finite by construction
    reference = baseline.pitch_median_hz or median_hz
    contour = [stats.semitones(f, reference) for f in hz]
    spread = (
        (stats.percentile(contour, 95) or 0.0) - (stats.percentile(contour, 5) or 0.0)
        if len(contour) >= _MIN_RANGE_FRAMES
        else max(contour) - min(contour)
    )
    return PitchSummary(
        voiced_frames=len(voiced),
        voiced_ratio=ratio,
        mean_hz=stats.mean(hz),
        median_hz=median_hz,
        min_hz=min(hz),
        max_hz=max(hz),
        range_st=spread,
        std_st=stats.std(contour),
        slope_st_per_s=stats.slope(times, contour),
        confidence=stats.mean([track.pitch_confidence[i] for i in voiced]),
        relative_st=baseline.pitch_relative_st(median_hz),
        z_score=baseline.pitch_z(median_hz),
        percentile=baseline.pitch_percentile(median_hz),
        local_deviation_st=_local_pitch_deviation(track, baseline, config, word, median_hz),
    )


def _local_pitch_deviation(
    track: AcousticTrack,
    baseline: Baseline,
    config: AnalysisConfig,
    word: TranscriptWord,
    median_hz: float,
) -> float | None:
    previous = [
        track.f0[i]
        for i in track.span(word.start - config.local_window, word.start)
        if track.is_voiced(i, config.min_pitch_confidence)
    ]
    if len(previous) < _MIN_LOCAL_FRAMES:
        return None
    context = stats.median(previous)
    _ = baseline
    return None if context is None else stats.semitones(median_hz, context)


def _energy_summary(
    track: AcousticTrack,
    baseline: Baseline,
    config: AnalysisConfig,
    frames: range,
    word: TranscriptWord,
) -> EnergySummary | None:
    active = [i for i in frames if track.speech[i] >= 0.5] or list(frames)
    levels = [track.rms_db[i] for i in active]
    mean_db = stats.power_mean_db(levels)
    if mean_db is None:
        return None
    times = [track.frame_start(i) + track.hop / 2 for i in active]
    spread = (
        (stats.percentile(levels, 95) or 0.0) - (stats.percentile(levels, 5) or 0.0)
        if len(levels) >= _MIN_RANGE_FRAMES
        else None
    )
    previous = [
        track.rms_db[i]
        for i in track.span(word.start - config.local_window, word.start)
        if track.speech[i] >= 0.5
    ]
    context = stats.power_mean_db(previous) if len(previous) >= _MIN_LOCAL_FRAMES else None
    return EnergySummary(
        frames=len(active),
        mean_db=mean_db,
        peak_db=max(levels),
        loudness_lufs=stats.power_mean_db([track.loudness[i] for i in active]),
        dynamic_range_db=spread,
        std_db=stats.std(levels),
        slope_db_per_s=stats.slope(times, levels),
        relative_db=None if baseline.energy_db is None else mean_db - baseline.energy_db.median,
        local_deviation_db=None if context is None else mean_db - context,
    )


def _pitch_change(
    events: Sequence[AudioEvent],
    word: TranscriptWord,
    pitch: PitchSummary | None,
    config: AnalysisConfig,
) -> PitchChange | None:
    if pitch is None or pitch.median_hz is None:
        return None  # nothing measured: unknown, not "no change"
    _ = config
    inside = overlapping(events, word.start, word.end, frozenset({PITCH_RISE, PITCH_FALL}))
    rise = max((e.strength for e in inside if e.kind == PITCH_RISE), default=0.0)
    fall = max((e.strength for e in inside if e.kind == PITCH_FALL), default=0.0)
    direction = FLAT if rise == fall == 0.0 else (RISE if rise >= fall else FALL)
    return PitchChange(direction, rise, fall)


def _energy_change(
    events: Sequence[AudioEvent],
    word: TranscriptWord,
    energy: EnergySummary | None,
) -> EnergyChange | None:
    if energy is None:
        return None
    inside = overlapping(events, word.start, word.end, frozenset({ENERGY_SPIKE, ENERGY_DROP}))
    return EnergyChange(
        rise=max((e.strength for e in inside if e.kind == ENERGY_SPIKE), default=0.0),
        drop=max((e.strength for e in inside if e.kind == ENERGY_DROP), default=0.0),
    )


def _rate_summary(word: TranscriptWord, rate: LocalRate, baseline: Baseline) -> RateSummary:
    typical = baseline.speech_rate.median if baseline.speech_rate else None
    per_char = baseline.word_duration_per_char
    chars = len(word.normalized_word or word.raw_word)
    return RateSummary(
        words_per_second=rate.words_per_second,
        words_per_minute=rate.words_per_second * 60.0,
        relative=rate.words_per_second / typical if typical else None,
        change=rate.change,
        duration_ratio=(
            (word.duration / chars) / per_char.median
            if per_char and per_char.median > 0 and chars > 0
            else None
        ),
    )


# --- segments ------------------------------------------------------------------------------------
def segment_acoustics(
    segments: Sequence[TranscriptSegment],
    track: AcousticTrack,
    baseline: Baseline,
    rates: Sequence[LocalRate],
    pauses: Sequence[Pause],
    config: AnalysisConfig,
) -> list[SegmentAcoustics]:
    by_after = {p.after_word: p for p in pauses if p.after_word is not None}
    by_before = {p.before_word: p for p in pauses if p.before_word is not None}
    result: list[SegmentAcoustics] = []
    for segment in segments:
        frames = track.span(segment.start, segment.end)
        voiced = [track.f0[i] for i in frames if track.is_voiced(i, config.min_pitch_confidence)]
        speech = [i for i in frames if track.speech[i] >= 0.5]
        reference = baseline.pitch_median_hz or stats.median(voiced)
        contour = [stats.semitones(f, reference) for f in voiced] if reference else []
        words = segment.words
        local = [rates[w.index].words_per_second for w in words]
        speaking = (segment.end - segment.start) - sum(
            b.start - a.end
            for a, b in pairwise(words)
            if b.start - a.end >= config.articulation_pause
        )
        mean_rate = stats.mean(local)
        spread = stats.std(local)
        result.append(
            SegmentAcoustics(
                word_count=len(words),
                pitch_median_hz=stats.median(voiced) if len(voiced) >= _MIN_LOCAL_FRAMES else None,
                pitch_range_st=(
                    (stats.percentile(contour, 95) or 0.0) - (stats.percentile(contour, 5) or 0.0)
                    if len(contour) >= _MIN_LOCAL_FRAMES
                    else None
                ),
                pitch_std_st=stats.std(contour) if len(contour) >= _MIN_LOCAL_FRAMES else None,
                energy_db=stats.power_mean_db([track.rms_db[i] for i in speech]),
                loudness_lufs=stats.power_mean_db([track.loudness[i] for i in speech]),
                speech_rate_wps=(len(words) / max(speaking, _MIN_RATE_SECONDS) if words else None),
                rate_cv=(
                    spread / mean_rate
                    if len(words) >= _MIN_CV_WORDS and spread is not None and mean_rate
                    else None
                ),
                pause_before=by_after.get(words[0].index) if words else None,
                pause_after=by_before.get(words[-1].index) if words else None,
            ),
        )
    return result
