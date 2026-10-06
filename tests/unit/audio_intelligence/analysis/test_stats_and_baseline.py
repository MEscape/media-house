"""Robust statistics and the speaker baseline."""

import math

import pytest

from media_house.modules.audio_intelligence.domain.analysis import stats
from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticTrack
from media_house.modules.audio_intelligence.domain.analysis.baseline import estimate_baseline
from media_house.modules.audio_intelligence.domain.analysis.config import AnalysisConfig
from tests.support.analysis_fakes import NAN, make_track

CONFIG = AnalysisConfig()


# --- statistics --------------------------------------------------------------------------------
def test_median_and_percentile_skip_unknown_values() -> None:
    assert stats.median([3.0, NAN, 1.0, 2.0]) == 2.0
    assert stats.median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert stats.median([NAN]) is None
    assert stats.percentile([0.0, 10.0], 50) == 5.0
    assert stats.percentile([], 50) is None


def test_robust_sigma_ignores_outliers_where_std_does_not() -> None:
    clean = [10.0, 11.0, 9.0, 10.5, 9.5, 10.2, 9.8]
    with_outlier = [*clean, 1000.0]

    assert stats.robust_sigma(with_outlier) == pytest.approx(stats.robust_sigma(clean), abs=0.5)
    assert (stats.std(with_outlier) or 0) > 100


def test_decibels_are_averaged_in_the_power_domain() -> None:
    assert stats.power_mean_db([-10.0, -10.0]) == pytest.approx(-10.0)
    assert stats.power_mean_db([-10.0, -30.0]) == pytest.approx(-12.97, abs=0.01)  # not -20
    assert stats.power_mean_db([]) is None


def test_saturate_maps_magnitudes_onto_unit_interval() -> None:
    assert stats.saturate(0.0, 5.0) == 0.0
    assert stats.saturate(2.5, 5.0) == 0.5
    assert stats.saturate(50.0, 5.0) == 1.0
    assert stats.saturate(-1.0, 5.0) == 0.0
    assert stats.saturate(None, 5.0) is None


def test_weighted_mean_renormalises_over_available_evidence() -> None:
    weights = {"a": 1.0, "b": 3.0, "c": 6.0}

    assert stats.weighted_mean({"a": 1.0, "b": 0.0, "c": None}, weights) == pytest.approx(0.25)
    assert stats.weighted_mean({"a": None, "b": None}, weights) is None  # unknown, never 0


def test_slope_of_a_line() -> None:
    assert stats.slope([0.0, 1.0, 2.0], [1.0, 3.0, 5.0]) == pytest.approx(2.0)
    assert stats.slope([0.0], [1.0]) is None


def test_swings_find_rises_and_falls_and_ignore_wiggles() -> None:
    series = [0.0, 0.5, 0.0, 0.4, 5.0, 10.0, 9.8, 5.0, 0.0]

    found = stats.swings(series, min_delta=4.0)

    assert [(a, b) for a, b, _ in found] == [(0, 5), (5, 8)] or [d > 0 for _, _, d in found] == [
        True,
        False,
    ]
    assert found[0][2] > 0
    assert found[-1][2] < 0
    assert stats.swings([0.0, 0.5, 0.2, 0.6], min_delta=4.0) == []


def test_moving_median_keeps_unknown_gaps_unknown() -> None:
    smoothed = stats.moving_median([1.0, 100.0, 1.0, NAN, NAN, NAN], width=3)

    assert smoothed[1] == 1.0  # the spike is removed
    assert math.isnan(smoothed[4])


# --- baseline ----------------------------------------------------------------------------------
def contour(base_hz: float) -> object:
    """Same relative melody (+-3 semitones) around any base pitch."""
    return lambda t: base_hz * 2 ** (3 * math.sin(2 * math.pi * 0.8 * t) / 12)


def track_for(base_hz: float, seconds: float = 6.0, db: float = -25.0) -> AcousticTrack:
    return make_track(seconds=seconds, f0=contour(base_hz), db=lambda _t: db)  # type: ignore[arg-type]


def test_deep_and_high_voices_share_one_relative_scale() -> None:
    deep = estimate_baseline(track_for(110.0), CONFIG)
    high = estimate_baseline(track_for(220.0), CONFIG)

    assert deep.pitch_median_hz == pytest.approx(110.0, rel=0.03)
    assert high.pitch_median_hz == pytest.approx(220.0, rel=0.03)
    assert deep.pitch_st is not None
    assert high.pitch_st is not None
    assert deep.pitch_st.sigma == pytest.approx(high.pitch_st.sigma, abs=0.05)
    # +12 semitones is "one octave above normal" for both speakers.
    assert deep.pitch_relative_st(220.0) == pytest.approx(12.0, abs=0.7)
    assert high.pitch_relative_st(440.0) == pytest.approx(12.0, abs=0.7)


def test_baseline_is_robust_against_octave_errors_and_shouts() -> None:
    clean = estimate_baseline(track_for(150.0), CONFIG)
    spiked = make_track(
        seconds=6.0,
        f0=lambda t: 1500.0 if 2.0 <= t < 2.1 else 150.0 * 2 ** (3 * math.sin(6 * t) / 12),
        db=lambda _t: -25.0,
    )

    robust = estimate_baseline(spiked, CONFIG)

    assert robust.pitch_median_hz == pytest.approx(clean.pitch_median_hz, rel=0.03)


def test_percentile_and_z_follow_the_speakers_own_distribution() -> None:
    baseline = estimate_baseline(track_for(150.0), CONFIG)

    low, mid, high = (baseline.pitch_percentile(hz) for hz in (120.0, 150.0, 190.0))

    assert low is not None
    assert mid is not None
    assert high is not None
    assert low < mid < high
    assert 0.3 < mid < 0.7
    assert baseline.pitch_percentile(10.0) == 0.0
    assert baseline.pitch_percentile(5000.0) == 1.0
    assert (baseline.pitch_z(190.0) or 0) > 0 > (baseline.pitch_z(120.0) or 0)


def test_unvoiced_audio_has_no_pitch_baseline_but_keeps_energy() -> None:
    baseline = estimate_baseline(make_track(seconds=4.0, db=lambda _t: -30.0), CONFIG)

    assert baseline.pitch_median_hz is None
    assert baseline.pitch_st is None
    assert baseline.pitch_percentile(150.0) is None
    assert baseline.energy_db is not None
    assert baseline.energy_db.median == pytest.approx(-30.0)


def test_too_little_voiced_speech_gives_no_pitch_baseline() -> None:
    few = make_track(
        seconds=4.0,
        f0=lambda t: 150.0 if t < 0.3 else NAN,  # 15 voiced frames < 30 required
        db=lambda _t: -25.0,
    )

    assert estimate_baseline(few, CONFIG).pitch_median_hz is None


def test_silence_is_excluded_from_the_energy_baseline() -> None:
    mixed = make_track(seconds=6.0, db=lambda t: -20.0 if t < 3.0 else -90.0)

    baseline = estimate_baseline(mixed, CONFIG)

    assert baseline.energy_db is not None
    assert baseline.energy_db.median == pytest.approx(-20.0)
    assert baseline.speech_frames == pytest.approx(150, abs=3)
