"""Deterministic seconds <-> frame conversion. Seconds stay canonical; frames are derived.

Frame ``n`` covers the half-open interval ``[n / fps, (n + 1) / fps)``. Pass NTSC rates as
exact fractions (``Fraction(30000, 1001)``); a float such as 29.97 is taken literally.
Floating-point noise (``0.1 + 0.2``) is removed before rounding so boundaries are stable.
"""

import math
from enum import StrEnum
from fractions import Fraction

from media_house.shared.errors import InvariantViolation

type Fps = int | float | Fraction

_NOISE_DENOMINATOR = 10**9


class FrameRounding(StrEnum):
    FLOOR = "floor"  # the frame that contains the instant (default everywhere)
    ROUND = "round"  # nearest frame boundary, halves away from zero
    CEIL = "ceil"  # first frame boundary at or after the instant


def _fps(fps: Fps) -> Fraction:
    value = (
        Fraction(fps).limit_denominator(_NOISE_DENOMINATOR)
        if isinstance(fps, float)
        else Fraction(fps)
    )
    if value <= 0:
        raise InvariantViolation("FPS must be positive", details={"fps": str(fps)})
    return value


def seconds_to_frame(
    seconds: float,
    fps: Fps,
    rounding: FrameRounding = FrameRounding.FLOOR,
) -> int:
    if not math.isfinite(seconds):
        raise InvariantViolation("Timestamp must be finite")
    exact = Fraction(seconds).limit_denominator(_NOISE_DENOMINATOR) * _fps(fps)
    match rounding:
        case FrameRounding.FLOOR:
            return math.floor(exact)
        case FrameRounding.CEIL:
            return math.ceil(exact)
        case FrameRounding.ROUND:
            return (
                math.floor(exact + Fraction(1, 2))
                if exact >= 0
                else -math.floor(-exact + Fraction(1, 2))
            )


def frame_to_seconds(frame: int, fps: Fps) -> float:
    """Start time of ``frame``."""
    return float(Fraction(frame) / _fps(fps))
