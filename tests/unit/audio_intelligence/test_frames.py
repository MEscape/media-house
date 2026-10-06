"""Deterministic seconds <-> frame conversion."""

from fractions import Fraction

import pytest

from media_house.modules.audio_intelligence.domain.frames import (
    FrameRounding,
    frame_to_seconds,
    seconds_to_frame,
)
from media_house.shared.errors import InvariantViolation


@pytest.mark.parametrize(
    ("seconds", "fps", "rounding", "expected"),
    [
        (12.34, 30, FrameRounding.FLOOR, 370),
        (12.34, 30, FrameRounding.ROUND, 370),
        (12.34, 30, FrameRounding.CEIL, 371),
        (12.34, 25, FrameRounding.FLOOR, 308),
        (12.34, 25, FrameRounding.ROUND, 309),  # 308.5 -> halves round up
        (12.34, 60, FrameRounding.FLOOR, 740),
        (0.0, 24, FrameRounding.FLOOR, 0),
        (1.0, 24, FrameRounding.CEIL, 24),
        (0.5 / 30, 30, FrameRounding.ROUND, 1),  # halves round up
    ],
)
def test_rounding_policies(
    seconds: float, fps: int, rounding: FrameRounding, expected: int
) -> None:
    assert seconds_to_frame(seconds, fps, rounding) == expected


def test_float_noise_does_not_move_a_boundary() -> None:
    assert 0.1 + 0.2 != 0.3
    assert seconds_to_frame(0.1 + 0.2, 10, FrameRounding.FLOOR) == 3
    assert seconds_to_frame(0.1 * 3, 10, FrameRounding.CEIL) == 3


def test_ntsc_rates_are_exact_when_given_as_fractions() -> None:
    ntsc = Fraction(30000, 1001)
    assert seconds_to_frame(1001 / 30, ntsc, FrameRounding.FLOOR) == 1000
    assert frame_to_seconds(30000, ntsc) == pytest.approx(1001.0)


def test_frame_to_seconds_round_trips_the_frame_start() -> None:
    for fps in (24, 25, 30, 50, 60):
        for frame in (0, 1, 17, 1234):
            assert seconds_to_frame(frame_to_seconds(frame, fps), fps) == frame


@pytest.mark.parametrize("fps", [0, -30, Fraction(0)])
def test_fps_must_be_positive(fps: float) -> None:
    with pytest.raises(InvariantViolation):
        seconds_to_frame(1.0, fps)


def test_timestamp_must_be_finite() -> None:
    with pytest.raises(InvariantViolation):
        seconds_to_frame(float("nan"), 30)
