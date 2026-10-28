"""Exact time, derived frames, and cut points that never clip a word."""

from fractions import Fraction

import pytest

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.cutpoints import CutContext, cut_in, cut_out
from media_house.modules.editing_intelligence.domain.profile import PROFILES
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.speech import AudioIndex, unit_extent
from media_house.modules.editing_intelligence.domain.timing import (
    exact,
    frame_ceil,
    frame_floor,
    frame_round,
    sample_round,
    snap_into,
    to_fraction,
    to_rational,
)
from media_house.modules.editing_intelligence.domain.values import Precision
from tests.support.edit_fakes import Recording, S, talk, with_video

NTSC = Rational(30000, 1001)
PROFILE = PROFILES["generic"]


def context(recording: Recording) -> tuple[CutContext, AudioIndex]:
    index = AudioIndex(recording.audio)
    return CutContext(recording.evidence.source, index, recording.evidence.video, PROFILE), index


def test_a_float_second_becomes_the_nearest_exact_fraction() -> None:
    assert exact(0.1 + 0.2) == Fraction(3, 10)
    assert exact(1 / 3) == Fraction(1, 3)
    assert to_fraction(to_rational(Fraction(7, 3))) == Fraction(7, 3)


def test_frames_are_derived_exactly_on_ntsc_rates() -> None:
    one_second = Fraction(1001, 1000)  # exactly 30 frames at 30000/1001
    assert frame_floor(one_second, NTSC) == 30
    assert frame_ceil(one_second, NTSC) == 30
    assert frame_floor(one_second + Fraction(1, 1000), NTSC) == 30
    assert frame_ceil(one_second + Fraction(1, 1000), NTSC) == 31
    assert frame_round(Fraction(1, 60), Rational(30, 1)) == 1  # half a frame rounds up
    assert sample_round(Fraction(3, 2), 48_000) == 72_000


def test_snap_into_picks_a_frame_boundary_inside_the_window_or_nothing() -> None:
    rate = Rational(25, 1)
    snapped = snap_into(Fraction(98, 100), Fraction(101, 100), Fraction(99, 100), rate)
    assert snapped == (Fraction(1), 25)
    assert snap_into(Fraction(101, 100), Fraction(102, 100), Fraction(1), rate) is None


def test_a_range_gets_frames_only_for_a_constant_known_rate() -> None:
    constant = talk([S("one two three")], vfr=False).evidence.source
    variable = talk([S("one two three")], vfr=True).evidence.source
    unknown = talk([S("one two three")], fps=None, vfr=None).evidence.source

    rng = make_range(
        constant, Fraction(1), Fraction(2), Precision.WORD_ALIGNED, Precision.WORD_ALIGNED
    )
    assert (rng.start_frame, rng.end_frame, rng.frame_rate) == (25, 50, Rational(25, 1))
    assert (rng.start_sample, rng.end_sample) == (48_000, 96_000)
    for source in (variable, unknown):
        none = make_range(
            source, Fraction(1), Fraction(2), Precision.WORD_ALIGNED, Precision.WORD_ALIGNED
        )
        assert (none.frame_rate, none.start_frame, none.end_frame) == (None, None, None)


def test_a_range_cannot_end_before_it_starts() -> None:
    source = talk([S("one")]).evidence.source
    with pytest.raises(Exception, match="must not end before"):
        make_range(source, Fraction(2), Fraction(1), Precision.ESTIMATED, Precision.ESTIMATED)


def test_cut_points_sit_in_the_silence_and_never_inside_a_word() -> None:
    rec = talk([S("first sentence here", gap=0.3), S("second sentence here", gap=1.0)])
    ctx, index = context(rec)
    second = unit_extent(index, rec.content.units[1])
    assert second is not None

    start = cut_in(ctx, second)
    end = cut_out(ctx, unit_extent(index, rec.content.units[0]))  # type: ignore[arg-type]

    previous_word_end = rec.audio.words[2].end
    first_word_start = rec.audio.words[3].start
    assert previous_word_end < start.time.value < first_word_start
    assert rec.audio.words[2].end < end.time.value < rec.audio.words[3].start
    assert start.precision is Precision.FRAME_EXACT or start.precision is Precision.WORD_ALIGNED
    assert start.confidence is not None and start.confidence > 0.8


def test_the_cut_lands_on_a_frame_boundary_of_the_recording_when_the_window_allows() -> None:
    rec = talk([S("first sentence here", gap=0.3), S("second sentence here", gap=1.0)])
    ctx, index = context(rec)
    point = cut_in(ctx, unit_extent(index, rec.content.units[1]))  # type: ignore[arg-type]

    assert (to_fraction(point.time) * 25).denominator == 1


def test_words_that_nearly_touch_give_a_low_confidence_cut() -> None:
    rec = talk([S("first sentence here", gap=0.3), S("second sentence here", gap=0.0)])
    ctx, index = context(rec)

    point = cut_in(ctx, unit_extent(index, rec.content.units[1]))  # type: ignore[arg-type]

    assert point.confidence is not None and point.confidence < 0.6
    assert "ms of room" in point.reason


def test_segment_level_alignment_is_less_trusted_than_forced_alignment() -> None:
    forced = talk([S("one two three", gap=0.8)], forced=True)
    estimated = talk([S("one two three", gap=0.8)], forced=False)
    results = []
    for rec in (forced, estimated):
        ctx, index = context(rec)
        results.append(cut_in(ctx, unit_extent(index, rec.content.units[0])))  # type: ignore[arg-type]

    assert results[0].confidence is not None and results[1].confidence is not None
    assert results[0].confidence > results[1].confidence
    assert results[1].precision is Precision.SEGMENT_ALIGNED


def test_without_word_timing_the_cut_uses_the_sentence_time_and_says_so() -> None:
    rec = talk([S("one two three", gap=0.8)], with_audio=False)
    ctx = CutContext(rec.evidence.source, None, None, PROFILE)
    extent = unit_extent(None, rec.content.units[0])
    assert extent is not None

    point = cut_in(ctx, extent)

    assert point.precision is Precision.SENTENCE_SPAN
    assert point.confidence is not None and point.confidence <= 0.35
    assert "no word timing" in point.reason


def test_an_interpolated_sentence_time_is_estimated_not_exact() -> None:
    rec = talk([S("one two three", gap=0.8)], forced=False, with_audio=False)
    extent = unit_extent(None, rec.content.units[0])

    assert extent is not None and extent.precision is Precision.ESTIMATED


def test_a_shot_boundary_inside_the_gap_is_preferred_and_is_frame_exact() -> None:
    rec = with_video(
        talk([S("first sentence here", gap=0.3), S("second sentence here", gap=1.0)]),
        [(0.0, 2.15), (2.15, 8.0)],
    )
    ctx, index = context(rec)
    point = cut_in(ctx, unit_extent(index, rec.content.units[1]))  # type: ignore[arg-type]

    assert point.precision is Precision.FRAME_EXACT
    assert point.time.value == pytest.approx(2.15)
    assert any(a.kind == "speech_room" for a in point.alternatives)


def test_a_breath_before_the_sentence_is_kept() -> None:
    rec = talk(
        [S("first sentence here", gap=0.3), S("second sentence here", gap=1.0, event="breath")]
    )
    ctx, index = context(rec)
    point = cut_in(ctx, unit_extent(index, rec.content.units[1]))  # type: ignore[arg-type]
    breath_start = rec.audio.events[0].start

    assert point.time.value <= breath_start
    assert "breath" in point.reason
