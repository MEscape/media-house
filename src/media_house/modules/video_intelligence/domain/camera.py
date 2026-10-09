"""Camera movement and picture motion of one shot, derived from the motion signals.

Measures and describes; it never says what to do about shake. On stabilized footage the original
shake is gone, so every observation carries whether (and on what version) it was measured.
"""

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from media_house.core.domain import FrameTime
from media_house.modules.video_intelligence.domain.observations import (
    CameraObservation,
    Evidence,
    MotionObservation,
)
from media_house.modules.video_intelligence.domain.profiles import MotionSettings
from media_house.modules.video_intelligence.domain.signals import MotionSignals, ShotSignals
from media_house.modules.video_intelligence.domain.source import ProcessingHistory
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerState,
    CameraMovement,
    Stabilization,
)

#: One axis must dominate the other by this factor for a pure pan or tilt.
_AXIS_DOMINANCE = 1.5


@dataclass(frozen=True, slots=True)
class _Pair:
    index: int
    frame: int
    seconds: float
    vx: float  # content velocity, fraction of frame width per second (right positive)
    vy: float  # ... fraction of frame width per second (down positive; height scaled to width)
    zoom: float  # rate of the log picture scale per second
    residual: float
    confidence: float


def _pairs_in(
    motion: MotionSignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    aspect: float,
) -> list[_Pair]:
    """Estimates whose two frames both lie in ``first..end-1`` (never across a boundary)."""
    pairs: list[_Pair] = []
    for index in range(bisect_left(motion.frames, first), len(motion.frames)):
        frame, previous = motion.frames[index], motion.prev_frames[index]
        if frame >= end:
            break
        if previous < first:
            continue
        seconds = (timeline.pts[frame] - timeline.pts[previous]) * timeline.timebase.value
        if seconds <= 0:
            continue
        pairs.append(
            _Pair(
                index,
                frame,
                seconds,
                motion.tx[index] / seconds,
                motion.ty[index] * aspect / seconds,
                motion.log_scale[index] / seconds,
                motion.residual[index],
                motion.confidence[index],
            )
        )
    return pairs


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total if total > 0 else 0.0


def _shake(pairs: Sequence[_Pair], settings: MotionSettings) -> float:
    """Rms distance of the camera path from its smoothed course (fraction of frame width)."""
    if len(pairs) < settings.smooth_samples:
        return 0.0
    x: list[float] = []
    y: list[float] = []
    cx = cy = 0.0
    for pair in pairs:
        cx += pair.vx * pair.seconds
        cy += pair.vy * pair.seconds
        x.append(cx)
        y.append(cy)
    half = settings.smooth_samples // 2
    squared = 0.0
    for k in range(len(pairs)):
        reach = min(half, k, len(pairs) - 1 - k)
        window = range(k - reach, k + reach + 1)
        mean_x = sum(x[j] for j in window) / len(window)
        mean_y = sum(y[j] for j in window) / len(window)
        squared += (x[k] - mean_x) ** 2 + (y[k] - mean_y) ** 2
    return math.sqrt(squared / len(pairs))


def _frame_times(timeline: ShotSignals, frames: Sequence[int]) -> tuple[FrameTime, ...]:
    return tuple(timeline.time_of(f) for f in dict.fromkeys(frames))


def describe_camera(
    motion: MotionSignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    aspect: float,
    settings: MotionSettings,
    history: ProcessingHistory,
) -> CameraObservation:
    """Camera movement of the shot ``first..end-1``. ``aspect`` is frame height / width."""
    pairs = [
        p
        for p in _pairs_in(motion, timeline, first, end, aspect)
        if p.confidence >= settings.min_confidence
    ]
    if len(pairs) < settings.min_pairs:
        return CameraObservation(
            state=AnalyzerState.UNKNOWN,
            reasons=("too_few_reliable_motion_estimates",),
            pairs=len(pairs),
            measured_on=history.measured_on,
            stabilized=history.stabilized,
        )

    weights = [p.confidence for p in pairs]
    vx = _weighted_mean([p.vx for p in pairs], weights)
    vy = _weighted_mean([p.vy for p in pairs], weights)
    zoom = _weighted_mean([p.zoom for p in pairs], weights)
    speed = math.hypot(vx, vy)
    shake = _shake(pairs, settings)

    def agreeing(values: Sequence[float], mean: float) -> float:
        return sum(1 for v in values if v * mean > 0) / len(values)

    translating = speed >= settings.pan_speed
    zooming = abs(zoom) >= settings.zoom_rate
    shaky = shake >= settings.shake_residual
    reasons: list[str] = []
    if shaky:
        reasons.append("handheld_shake")
    if history.stabilized is Stabilization.YES:
        reasons.append("measured_on_stabilized_footage")

    active: list[CameraMovement] = []
    consistency = 1.0
    if translating:
        horizontal = abs(vx) >= _AXIS_DOMINANCE * abs(vy)
        vertical = abs(vy) >= _AXIS_DOMINANCE * abs(vx)
        axis = [p.vx for p in pairs] if horizontal or not vertical else [p.vy for p in pairs]
        axis_mean = vx if horizontal or not vertical else vy
        agree = agreeing(axis, axis_mean)
        if agree >= settings.consistency:
            consistency = min(consistency, agree)
            if horizontal:
                active.append(CameraMovement.PAN)
            elif vertical:
                active.append(CameraMovement.TILT)
            else:
                active.extend((CameraMovement.PAN, CameraMovement.TILT))
        else:
            reasons.append("direction_not_consistent")
    if zooming:
        agree = agreeing([p.zoom for p in pairs], zoom)
        if agree >= settings.consistency:
            consistency = min(consistency, agree)
            active.append(CameraMovement.PUSH_IN if zoom > 0 else CameraMovement.PULL_OUT)
        else:
            reasons.append("scale_change_not_consistent")

    if len(active) > 1:
        movement = CameraMovement.MIXED
    elif active:
        movement = active[0]
    elif shaky or (translating or zooming):
        movement = CameraMovement.HANDHELD
    else:
        movement = CameraMovement.STATIC
        if speed >= settings.static_speed:
            reasons.append("slow_drift")

    intensity = min(1.0, max(speed, abs(zoom)) / settings.full_scale_speed)
    direction = (
        round(math.degrees(math.atan2(vy, -vx)), 3) % 360.0
        if speed >= settings.static_speed
        else None
    )
    confidence = (
        sum(weights) / len(weights) * consistency * min(1.0, len(pairs) / (2 * settings.min_pairs))
    )
    frames = [pairs[0].frame, pairs[len(pairs) // 2].frame, pairs[-1].frame]
    return CameraObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, confidence),
        evidence=(Evidence(frames=_frame_times(timeline, frames), metric="global_motion"),),
        reasons=tuple(reasons),
        movement=movement,
        intensity=intensity,
        direction_degrees=direction,
        speed=speed,
        zoom_rate=zoom,
        shake_residual=shake,
        measured_on=history.measured_on,
        stabilized=history.stabilized,
        pairs=len(pairs),
    )


def describe_motion(
    motion: MotionSignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    aspect: float,
    settings: MotionSettings,
) -> MotionObservation:
    """Picture motion left after the camera's own motion is removed."""
    pairs = _pairs_in(motion, timeline, first, end, aspect)
    if not pairs:
        return MotionObservation(
            state=AnalyzerState.UNKNOWN, reasons=("no_motion_estimate_inside_shot",)
        )
    residuals = [p.residual for p in pairs]
    peak = max(pairs, key=lambda p: p.residual)
    still = sum(
        1
        for p in pairs
        if p.residual < settings.static_residual
        and math.hypot(p.vx, p.vy) < settings.static_speed
        and abs(p.zoom) < settings.zoom_rate / 2
    )
    return MotionObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(pairs) / 6),
        evidence=(Evidence(frames=_frame_times(timeline, [peak.frame]), metric="residual"),),
        mean_energy=sum(residuals) / len(residuals),
        peak_energy=peak.residual,
        peak_time=timeline.time_of(peak.frame),
        static_fraction=still / len(pairs),
    )
