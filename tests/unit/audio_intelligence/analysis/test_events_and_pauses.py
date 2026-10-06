"""Frame-level events (pitch, energy, silence) and word-gap pauses."""

import math

import pytest

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    ENERGY_DROP,
    ENERGY_SPIKE,
    PITCH_FALL,
    PITCH_RISE,
    SILENCE,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import estimate_baseline
from media_house.modules.audio_intelligence.domain.analysis.config import AnalysisConfig
from media_house.modules.audio_intelligence.domain.analysis.detection import detect_acoustic_events
from media_house.modules.audio_intelligence.domain.analysis.pauses import LONG, MEDIUM, SHORT
from tests.support.analysis_fakes import NAN, make_track, timeline

CONFIG = AnalysisConfig()


def events_of(track: AcousticTrack, kind: str) -> list[AudioEvent]:
    baseline = estimate_baseline(track, CONFIG)
    return [e for e in detect_acoustic_events(track, baseline, CONFIG) if e.kind == kind]


def semitone_shift(base: float, st: float) -> float:
    return base * 2 ** (st / 12)


# --- pitch -------------------------------------------------------------------------------------
def test_a_sudden_rise_is_a_pitch_rise_event() -> None:
    def f0(t: float) -> float:
        if t < 2.0:
            return 120.0
        if t < 2.3:  # +9 semitones in 0.3 s
            return semitone_shift(120.0, 9 * (t - 2.0) / 0.3)
        return semitone_shift(120.0, 9)

    found = events_of(make_track(seconds=4.0, f0=f0, db=lambda _t: -25.0), PITCH_RISE)

    assert len(found) == 1
    event = found[0]
    assert 1.9 <= event.start <= 2.3
    assert event.strength >= 0.9
    assert event.details["delta_st"] > 5
    assert event.source == "pitch"
    assert event.confidence == pytest.approx(0.9)


def test_a_sudden_fall_is_a_pitch_fall_event() -> None:
    def f0(t: float) -> float:
        return 200.0 if t < 2.0 else semitone_shift(200.0, -7 * min(1.0, (t - 2.0) / 0.25))

    found = events_of(make_track(seconds=4.0, f0=f0, db=lambda _t: -25.0), PITCH_FALL)

    assert len(found) == 1
    assert found[0].details["delta_st"] < -4


def test_a_slow_glide_and_small_wiggles_are_not_events() -> None:
    glide = make_track(
        seconds=6.0,
        f0=lambda t: semitone_shift(120.0, 9 * t / 6.0),  # +9 st over 6 s
        db=lambda _t: -25.0,
    )
    wiggle = make_track(
        seconds=4.0,
        f0=lambda t: semitone_shift(150.0, 0.5 * math.sin(20 * t)),
        db=lambda _t: -25.0,
    )

    assert events_of(glide, PITCH_RISE) == []
    assert events_of(wiggle, PITCH_RISE) == []
    assert events_of(wiggle, PITCH_FALL) == []


def test_stronger_changes_get_higher_strength() -> None:
    def rise(st: float) -> list[AudioEvent]:
        return events_of(
            make_track(
                seconds=4.0,
                f0=lambda t: semitone_shift(150.0, st if t > 2.0 else 0.0),
                db=lambda _t: -25.0,
            ),
            PITCH_RISE,
        )

    small, large = rise(3.0), rise(7.0)

    assert small
    assert large
    assert small[0].strength < large[0].strength <= 1.0


def test_a_gap_in_voicing_never_makes_a_pitch_event() -> None:
    track = make_track(
        seconds=4.0,
        f0=lambda t: NAN if 1.0 <= t < 1.2 else (100.0 if t < 1.0 else 200.0),
        db=lambda _t: -25.0,
    )

    assert events_of(track, PITCH_RISE) == []  # an octave jump across unvoiced frames: unknown


def test_without_a_pitch_baseline_there_are_no_pitch_events() -> None:
    track = make_track(seconds=3.0, db=lambda _t: -25.0)

    assert events_of(track, PITCH_RISE) == []


# --- energy and silence ------------------------------------------------------------------------
def test_energy_spike_and_drop_need_a_sudden_large_change() -> None:
    track = make_track(
        seconds=8.0,
        f0=lambda _t: 150.0,
        db=lambda t: -10.0 if 4.0 <= t < 5.0 else -30.0,
    )

    spikes, drops = events_of(track, ENERGY_SPIKE), events_of(track, ENERGY_DROP)

    assert len(spikes) == 1
    assert len(drops) == 1
    assert spikes[0].start == pytest.approx(4.0, abs=0.15)
    assert drops[0].start == pytest.approx(5.0, abs=0.15)
    assert spikes[0].strength == pytest.approx(1.0)  # 20 dB > full scale
    assert spikes[0].details["delta_db"] == pytest.approx(20.0, abs=1.0)


def test_syllable_level_wobble_is_not_an_energy_event() -> None:
    track = make_track(
        seconds=6.0,
        f0=lambda _t: 150.0,
        db=lambda t: -25.0 + 3.0 * math.sin(2 * math.pi * 4 * t),
    )

    assert events_of(track, ENERGY_SPIKE) == []
    assert events_of(track, ENERGY_DROP) == []


def test_silence_events_have_raw_spans_and_a_minimum_length() -> None:
    track = make_track(
        seconds=6.0,
        f0=lambda _t: 150.0,
        db=lambda t: -90.0 if 2.0 <= t < 2.8 or 4.0 <= t < 4.1 else -25.0,
    )

    found = events_of(track, SILENCE)

    assert len(found) == 1  # the 0.1 s dip is shorter than min_silence
    assert found[0].start == pytest.approx(2.0)
    assert found[0].end == pytest.approx(2.8)
    assert 0 < found[0].strength < 1


def test_events_are_sorted_by_time() -> None:
    track = make_track(
        seconds=6.0,
        f0=lambda t: 150.0 if t < 3 else 300.0,
        db=lambda t: -90.0 if 1.0 <= t < 1.6 else -25.0,
    )
    baseline = estimate_baseline(track, CONFIG)

    starts = [e.start for e in detect_acoustic_events(track, baseline, CONFIG)]

    assert starts == sorted(starts)


# --- pauses ------------------------------------------------------------------------------------
def test_pauses_keep_raw_duration_and_get_a_kind() -> None:
    words = [
        [("one", 0.5, 0.8), ("two", 1.0, 1.3), ("three", 1.8, 2.1)],
        [("four", 3.4, 3.8), ("five", 3.85, 4.2)],
    ]

    pauses = timeline(words).pauses

    spans = [(p.start, p.end, p.kind) for p in pauses]
    assert (0.0, 0.5, MEDIUM) in spans  # leading silence
    assert (0.8, 1.0, SHORT) in spans
    assert (1.3, 1.8, MEDIUM) in spans
    assert (2.1, 3.4, LONG) in spans
    assert all(p.start != 3.8 for p in pauses)  # 0.05 s gap is below min_pause
    long_pause = next(p for p in pauses if p.kind == LONG)
    assert long_pause.duration == pytest.approx(1.3)  # raw, not bucketed
    assert (long_pause.before_word, long_pause.after_word) == (2, 3)
    assert (long_pause.before_segment, long_pause.after_segment) == (0, 1)
    assert long_pause.at_segment_boundary
    assert not next(p for p in pauses if p.kind == SHORT).at_segment_boundary


def test_trailing_silence_is_a_pause_until_the_media_ends() -> None:
    pauses = timeline([[("end", 0.5, 0.9)]], duration=3.0).pauses

    last = pauses[-1]
    assert (last.start, last.end) == (0.9, 3.0)
    assert last.after_word is None


def test_pause_silence_ratio_confirms_real_silence() -> None:
    words = [[("a", 0.5, 0.9), ("b", 2.0, 2.4)]]
    quiet = make_track(
        seconds=3.0,
        f0=lambda t: 150.0 if 0.5 <= t < 0.9 or 2.0 <= t < 2.4 else NAN,
        db=lambda t: -25.0 if 0.5 <= t < 0.9 or 2.0 <= t < 2.4 else -90.0,
    )
    noisy = make_track(
        seconds=3.0,
        f0=lambda t: 150.0 if 0.5 <= t < 0.9 or 2.0 <= t < 2.4 else NAN,
        db=lambda t: -25.0 if 0.5 <= t < 0.9 or 2.0 <= t < 2.4 or 1.0 <= t < 1.8 else -90.0,
    )

    clean = next(p for p in timeline(words, quiet).pauses if p.before_word == 0)
    filled = next(p for p in timeline(words, noisy).pauses if p.before_word == 0)

    assert clean.silence_ratio == pytest.approx(1.0)
    assert filled.silence_ratio is not None
    assert filled.silence_ratio < 0.4  # something other than silence fills the gap
