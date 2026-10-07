"""Decisions: measured fact -> processing decision -> applied correction, within the bounds."""

import pytest

from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.planning import (
    PlannedOperation,
    ProcessingPlan,
    plan_processing,
    refine_plan,
)
from media_house.modules.video_improvement.domain.profiles import resolve_processing_profile
from media_house.modules.video_improvement.domain.settings import ProcessingProfile
from media_house.modules.video_improvement.domain.source import SourceProfile, get_source_profile
from media_house.modules.video_improvement.domain.values import ProcessingStage, StageStatus
from tests.support.video_fakes import REC709, facts, measurements

NATURAL = resolve_processing_profile("natural")
SOURCE = get_source_profile("rec709")


def plan(
    m: object = None,
    *,
    source: SourceProfile = SOURCE,
    profile: ProcessingProfile = NATURAL,
    predicted: object = None,
    **video: object,
) -> ProcessingPlan:
    """Both planning steps; ``predicted`` defaults to the same measurements (nothing changes)."""
    measured = m or measurements()
    first = plan_processing(measured, facts(**video), source, profile)  # type: ignore[arg-type]
    return refine_plan(first, predicted or measured, profile)  # type: ignore[arg-type]


def op(result: ProcessingPlan, name: str) -> PlannedOperation:
    found = result.operation(name)
    assert found is not None, name
    return found


class TestGoodFootage:
    def test_footage_that_needs_nothing_gets_nothing(self) -> None:
        result = plan()

        assert result.stages_applied == ()
        assert result.color is None
        assert result.denoise is None
        assert result.sharpen is None

    def test_every_skipped_operation_says_why(self) -> None:
        result = plan()

        assert result.operations
        assert all(o.reason for o in result.operations)
        assert all(o.status is StageStatus.SKIPPED for o in result.operations)

    def test_the_same_inputs_always_give_the_same_plan(self) -> None:
        m = measurements(linear_p50=0.05, cast_red=1.1)

        assert plan(m) == plan(m)


class TestExposure:
    def test_underexposed_footage_is_brightened_within_the_bounds(self) -> None:
        result = plan(measurements(linear_p50=0.05, linear_p99=0.20))

        assert result.color is not None
        stops = result.color.exposure_stops
        assert 0 < stops <= NATURAL.color.max_exposure_stops
        assert stops == pytest.approx(
            min(1.848, 2.17, NATURAL.color.max_exposure_stops) * NATURAL.color.exposure_strength,
            abs=0.01,
        )
        assert op(result, "exposure").status is StageStatus.APPLIED

    def test_brightening_stops_before_the_highlights_run_out_of_room(self) -> None:
        # dark median but highlights already near white (a night scene with lights)
        result = plan(measurements(linear_p50=0.03, linear_p99=1.25))

        assert op(result, "exposure").status is StageStatus.SKIPPED
        assert "no room" in op(result, "exposure").reason

    def test_a_small_error_inside_the_deadband_is_left_alone(self) -> None:
        result = plan(measurements(linear_p50=0.16))

        assert op(result, "exposure").status is StageStatus.SKIPPED

    def test_overexposed_footage_with_blown_highlights_is_darkened(self) -> None:
        m = measurements(linear_p50=0.45, linear_p99=1.0, highlight_clip=0.10)

        result = plan(m)

        assert result.color is not None
        assert result.color.exposure_stops < 0

    def test_bright_footage_without_blown_highlights_is_not_touched(self) -> None:
        result = plan(measurements(linear_p50=0.45, linear_p99=0.85, highlight_clip=0.0))

        assert op(result, "exposure").status is StageStatus.SKIPPED
        assert "left as shot" in op(result, "exposure").reason

    def test_the_decision_carries_the_numbers_it_was_based_on(self) -> None:
        m = measurements(linear_p50=0.05, linear_p99=0.20)

        measured = op(plan(m), "exposure").measured

        assert measured["linear_p50"] == 0.05
        assert measured["linear_p99"] == 0.20

    def test_a_profile_with_a_wider_limit_corrects_more(self) -> None:
        m = measurements(linear_p50=0.02, linear_p99=0.10)

        natural = plan(m).color
        outdoor = plan(m, profile=resolve_processing_profile("outdoor")).color

        assert natural is not None and outdoor is not None
        assert outdoor.exposure_stops > natural.exposure_stops


class TestMidtoneLift:
    """Dark footage with bright spots: exposure has no room, so the midtones are lifted."""

    dark_with_bright_spots = measurements(linear_p50=0.059, linear_p99=0.936, highlight_clip=0.08)

    def test_dark_footage_with_bright_highlights_is_lifted_not_left_alone(self) -> None:
        result = plan(self.dark_with_bright_spots)

        assert result.color is not None
        assert NATURAL.color.min_gamma <= result.color.gamma < 1.0
        assert op(result, "midtone_lift").status is StageStatus.APPLIED
        assert result.color.knee is not None  # the shoulder protects the bright spots

    def test_the_lift_is_bounded_by_the_profile(self) -> None:
        extreme = measurements(linear_p50=0.005, linear_p99=0.936)

        natural = plan(extreme).color
        outdoor = plan(extreme, profile=resolve_processing_profile("outdoor")).color

        assert natural is not None and outdoor is not None
        floor = 1.0 - (1.0 - NATURAL.color.min_gamma) * NATURAL.color.lift_strength
        assert natural.gamma == pytest.approx(floor)
        assert outdoor.gamma < natural.gamma  # outdoor footage may be lifted harder

    def test_bright_enough_midtones_are_not_lifted(self) -> None:
        assert op(plan(measurements(linear_p50=0.17)), "midtone_lift").status is StageStatus.SKIPPED

    def test_overexposed_footage_is_never_lifted(self) -> None:
        result = plan(measurements(linear_p50=0.5, linear_p99=1.0, highlight_clip=0.1))

        assert op(result, "midtone_lift").status is StageStatus.SKIPPED

    def test_exposure_does_the_work_first_and_the_lift_only_the_rest(self) -> None:
        result = plan(measurements(linear_p50=0.12, linear_p99=0.4))

        assert result.color is not None
        assert result.color.exposure_stops > NATURAL.color.exposure_deadband_stops
        assert result.color.gamma == 1.0  # exposure got close enough to the target on its own

    def test_a_rolloff_lets_exposure_use_more_headroom(self) -> None:
        m = measurements(linear_p50=0.05, linear_p99=1.0, highlight_clip=0.05)

        natural = plan(m).color
        outdoor = plan(m, profile=resolve_processing_profile("outdoor")).color

        assert natural is not None and outdoor is not None
        assert outdoor.exposure_stops > natural.exposure_stops
        assert outdoor.knee is not None


class TestWhiteBalance:
    def test_a_clear_cast_is_corrected_in_the_opposite_direction(self) -> None:
        result = plan(measurements(cast_red=1.06, cast_blue=0.94))

        assert result.color is not None
        red, green, blue = result.color.gains
        assert red < 1.0 < blue
        assert green == 1.0

    def test_the_correction_is_limited_by_the_profile(self) -> None:
        result = plan(measurements(cast_red=1.4, cast_blue=0.6))

        assert result.color is not None
        red, _, blue = result.color.gains
        limit = NATURAL.color.max_white_balance_shift
        assert red == pytest.approx(1 - limit)
        assert blue == pytest.approx(1 + limit)

    def test_a_cast_inside_the_deadband_is_ignored(self) -> None:
        result = plan(measurements(cast_red=1.02, cast_blue=0.98))

        assert op(result, "white_balance").status is StageStatus.SKIPPED
        assert op(result, "white_balance").reason == "no colour cast"

    def test_without_enough_neutral_pixels_no_cast_is_assumed(self) -> None:
        # a scene full of green foliage: the average colour says nothing about the light
        result = plan(measurements(cast_red=0.8, cast_blue=0.7, neutral_share=0.01))

        assert op(result, "white_balance").status is StageStatus.SKIPPED
        assert "too few neutral" in op(result, "white_balance").reason

    def test_unmeasurable_cast_is_skipped(self) -> None:
        result = plan(measurements(cast_red=None, cast_blue=None, neutral_share=0.0))

        assert op(result, "white_balance").status is StageStatus.SKIPPED


class TestToneAndColor:
    def test_milky_blacks_are_pulled_down_but_deep_blacks_are_not(self) -> None:
        milky = plan(measurements(luma_p1=0.15))
        deep = plan(measurements(luma_p1=0.03))

        assert milky.color is not None
        assert 0 < milky.color.black_point <= NATURAL.color.max_black_point_shift
        assert op(deep, "black_point").status is StageStatus.SKIPPED

    def test_blown_highlights_get_a_soft_rolloff(self) -> None:
        result = plan(measurements(linear_p99=0.98, highlight_clip=0.08))

        assert result.color is not None
        assert result.color.knee == NATURAL.color.highlight_knee

    def test_highlights_with_room_get_no_rolloff(self) -> None:
        result = plan(measurements(linear_p99=0.6))

        assert op(result, "highlight_rolloff").status is StageStatus.SKIPPED

    def test_flat_footage_gets_its_contrast_raised_but_never_beyond_the_limit(self) -> None:
        flat = measurements(luma_p1=0.2, luma_p99=0.6)

        result = plan(flat)

        assert result.color is not None
        assert 1.0 < result.color.contrast <= NATURAL.color.max_contrast_boost

    def test_a_normal_tonal_range_keeps_its_contrast(self) -> None:
        assert plan().operation("contrast") is None
        assert plan().color is None

    def test_oversaturated_footage_is_pulled_into_the_band_never_beyond_the_limit(self) -> None:
        result = plan(measurements(mean_saturation=0.72))

        assert result.color is not None
        limit = NATURAL.color.max_saturation_change
        assert 1.0 - limit <= result.color.saturation < 1.0

    def test_washed_out_footage_is_raised_into_the_band(self) -> None:
        result = plan(measurements(mean_saturation=0.06))

        assert result.color is not None
        assert 1.0 < result.color.saturation <= 1.0 + NATURAL.color.max_saturation_change

    def test_saturation_inside_the_band_is_left_alone(self) -> None:
        assert op(plan(measurements(mean_saturation=0.30)), "saturation").status is (
            StageStatus.SKIPPED
        )

    def test_saturation_is_judged_on_the_predicted_picture(self) -> None:
        source = measurements(mean_saturation=0.30)
        predicted = measurements(mean_saturation=0.70)  # the grade would oversaturate

        result = plan(source, predicted=predicted)

        assert result.color is not None
        assert result.color.saturation < 1.0

    def test_a_user_look_lut_is_always_applied_and_traceable(self) -> None:
        profile = resolve_processing_profile("natural")
        from dataclasses import replace

        from media_house.modules.video_improvement.domain.settings import LookSettings

        profile = replace(profile, look=LookSettings(lut_path="look.cube", strength=0.6))

        first = plan_processing(measurements(), facts(), SOURCE, profile, look_sha256="abc")
        result = refine_plan(first, measurements(), profile)

        assert result.color is not None
        assert (result.color.look_lut, result.color.look_strength) == ("look.cube", 0.6)
        assert op(result, "look").parameters["sha256"] == "abc"


class TestSceneReferredFootage:
    flat = get_source_profile("gopro_flat")

    def test_the_input_transform_and_rendering_are_always_applied(self) -> None:
        result = plan(source=self.flat)

        assert result.color is not None
        assert result.color.input_color.scene_referred
        assert result.color.contrast == pytest.approx(self.flat.rendering.contrast)
        assert result.color.knee is not None
        assert op(result, "input_transform").status is StageStatus.APPLIED

    def test_blacks_are_not_pulled_because_the_rendering_sets_them(self) -> None:
        result = plan(measurements(luma_p1=0.2), source=self.flat)

        assert op(result, "black_point").status is StageStatus.SKIPPED

    def test_a_camera_is_a_profile_not_a_code_path(self) -> None:
        slog = get_source_profile("sony_slog3")

        result = plan(source=slog)

        assert result.color is not None
        assert result.color.input_color == ColorSpec(Transfer.SLOG3, Primaries.SGAMUT3_CINE)
        assert result.color.contrast == pytest.approx(slog.rendering.contrast)


class TestColorSpaceUnknownOrUnsupported:
    def test_an_unknown_colour_space_is_never_guessed(self) -> None:
        result = plan(measurements(linear_p50=0.02), source=get_source_profile("generic"))

        assert result.color is None
        assert op(result, "color").status is StageStatus.SKIPPED
        assert "not guessed" in op(result, "color").reason

    def test_denoising_and_sharpening_still_work_without_a_known_colour_space(self) -> None:
        noisy = measurements(noise_sigma=0.02)

        result = plan(noisy, source=get_source_profile("generic"))

        assert result.denoise is not None

    def test_hdr_is_not_graded(self) -> None:
        hdr = SourceProfile("hdr", "test", input_color=ColorSpec(Transfer.PQ, Primaries.BT2020))

        result = plan(source=hdr)

        assert result.color is None
        assert "HDR" in op(result, "color").reason

    def test_the_colour_stage_can_be_disabled(self) -> None:
        from dataclasses import replace

        from media_house.modules.video_improvement.domain.settings import ColorSettings

        off = replace(NATURAL, color=ColorSettings(enabled=False))

        result = plan(measurements(linear_p50=0.02), profile=off)

        assert result.color is None


class TestDenoise:
    def test_clean_footage_is_not_denoised(self) -> None:
        assert plan(measurements(noise_sigma=0.002)).denoise is None

    def test_strength_grows_with_the_noise_inside_the_profile_bounds(self) -> None:
        low = plan(measurements(noise_sigma=0.008)).denoise
        high = plan(measurements(noise_sigma=0.02)).denoise
        extreme = plan(measurements(noise_sigma=0.5)).denoise

        assert low is not None and high is not None and extreme is not None
        assert NATURAL.denoise.min_strength <= low.strength < high.strength
        assert extreme.strength == pytest.approx(NATURAL.denoise.max_strength)

    def test_interlaced_footage_is_left_alone(self) -> None:
        result = plan(measurements(noise_sigma=0.03), interlaced=True)

        assert result.denoise is None
        assert "interlaced" in op(result, "denoise").reason

    def test_a_clean_studio_signal_needs_more_noise_than_the_default_to_trigger(self) -> None:
        m = measurements(noise_sigma=0.007)

        assert plan(m).denoise is not None
        assert plan(m, profile=resolve_processing_profile("studio")).denoise is None


class TestSharpen:
    def test_soft_clean_footage_is_sharpened_in_proportion(self) -> None:
        slightly = plan(measurements(sharpness=0.07)).sharpen
        very = plan(measurements(sharpness=0.02)).sharpen

        assert slightly is not None and very is not None
        assert NATURAL.sharpen.min_amount <= slightly.amount < very.amount
        assert very.amount <= NATURAL.sharpen.max_amount

    def test_crisp_footage_is_never_sharpened(self) -> None:
        result = plan(measurements(sharpness=0.14))

        assert result.sharpen is None
        assert op(result, "sharpen").reason == "already crisp"

    def test_noisy_soft_footage_is_not_sharpened(self) -> None:
        result = plan(measurements(sharpness=0.03, noise_sigma=0.02))

        assert result.sharpen is None
        assert "noisy" in op(result, "sharpen").reason


class TestWarningsAndBypass:
    def test_changing_exposure_through_the_clip_is_reported_not_hidden(self) -> None:
        result = plan(measurements(exposure_variation_stops=1.8))

        assert any("exposure varies" in w for w in result.warnings)

    def test_a_bypassed_stage_is_removed_and_recorded(self) -> None:
        result = plan(measurements(linear_p50=0.05, linear_p99=0.2, noise_sigma=0.02))
        assert result.applied(ProcessingStage.COLOR) and result.applied(ProcessingStage.DENOISE)

        bypassed = result.bypass(ProcessingStage.COLOR, "it made the picture worse")

        assert bypassed.color is None
        assert bypassed.denoise == result.denoise  # other stages stay
        assert not bypassed.applied(ProcessingStage.COLOR)
        assert bypassed.stages_applied == (ProcessingStage.DENOISE,)
        recorded = [o for o in bypassed.operations if o.status is StageStatus.BYPASSED]
        assert recorded
        assert all(o.reason == "it made the picture worse" for o in recorded)

    def test_the_colour_plan_is_a_dataclass_of_plain_numbers(self) -> None:
        result = plan(measurements(linear_p50=0.05, linear_p99=0.2))

        assert result.color is not None
        assert result.color.input_color == REC709
