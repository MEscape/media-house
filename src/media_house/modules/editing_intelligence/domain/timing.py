"""Exact time for edit planning.

Conventions (one place, so no stage invents its own):

* A moment on a SOURCE recording is exact seconds (``Fraction`` inside the domain, ``Rational`` in
  the stored plan) on the recording's container timeline, the "common clock" the alignment uses.
  Upstream measures in floats; a float is taken to the nearest fraction with a denominator up
  to a million (``exact``, microsecond-accurate) and then never touched by floating point again.
* A FRAME index is derived, never measured here: ``floor(seconds * fps)`` on the recording's own
  constant frame rate. It exists only when that rate is known and the stream is not variable-rate;
  otherwise no frame is offered (a variable-rate frame index cannot be derived from seconds).
* A SAMPLE index is ``round(seconds * sample_rate)``.
* A range is half-open: ``[start, end)``. Inclusive ends are never used.
* TIMELINE time is exact seconds from the start of the edit; its frames are on the plan's
  timeline rate and are rounded to the nearest frame, the exact value stays the authority.
"""

import math
from fractions import Fraction

from media_house.core.domain import Rational

_MICRO = 1_000_000


def exact(seconds: float) -> Fraction:
    """A float second count as the nearest fraction with a denominator up to a million (so noise
    such as 0.1 + 0.2 is removed and the value is microsecond-accurate)."""
    return Fraction(seconds).limit_denominator(_MICRO)


def to_rational(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)


def to_fraction(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def seconds(value: Rational) -> float:
    return value.value


def frame_floor(moment: Fraction, rate: Rational) -> int:
    """The frame that contains ``moment`` on a constant frame rate."""
    return math.floor(moment * to_fraction(rate))


def frame_ceil(moment: Fraction, rate: Rational) -> int:
    """The first frame boundary at or after ``moment``: the exclusive end frame of a range."""
    return math.ceil(moment * to_fraction(rate))


def frame_round(moment: Fraction, rate: Rational) -> int:
    scaled = moment * to_fraction(rate)
    return math.floor(scaled + Fraction(1, 2))


def frame_start(frame: int, rate: Rational) -> Fraction:
    return Fraction(frame) / to_fraction(rate)


def sample_round(moment: Fraction, sample_rate: int) -> int:
    return math.floor(moment * sample_rate + Fraction(1, 2))


def snap_into(
    low: Fraction, high: Fraction, target: Fraction, rate: Rational
) -> tuple[Fraction, int] | None:
    """The frame boundary nearest ``target`` that lies inside ``[low, high]``, or ``None`` when the
    window holds no frame boundary (it is narrower than a frame and misses every one)."""
    centre = frame_round(target, rate)
    best_frame: int | None = None
    best_distance: Fraction | None = None
    for frame in (centre - 1, centre, centre + 1):
        at = frame_start(frame, rate)
        if frame < 0 or not low <= at <= high:
            continue
        distance = abs(at - target)
        if best_distance is None or distance < best_distance:
            best_frame, best_distance = frame, distance
    if best_frame is None:
        return None
    return frame_start(best_frame, rate), best_frame
