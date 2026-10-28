"""Energy rhythm: a steady pulse is found, speech and noise are not mistaken for one."""

import math
import random

import pytest

from media_house.modules.audio_intelligence.application.contracts import (
    RhythmConfig,
    RhythmWindow,
    estimate_rhythm,
)
from media_house.shared.errors import InvariantViolation
from tests.support.analysis_fakes import make_track

QUIET = -60.0
LOUD = -12.0


def clicks(bpm: float, seconds: float = 30.0, *, width: float = 0.06, speech: bool = False):  # type: ignore[no-untyped-def]
    period = 60.0 / bpm

    def level(t: float) -> float:
        return LOUD if (t % period) < width else QUIET

    return make_track(seconds=seconds, db=level, speech=(lambda _t: speech))


def test_a_steady_pulse_gives_its_tempo_and_a_regular_beat_grid() -> None:
    windows = estimate_rhythm(clicks(120.0))

    assert windows
    for window in windows:
        assert window.tempo_bpm == pytest.approx(120.0, abs=3.0)
        assert window.strength > 0.5
        gaps = [b - a for a, b in zip(window.beats, window.beats[1:], strict=False)]
        assert all(g == pytest.approx(0.5, abs=0.03) for g in gaps)
        assert window.start <= window.beats[0] < window.end


def test_beats_land_on_the_pulses_not_between_them() -> None:
    (window, *_) = estimate_rhythm(clicks(100.0))

    period = 0.6
    for beat in window.beats[:-1]:
        offset = beat % period
        assert min(offset, period - offset) < 0.08


def test_a_slower_tempo_is_reported_as_slower() -> None:
    slow = estimate_rhythm(clicks(75.0))
    fast = estimate_rhythm(clicks(150.0))

    assert slow and fast
    assert slow[0].tempo_bpm == pytest.approx(75.0, abs=3.0)
    assert fast[0].tempo_bpm == pytest.approx(150.0, abs=4.0)


def test_speech_is_never_read_as_a_tempo() -> None:
    assert estimate_rhythm(clicks(120.0, speech=True)) == ()


def test_irregular_noise_has_no_rhythm() -> None:
    rng = random.Random(7)  # noqa: S311  (deterministic test noise, not security)
    levels = [LOUD if rng.random() < 0.15 else QUIET for _ in range(2000)]
    track = make_track(
        seconds=40.0, db=lambda t: levels[int(t / 0.02) % 2000], speech=lambda _t: False
    )

    assert estimate_rhythm(track) == ()


def test_silence_has_no_rhythm_and_a_short_track_is_not_an_error() -> None:
    assert (
        estimate_rhythm(make_track(seconds=30.0, db=lambda _t: QUIET, speech=lambda _t: False))
        == ()
    )
    assert estimate_rhythm(make_track(seconds=2.0, db=lambda _t: LOUD)) == ()


def test_the_configuration_is_validated_and_changes_the_result() -> None:
    with pytest.raises(InvariantViolation):
        RhythmConfig(min_bpm=200, max_bpm=100)
    with pytest.raises(InvariantViolation):
        RhythmConfig(window=5, step=10)
    strict = RhythmConfig(min_strength=0.999)

    assert estimate_rhythm(clicks(120.0), strict) == ()


def test_the_estimate_is_deterministic_and_finite() -> None:
    first, second = estimate_rhythm(clicks(128.0)), estimate_rhythm(clicks(128.0))

    assert first == second
    assert all(math.isfinite(w.tempo_bpm) for w in first)
    assert RhythmWindow in {type(w) for w in first}
