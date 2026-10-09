"""Picture quality of one shot: raw metrics and the descriptive reasons derived from them.

Metrics only. A reason code names a property of the picture (``soft_focus``), never an action.
Flat or log-encoded footage has little tonal spread, so exposure is NOT judged on it: a flat
picture must not be reported as underexposed.
"""

from bisect import bisect_left
from collections.abc import Sequence
from statistics import median

from media_house.modules.video_intelligence.domain.observations import (
    Evidence,
    QualityAssessment,
    QualityMetrics,
)
from media_house.modules.video_intelligence.domain.profiles import QualitySettings
from media_house.modules.video_intelligence.domain.signals import QualitySignals, ShotSignals
from media_house.modules.video_intelligence.domain.source import ProcessingHistory, SourceInfo
from media_house.modules.video_intelligence.domain.values import AnalyzerState

#: Frames next to a shot boundary are left out of the flicker measurement.
_EDGE_FRAMES = 2
_MIN_FLICKER_FRAMES = 5
#: Sampled frames at which the confidence of a median is full.
_FULL_CONFIDENCE_SAMPLES = 6


def flicker(timeline: ShotSignals, first: int, end: int) -> float | None:
    """Rms luma flutter: how far each frame's mean brightness lies from its neighbours' mean."""
    low, high = first + _EDGE_FRAMES, end - _EDGE_FRAMES
    if high - low < _MIN_FLICKER_FRAMES:
        return None
    luma = timeline.luma_mean
    squares = [(luma[i] - (luma[i - 1] + luma[i + 1]) / 2) ** 2 for i in range(low, high)]
    return float((sum(squares) / len(squares)) ** 0.5)


def _slice(quality: QualitySignals, first: int, end: int) -> range:
    start = bisect_left(quality.frames, first)
    return range(start, bisect_left(quality.frames, end))


def _middle(values: Sequence[float]) -> float:
    return float(median(values))


def describe_quality(
    quality: QualitySignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    settings: QualitySettings,
    source: SourceInfo,
    history: ProcessingHistory,
) -> QualityAssessment:
    rows = _slice(quality, first, end)
    flutter = flicker(timeline, first, end)
    if len(rows) < settings.min_samples:
        return QualityAssessment(
            state=AnalyzerState.UNKNOWN,
            reasons=("too_few_sampled_frames",),
            metrics=QualityMetrics(flicker=flutter, sampled_frames=len(rows)),
            measured_on=history.measured_on,
        )

    def column(values: Sequence[float]) -> float:
        return _middle([values[i] for i in rows])

    p1, p50, p99 = column(quality.luma_p1), column(quality.luma_p50), column(quality.luma_p99)
    sharpness, noise = column(quality.sharpness), column(quality.noise_sigma)
    clipped, crushed = column(quality.clipped), column(quality.crushed)
    metrics = QualityMetrics(
        sharpness=sharpness,
        noise_sigma=noise,
        luma_p1=p1,
        luma_p50=p50,
        luma_p99=p99,
        tonal_spread=p99 - p1,
        clipped=clipped,
        crushed=crushed,
        flicker=flutter,
        sampled_frames=len(rows),
    )

    reasons: list[str] = []
    if sharpness < settings.low_sharpness:
        reasons.append("soft_focus")
    if noise > settings.high_noise:
        reasons.append("noisy")
    if flutter is not None and flutter > settings.flicker:
        reasons.append("flicker_present")

    transfer = (source.color_transfer or "").lower()
    if transfer in settings.non_display_transfers:
        reasons.append("exposure_not_judged_non_display_transfer")
    elif p99 - p1 < settings.flat_spread and p1 >= settings.flat_black_floor:
        reasons.append("flat_or_log_footage")
    else:
        if clipped > settings.clipped_fraction:
            reasons.append("clipped_highlights")
        if crushed > settings.crushed_fraction:
            reasons.append("crushed_shadows")
        if p50 < settings.dark_median:
            reasons.append("dark_exposure")
        elif p50 > settings.bright_median:
            reasons.append("bright_exposure")

    sharpest = max(rows, key=lambda i: (quality.sharpness[i], -i))
    middle = min(rows, key=lambda i: (abs(quality.luma_p50[i] - p50), i))
    frames = tuple(timeline.time_of(quality.frames[i]) for i in dict.fromkeys((sharpest, middle)))
    return QualityAssessment(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(rows) / _FULL_CONFIDENCE_SAMPLES),
        evidence=(Evidence(frames=frames, metric="sampled_frame_metrics"),),
        reasons=tuple(reasons),
        metrics=metrics,
        measured_on=history.measured_on,
    )
