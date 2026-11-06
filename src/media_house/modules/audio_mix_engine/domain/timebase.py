"""Exact time: frames, seconds and samples are converted once, from rational values.

A frame rate is a ``Rational`` (30000/1001 is not 29.97); a position in samples is
``round(exact seconds * sample rate)`` with half-to-even rounding, never an accumulation of
rounded steps. Floats never address a sample.
"""

from fractions import Fraction

from media_house.core.domain import Rational


def fr(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def rat(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)


def frame_to_sample(frame: int, frame_rate: Rational, sample_rate: int) -> int:
    """The sample at which a frame starts."""
    return round(Fraction(frame) / fr(frame_rate) * sample_rate)


def seconds_to_sample(seconds: Fraction, sample_rate: int) -> int:
    return round(seconds * sample_rate)


def sample_to_seconds(sample: int, sample_rate: int) -> Fraction:
    return Fraction(sample, sample_rate)


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)
