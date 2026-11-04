"""Which picture a source time addresses, in exact arithmetic.

``source_time`` is exact seconds on the recording's common clock (zero = the container's start,
the clock an edit's source ranges use). On a known CONSTANT frame rate the addressed picture
is the one on screen at that time: ``k = floor((t - video_offset) * fps)``. The extractor is then
asked for the first picture at or after the middle of the previous frame interval, which lands
on ``k`` whatever rounding the container's time base adds. Without such a clock (variable or
unknown rate, no stored inspection) the first decoded picture at or after ``t`` is taken and the
position is marked inexact: seconds stay authoritative and no frame index is claimed.
"""

import math
from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamClock:
    """The facts of the picture stream that frame addressing needs (from stored inspection)."""

    frame_rate: Rational
    #: Seconds between the container start and the first picture (0 when they coincide).
    video_offset: Rational
    #: Stream length in seconds, if known.
    duration: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FramePosition:
    source_time: Rational
    #: Input seek handed to the extractor (accurate seek: first picture at or after it).
    seek_time: Rational
    #: Index of the addressed picture in presentation order, only on a known constant rate.
    frame_index: int | None
    #: Exact start of that picture on the common clock, only on a known constant rate.
    frame_time: Rational | None
    exact: bool
    detail: str


def _f(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def _r(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)


def frame_position(source_time: Rational, clock: StreamClock | None) -> FramePosition:
    if clock is None or clock.frame_rate.numerator == 0:
        return FramePosition(
            source_time=source_time,
            seek_time=source_time,
            frame_index=None,
            frame_time=None,
            exact=False,
            detail="no constant frame rate known: first decoded picture at or after the time",
        )
    fps = _f(clock.frame_rate)
    relative = max(Fraction(0), _f(source_time) - _f(clock.video_offset))
    index = math.floor(relative * fps)
    frame_time = _f(clock.video_offset) + index / fps
    seek = max(Fraction(0), frame_time - 1 / (2 * fps))
    return FramePosition(
        source_time=source_time,
        seek_time=_r(seek),
        frame_index=index,
        frame_time=_r(frame_time),
        exact=True,
        detail=f"frame {index} at {clock.frame_rate} fps",
    )


def decimal_seconds(value: Rational) -> str:
    """Exact seconds as a decimal string for a command line (nanoseconds, half-even)."""
    nanos = round(_f(value) * 1_000_000_000)
    return f"{nanos // 1_000_000_000}.{nanos % 1_000_000_000:09d}"
