"""Adaptive frame sampling: dense where the picture changes, sparse where it is still.

A plan is a pure function of the per-frame signals and the profile, so it is deterministic,
explainable and identical whether the signals were just measured or loaded from the cache. There
are two plans: the grey samples (motion, quality, saliency) and the colour frames (models and
colour geometry), each with its own rate, longest gap and change threshold.
"""

from media_house.modules.video_intelligence.domain.profiles import MeasurementSettings
from media_house.modules.video_intelligence.domain.signals import ShotSignals

_DEFAULT_FPS = 30.0


def _stride(nominal_fps: float | None, candidate_fps: float) -> int:
    fps = nominal_fps if nominal_fps and nominal_fps > 0 else _DEFAULT_FPS
    return max(1, round(fps / candidate_fps))


def decode_stride(nominal_fps: float | None, settings: MeasurementSettings) -> int:
    """Distance in frames between the candidate grey sample positions that are decoded."""
    return _stride(nominal_fps, settings.candidate_fps)


def rgb_stride(nominal_fps: float | None, settings: MeasurementSettings) -> int:
    """Distance in frames between the candidate colour frame positions that are decoded."""
    return _stride(nominal_fps, settings.rgb_fps)


def _plan(
    signals: ShotSignals, stride: int, gap_seconds: float, change_threshold: float
) -> tuple[int, ...]:
    """Candidates (every ``stride``-th frame) kept when the picture changed enough since the last
    kept one (summed frame difference reaches ``change_threshold``) or when ``gap_seconds`` passed.
    The first candidate is always kept."""
    fps = signals.frames_per_second or _DEFAULT_FPS
    longest_gap = max(stride, round(gap_seconds * fps))
    kept: list[int] = []
    last = -1
    previous = 0
    accumulated = 0.0
    for frame in range(0, signals.frame_count, stride):
        accumulated += sum(signals.diff1[previous + 1 : frame + 1])
        previous = frame
        if last < 0 or accumulated >= change_threshold or frame - last >= longest_gap:
            kept.append(frame)
            last = frame
            accumulated = 0.0
    return tuple(kept)


def plan_samples(
    signals: ShotSignals, stride: int, settings: MeasurementSettings
) -> tuple[int, ...]:
    """The grey sample frames to analyse, in order."""
    return _plan(signals, stride, settings.static_gap_seconds, settings.change_threshold)


def plan_rgb(signals: ShotSignals, stride: int, settings: MeasurementSettings) -> tuple[int, ...]:
    """The colour frames to analyse, in order."""
    return _plan(signals, stride, settings.rgb_gap_seconds, settings.rgb_change_threshold)
