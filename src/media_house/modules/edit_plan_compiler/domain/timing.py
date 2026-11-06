"""Exact time arithmetic. Seconds are ``Fraction``s; frames and samples are computed once.

The rule of every module in the system: ``frame = round(t * fps)`` with Python's half-to-even
rounding on the EXACT value, and a duration in frames is ``end_frame - start_frame``, never
``round(duration * fps)``. Floats never address a frame or a sample.
"""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.shared.errors import InvariantViolation

ZERO = Rational(0, 1)
ONE = Rational(1, 1)


def fr(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def rat(value: Fraction) -> Rational:
    if value < 0:
        raise InvariantViolation(f"a time cannot be negative: {value}")
    return Rational(value.numerator, value.denominator)


def seconds_text(value: Fraction) -> str:
    return f"{float(value):.6f}"


def frame_at(t: Fraction, fps: Fraction) -> int:
    return round(t * fps)


def snap(t: Fraction, fps: Fraction) -> Fraction:
    """The frame boundary nearest to ``t``."""
    return Fraction(frame_at(t, fps)) / fps


def sample_at(t: Fraction, sample_rate: int) -> int:
    return round(t * sample_rate)


def frame_span(start: Fraction, end: Fraction, fps: Fraction) -> tuple[int, int, int]:
    """``(start_frame, end_frame, frames)`` with ``frames = end_frame - start_frame``."""
    first, last = frame_at(start, fps), frame_at(end, fps)
    return first, last, last - first


def overlap(a_start: Fraction, a_end: Fraction, b_start: Fraction, b_end: Fraction) -> bool:
    """Half-open intervals ``[start, end)`` intersect."""
    return a_start < b_end and b_start < a_end
