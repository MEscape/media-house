"""What is measured on the preview, against what the timeline says is deliberate."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.quality_control.domain import facts as f
from media_house.modules.quality_control.domain.checks_render import run_render_checks
from media_house.modules.quality_control.domain.measurements import (
    ClipLoudness,
    DecodeFacts,
    LoudnessFacts,
)
from media_house.modules.quality_control.domain.values import Basis, Severity
from tests.unit.quality_control.fixtures import (
    GENERIC,
    PA,
    clean,
    codes,
    frame,
    measurements,
    one,
    span,
    streams,
    with_clip,
)


def run(m, facts: f.TimelineFacts | None = None, gaps=None, profile=GENERIC):  # type: ignore[no-untyped-def]
    return run_render_checks(facts or clean(), profile, m, gaps or [])


def test_a_preview_that_matches_the_timeline_has_nothing_wrong() -> None:
    assert run(measurements()) == []


# --- the file --------------------------------------------------------------------------------


def test_a_preview_that_does_not_decode_is_critical() -> None:
    issue = one(
        run(measurements(decode=DecodeFacts(False, ("error while decoding MB 3 4",)))),
        "render.decode_errors",
    )

    assert issue.severity is Severity.CRITICAL and issue.blocks_export
    assert "error while decoding" in issue.evidence[0]


def test_wrong_size_rate_and_length_are_found() -> None:
    m = measurements(
        streams=streams(width=1280, height=720, fps=Fraction(30), duration=Fraction(10))
    )

    assert {"render.size_mismatch", "render.rate_mismatch", "render.duration_mismatch"} <= set(
        codes(run(m))
    )


def test_a_small_length_difference_is_tolerated() -> None:
    assert run(measurements(streams=streams(duration=Fraction(8) + Fraction(1, 25)))) == []


def test_sound_that_the_timeline_has_and_the_preview_lacks_is_critical() -> None:
    issue = one(
        run(measurements(streams=streams(has_audio=False, audio_duration=None))), "audio.missing"
    )

    assert issue.severity is Severity.CRITICAL


def test_sound_and_picture_of_different_length_drift() -> None:
    issue = one(
        run(measurements(streams=streams(audio_duration=Fraction(78, 10)))), "audio.sync_drift"
    )

    assert issue.severity is Severity.HIGH


# --- black, frozen, silent -------------------------------------------------------------------


def test_black_that_nothing_explains_is_critical_and_found_where_it_is() -> None:
    issue = one(run(measurements(black=(span(3.0, 4.2),))), "video.black_frames")

    assert issue.severity is Severity.CRITICAL and issue.blocks_export
    assert (issue.start_frame, issue.end_frame) == (75, 105)
    assert PA in issue.affected_clips


def test_a_short_black_is_high_not_critical() -> None:
    issue = one(run(measurements(black=(span(3.0, 3.3),))), "video.black_frames")

    assert issue.severity is Severity.HIGH and not issue.blocks_export


def test_black_in_a_dip_to_black_is_deliberate_and_not_flagged() -> None:
    facts = clean()
    dip = replace(facts.transitions[0], mode="dip", start_frame=75, end_frame=105, cut_frame=90)
    facts = replace(facts, transitions=(dip,))

    assert run(measurements(black=(span(3.0, 4.2),)), facts) == []


def test_black_the_timeline_gap_already_explains_is_not_reported_twice() -> None:
    gap = (Fraction(3), Fraction(42, 10))

    assert run(measurements(black=(span(3.0, 4.2),)), gaps=[gap]) == []


def test_a_little_black_at_the_start_or_the_end_is_only_a_slip() -> None:
    issue = one(run(measurements(black=(span(0.0, 0.16),))), "video.black_edge")

    assert issue.severity is Severity.LOW


def test_a_picture_that_stops_is_found_but_a_freeze_frame_or_still_image_is_not() -> None:
    stuck = measurements(frozen=(span(2.0, 4.5),))
    assert one(run(stuck), "video.frozen_frames").severity is Severity.HIGH

    planned = with_clip(clean(), PA, freezes=((50, 112),))
    assert run(stuck, planned) == []

    still = clean()
    still = replace(
        still,
        sources=tuple(
            replace(s, media_type="image") if s.source_id == "rec1" else s for s in still.sources
        ),
    )
    assert run(stuck, still) == []


def test_black_is_not_also_called_frozen() -> None:
    found = codes(run(measurements(black=(span(3.0, 4.2),), frozen=(span(3.0, 4.2),))))

    assert found == ["video.black_frames"]


def test_silence_where_sound_is_planned_is_a_finding_and_elsewhere_it_is_intent() -> None:
    planned = run(measurements(silent=(span(1.0, 3.0),)))
    assert one(planned, "audio.unexpected_silence").confidence == 0.75

    nothing_planned = run(
        measurements(silent=(span(8.5, None),), streams=streams(duration=Fraction(8)))
    )
    assert nothing_planned == []


# --- the picture -----------------------------------------------------------------------------


def test_crushed_shadows_clipped_highlights_and_exposure_are_found() -> None:
    m = measurements(
        frames=(
            frame(1.0, crushed_fraction=0.7),
            frame(2.0, clipped_fraction=0.3),
            frame(3.0, luma_mean=10.0),
            frame(5.0, "clip:v:pB", "v:pB", luma_mean=240.0),
        )
    )

    found = codes(run(m))

    assert {
        "visual.crushed_shadows",
        "visual.clipped_highlights",
        "visual.underexposed",
        "visual.overexposed",
    } <= set(found)


def test_a_frame_that_is_a_dip_to_black_is_not_an_exposure_problem() -> None:
    assert run(measurements(frames=(frame(1.0, luma_mean=2.0, black_fraction=0.99),))) == []


def test_a_much_softer_frame_is_a_low_confidence_finding() -> None:
    m = measurements(
        frames=(
            frame(0.5, sharpness=500),
            frame(1.5, sharpness=480),
            frame(2.5, sharpness=520),
            frame(3.0, sharpness=20),
        )
    )

    issue = one(run(m), "visual.soft_focus")

    assert issue.confidence < 0.5 and issue.severity is Severity.LOW


def test_a_colour_jump_across_a_cut_is_found() -> None:
    m = measurements(
        frames=(
            frame(3.9, "cut:100:before", PA, mean_rgb=(150.0, 110.0, 90.0)),
            frame(4.1, "cut:100:after", "v:pB", mean_rgb=(90.0, 110.0, 160.0)),
        )
    )

    issue = one(run(m), "visual.color_mismatch")

    assert issue.start_frame == 100 and issue.severity is Severity.MEDIUM


def test_borders_are_only_wrong_when_the_picture_should_fill_the_frame() -> None:
    bars = measurements(frames=(frame(1.0, border_black=(0.1, 0.1, 0.0, 0.0)),))
    assert run(bars) == []  # contain: bars are what the fit mode means

    facts = clean()
    cover = replace(facts, settings=replace(facts.settings, fit_mode="cover"))
    assert "visual.unexpected_borders" in codes(run(bars, cover))
    off_centre = measurements(frames=(frame(1.0, border_black=(0.0, 0.035, 0.0, 0.0)),))
    assert "visual.bad_crop" in codes(run(off_centre, cover))


# --- the sound -------------------------------------------------------------------------------


def test_loudness_off_target_is_medium_and_far_off_is_high() -> None:
    near = run(measurements(loudness=LoudnessFacts(-18.0, 6.0, -2.0, -3.0)))
    far = run(measurements(loudness=LoudnessFacts(-30.0, 6.0, -2.0, -3.0)))

    assert one(near, "audio.loudness_off").severity is Severity.MEDIUM
    assert one(far, "audio.loudness_off").severity is Severity.HIGH


def test_true_peak_clipping_and_a_wide_range_are_found() -> None:
    m = measurements(loudness=LoudnessFacts(-14.0, 20.0, 0.5, 0.0))

    assert {"audio.true_peak", "audio.clipping", "audio.range_wide"} <= set(codes(run(m)))


def test_dialogue_that_jumps_in_level_is_found_with_the_clips() -> None:
    m = measurements(
        dialogue_levels=(
            ClipLoudness("a:aV", -14.0),
            ClipLoudness("a:aW", -26.0),
            ClipLoudness("a:aX", -15.0),
        )
    )

    issue = one(run(m), "audio.dialogue_inconsistent")

    assert "a:aW" in issue.evidence[0] and issue.basis is Basis.OBJECTIVE


def test_sound_that_is_loud_at_the_very_end_with_no_fade_is_abrupt() -> None:
    facts = clean()
    bare = replace(facts, sounds=tuple(replace(s, fade_out=Fraction(0)) for s in facts.sounds))

    assert "audio.abrupt_end" in codes(run(measurements(end_peak_dbfs=-12.0), bare))
    assert "audio.abrupt_end" not in codes(run(measurements(end_peak_dbfs=-12.0), facts))


def test_a_measurement_that_was_not_made_makes_no_finding() -> None:
    m = measurements(streams=None, loudness=None, end_peak_dbfs=None, dialogue_levels=())

    assert run(m) == []
