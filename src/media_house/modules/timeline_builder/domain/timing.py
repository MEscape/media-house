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


def frame_at(t: Fraction, fps: Fraction) -> int:
    return round(t * fps)


def time_of_frame(frame: int, fps: Fraction) -> Rational:
    """The exact start of a frame."""
    return rat(Fraction(frame) / fps)


def sample_at(t: Fraction, sample_rate: int) -> int:
    return round(t * sample_rate)


def time_of_sample(sample: int, sample_rate: int) -> Rational:
    return rat(Fraction(sample, sample_rate))


def parse_rational(text: str) -> Rational:
    """``"3/2"``, ``"2"`` or ``"0.25"`` as an exact, non-negative time."""
    try:
        return rat(Fraction(text.strip()))
    except (ValueError, ZeroDivisionError, InvariantViolation) as exc:
        raise ValueError(f"not an exact time: {text!r}") from exc
