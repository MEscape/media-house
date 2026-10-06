"""Per-word evidence, speaking rate, derived scores and editing signals."""

import math
from typing import Any

import pytest

from media_house.modules.audio_intelligence.domain.analysis.config import (
    AudioIntelligenceConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.scoring import (
    BROLL,
    CUT,
    EDITING_KINDS,
    MOMENT,
    MUSIC_BREAK,
    MUSIC_DUCK,
    ZOOM_EMPHASIS,
    EditingSignal,
)
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from tests.support.analysis_fakes import (
    NAN,
    Delivery,
    Word,
    make_track,
    speech_track,
    timeline,
)


def evenly(
    count: int,
    step: float = 0.4,
    length: float = 0.3,
    start: float = 0.5,
) -> list[Word]:
    return [(f"w{i}", start + i * step, start + i * step + length) for i in range(count)]


# --- pitch and energy of a word ----------------------------------------------------------------
def test_word_pitch_is_measured_and_expressed_relative_to_the_speaker() -> None:
    words = evenly(8)
    tl = timeline([words], delivery={4: Delivery(f0=300.0, db=-15.0)})

    word = tl.words[4]

    assert word.pitch is not None
    assert word.pitch.median_hz == pytest.approx(300.0)
    assert word.pitch.range_st == pytest.approx(0.0, abs=0.01)
    assert word.pitch.voiced_ratio == pytest.approx(1.0, abs=0.15)
    assert word.pitch.relative_st == pytest.approx(12 * math.log2(300 / 150), abs=0.5)
    assert word.pitch.z_score is None  # a perfectly flat speaker has no spread to compare to
    assert (word.pitch.percentile or 0) > 0.9
    assert word.energy is not None
    assert word.energy.relative_db == pytest.approx(10.0, abs=0.5)
    assert (word.pitch.local_deviation_st or 0) == pytest.approx(12.0, abs=0.7)
    assert (word.energy.local_deviation_db or 0) == pytest.approx(10.0, abs=0.7)


def test_unvoiced_words_have_unknown_pitch_not_zero() -> None:
    tl = timeline([evenly(8)], delivery={3: Delivery(f0=None, db=-30.0)})

    word = tl.words[3]

    assert word.pitch is not None
    assert word.pitch.median_hz is None
    assert word.pitch.relative_st is None
    assert word.pitch.voiced_ratio == 0.0
    assert word.pitch_change is None  # unknown, not "no change"
    assert word.energy is not None  # energy is still measured


def test_word_energy_averages_in_the_power_domain() -> None:
    words = evenly(6)
    flicker = make_track(
        seconds=4.0,
        f0=lambda t: 150.0 if any(s <= t < e for _, s, e in words) else NAN,
        db=lambda t: (
            (-10.0 if int(t / 0.02) % 2 == 0 else -30.0)
            if any(s <= t < e for _, s, e in words)
            else -90.0
        ),
    )

    word = timeline([words], flicker).words[2]

    assert word.energy is not None
    assert word.energy.mean_db == pytest.approx(-13.0, abs=0.5)  # not the arithmetic -20
    assert word.energy.peak_db == pytest.approx(-10.0)
    assert (word.energy.dynamic_range_db or 0) == pytest.approx(20.0, abs=0.5)


def test_words_without_frames_still_exist_with_unknown_acoustics() -> None:
    words = evenly(4)
    short = make_track(seconds=1.0, f0=lambda _t: 150.0, db=lambda _t: -25.0)  # ends mid-speech

    tl = timeline([words], short, duration=4.0)

    last = tl.words[-1]
    assert last.acoustics is not None
    assert last.acoustics.first_frame is None
    assert last.pitch is None
    assert last.energy is None
    assert last.emphasis_score is not None or last.emphasis_score is None  # computed, no crash


def test_word_frame_references_match_the_word_times() -> None:
    tl = timeline([evenly(5)])
    word = tl.words[2]

    assert word.acoustics is not None
    assert word.acoustics.first_frame is not None
    first = tl.sample(word.acoustics.first_frame)
    assert word.start - tl.track.hop <= first.start <= word.start + tl.track.hop


# --- speaking rate -----------------------------------------------------------------------------
def test_steady_speech_has_a_stable_rate_and_no_acceleration() -> None:
    tl = timeline([evenly(20, step=0.25, length=0.2)])

    middle = tl.words[10].rate

    assert middle is not None
    assert middle.words_per_second == pytest.approx(4.0, abs=0.7)
    assert middle.words_per_minute == pytest.approx(middle.words_per_second * 60)
    assert middle.relative == pytest.approx(1.0, abs=0.2)
    assert middle.change is not None
    assert abs(middle.change) < 0.2


def test_speeding_up_and_slowing_down_are_signed() -> None:
    slow = [(f"s{i}", 0.5 + i * 0.6, 0.5 + i * 0.6 + 0.4) for i in range(8)]
    fast_start = slow[-1][2] + 0.2
    fast = [(f"f{i}", fast_start + i * 0.2, fast_start + i * 0.2 + 0.15) for i in range(16)]

    speeding = timeline([[*slow, *fast]])
    boundary = next(w for w in speeding.words if w.raw_word == "s7")
    slowing = timeline([[*fast_as_first(fast_start), *slow_after()]])

    assert boundary.rate is not None
    assert (boundary.rate.change or 0) > 0.3
    assert slowing.words[-9].rate is not None


def fast_as_first(start: float) -> list[Word]:
    return [(f"f{i}", start + i * 0.2, start + i * 0.2 + 0.15) for i in range(16)]


def slow_after() -> list[Word]:
    base = 0.5 + 16 * 0.2 + 0.2
    return [(f"s{i}", base + i * 0.6, base + i * 0.6 + 0.4) for i in range(8)]


def test_long_pauses_do_not_dilute_the_speaking_rate() -> None:
    first = [(f"a{i}", 0.5 + i * 0.25, 0.5 + i * 0.25 + 0.2) for i in range(12)]
    resume = first[-1][2] + 2.0
    second = [(f"b{i}", resume + i * 0.25, resume + i * 0.25 + 0.2) for i in range(12)]

    tl = timeline([first, second])

    just_before_pause = tl.words[11].rate
    assert just_before_pause is not None
    assert just_before_pause.words_per_second == pytest.approx(4.0, abs=1.0)  # not ~2


# --- scores ------------------------------------------------------------------------------------
def emphasised_timeline(**kwargs: Any) -> AudioIntelligenceTimeline:
    words = evenly(9)

    def f0(t: float) -> float:
        base = 150.0
        if 0.5 + 4 * 0.4 <= t < 0.5 + 4 * 0.4 + 0.3:  # rising pitch inside word 4
            return base * 2 ** (10 * (t - (0.5 + 1.6)) / 0.3 / 12) * 1.3
        return base

    track = make_track(
        seconds=5.0,
        f0=lambda t: f0(t) if any(s <= t < e for _, s, e in words) else NAN,
        db=lambda t: (
            (-12.0 if 2.1 <= t < 2.4 else -26.0) if any(s <= t < e for _, s, e in words) else -90.0
        ),
    )
    return timeline([words], track, **kwargs)


def test_emphasised_word_outscores_its_neighbours_with_visible_evidence() -> None:
    tl = emphasised_timeline()

    scores = [w.emphasis_score or 0.0 for w in tl.words]

    assert scores[4] == max(scores)
    assert scores[4] > 1.5 * sorted(scores)[len(scores) // 2]
    explanation = tl.explain_word(tl.words[4])
    assert set(explanation["emphasis"]) >= {"score", "energy_level", "pitch_level"}
    assert all(0.0 <= v <= 1.0 for part in explanation.values() for v in part.values())
    assert (tl.words[4].moment_score or 0) == max(w.moment_score or 0 for w in tl.words)


def test_all_scores_stay_within_their_documented_range_and_are_deterministic() -> None:
    first, again = emphasised_timeline(), emphasised_timeline()

    for word in first.words:
        for score in (
            word.emphasis_score,
            word.expressiveness_score,
            word.arousal_score,
            word.local_contrast,
            word.moment_score,
        ):
            assert score is None or 0.0 <= score <= 1.0
    for signal in first.editing_signals:
        assert 0.0 <= signal.score <= 1.0
        assert all(0.0 <= v <= 1.0 for v in signal.contributors.values())
        assert signal.kind in EDITING_KINDS
    assert first.editing_signals == again.editing_signals
    assert [w.emphasis_score for w in first.words] == [w.emphasis_score for w in again.words]


def test_missing_evidence_is_left_out_of_a_score_not_counted_as_zero() -> None:
    tl = timeline([evenly(8)], delivery={3: Delivery(f0=None, db=-20.0)})

    emphasis = tl.words[3].signals.emphasis

    assert emphasis is not None
    assert "pitch_change" not in emphasis.contributors  # unvoiced: unknown
    assert "pitch_level" not in emphasis.contributors
    assert "energy_level" in emphasis.contributors


def test_scoring_weights_are_configurable_and_change_the_explanation() -> None:
    only_energy = AudioIntelligenceConfig(
        scoring=ScoringConfig(emphasis_weights={"energy_level": 1.0}),
    )
    words = evenly(8)

    tl = timeline([words], config=only_energy, delivery={4: Delivery(f0=300.0, db=-15.0)})

    emphasis = tl.words[4].signals.emphasis
    assert emphasis is not None
    assert set(emphasis.contributors) == {"energy_level"}
    assert emphasis.score == emphasis.contributors["energy_level"]


def test_flat_delivery_is_monotone_and_varied_delivery_is_expressive() -> None:
    flat_words = evenly(10)
    varied_start = flat_words[-1][2] + 1.0
    varied_words = [
        (f"v{i}", varied_start + i * 0.4, varied_start + i * 0.4 + 0.3) for i in range(10)
    ]
    delivery = {
        10 + i: Delivery(f0=100.0 if i % 2 == 0 else 220.0, db=-35.0 if i % 3 == 0 else -15.0)
        for i in range(10)
    }

    tl = timeline([flat_words, varied_words], delivery=delivery)
    flat, varied = tl.segments

    assert flat.acoustics.monotony is not None
    assert varied.acoustics.monotony is not None
    assert flat.acoustics.monotony.score > varied.acoustics.monotony.score
    assert (flat.acoustics.expressiveness.score if flat.acoustics.expressiveness else 0) < (
        varied.acoustics.expressiveness.score if varied.acoustics.expressiveness else 0
    )
    broll = {s.anchor_id: s for s in tl.editing_signals if s.kind == BROLL}
    assert broll[0].score > broll[1].score
    assert set(broll[0].contributors) >= {"monotony", "uninterrupted_speech"}


def test_segment_values_use_meaningful_aggregation() -> None:
    tl = timeline([evenly(10)])
    segment = tl.segments[0].acoustics

    assert segment.pitch_median_hz == pytest.approx(150.0, rel=0.02)
    assert segment.energy_db == pytest.approx(-25.0, abs=0.5)
    assert segment.speech_rate_wps is not None
    assert segment.speech_rate_wps > 0
    assert segment.emphasis_max is not None
    assert segment.pause_before is not None  # the leading silence
    assert segment.pause_after is not None


# --- editing signals ---------------------------------------------------------------------------
def signals(tl: AudioIntelligenceTimeline, kind: str) -> list[EditingSignal]:
    return [s for s in tl.editing_signals if s.kind == kind]


def test_every_editing_signal_kind_is_produced_with_contributors() -> None:
    tl = emphasised_timeline()

    assert {s.kind for s in tl.editing_signals} == set(EDITING_KINDS)
    assert signals(tl, MOMENT)
    assert all(s.contributors for s in tl.editing_signals)
    assert [s.start for s in tl.editing_signals] == sorted(s.start for s in tl.editing_signals)


def test_a_pause_after_an_important_word_is_a_stronger_music_break() -> None:
    def scenario(loud: bool) -> EditingSignal:
        words = [[(f"w{i}", 0.5 + i * 0.4, 0.8 + i * 0.4) for i in range(6)]]
        resume = words[0][-1][2] + 1.5
        words.append([(f"x{i}", resume + i * 0.4, resume + 0.3 + i * 0.4) for i in range(3)])
        delivery = {5: Delivery(f0=320.0, db=-12.0)} if loud else {}
        tl = timeline(words, delivery=delivery)
        return next(s for s in signals(tl, MUSIC_BREAK) if s.anchor_id == _pause_after_word(tl, 5))

    assert scenario(loud=True).score > scenario(loud=False).score
    assert set(scenario(loud=True).contributors) >= {"pause_length", "preceding_moment"}


def _pause_after_word(tl: AudioIntelligenceTimeline, word: int) -> int:
    return next(n for n, p in enumerate(tl.pauses) if p.before_word == word)


def test_cut_evidence_is_strongest_at_a_finished_sentence_before_a_long_pause() -> None:
    def scenario(last: str) -> EditingSignal:
        first = [("one", 0.5, 0.8), ("two", 1.0, 1.3), (last, 1.5, 1.9)]
        second = [("next", 3.2, 3.6), ("part", 3.8, 4.1)]
        tl = timeline([first, second])
        return next(s for s in signals(tl, CUT) if tl.pauses[s.anchor_id].before_word == 2)

    finished, unfinished = scenario("done."), scenario("and")

    assert finished.score > unfinished.score
    assert finished.contributors["segment_boundary"] == 1.0
    assert finished.contributors["sentence_completion"] == 1.0
    assert unfinished.contributors["sentence_completion"] == 0.0


def test_duck_and_zoom_signals_follow_the_emphasis_evidence() -> None:
    tl = emphasised_timeline()

    duck = {s.anchor_id: s.score for s in signals(tl, MUSIC_DUCK)}
    zoom = {s.anchor_id: s.score for s in signals(tl, ZOOM_EMPHASIS)}

    assert duck[4] == max(duck.values())
    assert zoom[4] == max(zoom.values())


def test_silence_only_audio_gives_an_empty_but_valid_timeline() -> None:
    quiet = make_track(seconds=3.0)
    tl = timeline([], quiet, duration=3.0)

    assert tl.words == ()
    assert tl.editing_signals == ()
    assert tl.baseline.pitch_median_hz is None
    assert tl.events_between(0.0, 3.0)
    assert tl.moment_at(1.0) is None


def test_speech_track_helper_marks_only_words_as_voiced() -> None:
    track = speech_track([("a", 0.5, 0.9)], seconds=2.0)

    assert track.is_voiced(track.index_at(0.7) or 0, 0.5)
    assert not track.is_voiced(track.index_at(1.5) or 0, 0.5)
