"""Camera, picture quality, handles, keyframes and sampling derived from hand-made signals."""

from dataclasses import replace
from itertools import pairwise

import pytest

from media_house.modules.video_intelligence.domain.camera import describe_camera, describe_motion
from media_house.modules.video_intelligence.domain.observations import CameraObservation
from media_house.modules.video_intelligence.domain.profiles import (
    MeasurementSettings,
    MotionSettings,
    get_profile,
)
from media_house.modules.video_intelligence.domain.sampling import decode_stride, plan_samples
from media_house.modules.video_intelligence.domain.signals import MotionSignals
from media_house.modules.video_intelligence.domain.source import ProcessingHistory
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerState,
    BoundaryKind,
    CameraMovement,
    MeasuredOn,
    Stabilization,
)
from tests.support.vi_signals import (
    IMPROVED,
    ORIGINAL,
    derived,
    motion_signals,
    quality_signals,
    shot_signals,
    source,
)

TIMELINE = shot_signals(120)
SETTINGS = MotionSettings()
ASPECT = 180 / 320
#: One sample pair spans two frames at 30 fps; a pan of ``v`` frame widths per second moves the
#: picture ``v * 2/30`` of the width per pair.
PAIR_SECONDS = 2 / 30


def camera(
    motion: MotionSignals, history: ProcessingHistory = ORIGINAL, first: int = 0, end: int = 120
) -> CameraObservation:
    return describe_camera(motion, TIMELINE, first, end, ASPECT, SETTINGS, history)


class TestCameraMovement:
    def test_a_steady_sideways_drift_of_the_content_is_a_pan_in_the_opposite_direction(
        self,
    ) -> None:
        # content moves left at 0.2 widths/s -> the camera panned right (0 degrees)
        observed = camera(motion_signals(30, tx=lambda _k: -0.2 * PAIR_SECONDS))

        assert observed.ok and observed.movement is CameraMovement.PAN
        assert observed.speed == pytest.approx(0.2, rel=0.01)
        assert observed.direction_degrees is not None
        assert min(observed.direction_degrees, 360 - observed.direction_degrees) < 1.0

    def test_content_moving_down_is_the_camera_tilting_up(self) -> None:
        observed = camera(motion_signals(30, ty=lambda _k: 0.2 * PAIR_SECONDS))

        assert observed.movement is CameraMovement.TILT
        assert observed.direction_degrees == pytest.approx(90.0, abs=1.0)

    def test_no_motion_is_static(self) -> None:
        observed = camera(motion_signals(30))

        assert observed.movement is CameraMovement.STATIC
        assert observed.intensity == pytest.approx(0.0, abs=0.01)
        assert observed.direction_degrees is None

    def test_growing_content_is_a_push_in_and_shrinking_content_a_pull_out(self) -> None:
        grows = camera(motion_signals(30, scale=lambda _k: 0.3 * PAIR_SECONDS))
        shrinks = camera(motion_signals(30, scale=lambda _k: -0.3 * PAIR_SECONDS))

        assert grows.movement is CameraMovement.PUSH_IN
        assert shrinks.movement is CameraMovement.PULL_OUT
        assert grows.zoom_rate == pytest.approx(0.3, rel=0.01)

    def test_random_jitter_without_a_course_is_handheld(self) -> None:
        jitter = [0.012, -0.011, 0.013, -0.012, 0.011, -0.013] * 5
        observed = camera(motion_signals(30, tx=lambda k: jitter[k]))

        assert observed.movement is CameraMovement.HANDHELD
        assert observed.shake_residual is not None and observed.shake_residual > 0.004

    def test_a_pan_that_shakes_is_still_a_pan_and_says_it_shakes(self) -> None:
        jitter = [0.012, -0.011, 0.013, -0.012, 0.011, -0.013] * 5
        observed = camera(motion_signals(30, tx=lambda k: -0.2 * PAIR_SECONDS + jitter[k]))

        assert observed.movement is CameraMovement.PAN
        assert "handheld_shake" in observed.reasons

    def test_two_motions_at_once_are_mixed(self) -> None:
        observed = camera(
            motion_signals(
                30, tx=lambda _k: -0.2 * PAIR_SECONDS, scale=lambda _k: 0.3 * PAIR_SECONDS
            )
        )

        assert observed.movement is CameraMovement.MIXED

    def test_a_back_and_forth_motion_is_not_called_a_pan(self) -> None:
        swing = [-0.02, 0.02] * 15
        observed = camera(motion_signals(30, tx=lambda k: swing[k]))

        assert observed.movement is not CameraMovement.PAN

    def test_too_few_reliable_estimates_leave_the_movement_unknown_with_a_reason(self) -> None:
        weak = camera(motion_signals(30, confidence=lambda _k: 0.05))
        short = camera(motion_signals(1))

        for observed in (weak, short):
            assert observed.state is AnalyzerState.UNKNOWN
            assert observed.movement is None and observed.confidence is None
            assert observed.reasons == ("too_few_reliable_motion_estimates",)

    def test_estimates_never_cross_a_shot_boundary(self) -> None:
        # the pairs inside 0-40 pan, the pairs after 40 are static; a boundary at 40 separates them
        pans = motion_signals(60, tx=lambda k: -0.2 * PAIR_SECONDS if k < 20 else 0.0)

        first = camera(pans, first=0, end=40)
        second = camera(pans, first=40, end=120)

        assert first.movement is CameraMovement.PAN
        assert second.movement is CameraMovement.STATIC

    def test_every_observation_says_which_version_of_the_footage_it_was_measured_on(self) -> None:
        original = camera(motion_signals(30), ORIGINAL)
        improved = camera(motion_signals(30), IMPROVED)
        unknown = camera(motion_signals(1), replace(IMPROVED, measured_on=MeasuredOn.UNKNOWN))

        assert (original.measured_on, original.stabilized) == (
            MeasuredOn.ORIGINAL,
            Stabilization.NO,
        )
        assert improved.measured_on is MeasuredOn.IMPROVED
        assert unknown.measured_on is MeasuredOn.UNKNOWN  # even when nothing could be said

    def test_motion_measured_on_stabilized_footage_is_flagged(self) -> None:
        stabilized = replace(IMPROVED, stabilized=Stabilization.YES)

        observed = camera(motion_signals(30), stabilized)

        assert "measured_on_stabilized_footage" in observed.reasons
        assert observed.stabilized is Stabilization.YES

    def test_confidence_reflects_the_estimates_it_rests_on(self) -> None:
        sure = camera(motion_signals(30, confidence=lambda _k: 0.95))
        shaky = camera(motion_signals(30, confidence=lambda _k: 0.3))

        assert shaky.confidence is not None and sure.confidence is not None
        assert 0 < shaky.confidence < sure.confidence <= 1

    def test_evidence_names_frames_inside_the_shot(self) -> None:
        observed = camera(motion_signals(30), first=10, end=60)

        frames = [t.frame for e in observed.evidence for t in e.frames]
        assert frames and all(10 <= f < 60 for f in frames)


class TestPictureMotion:
    def test_residual_energy_is_summarised_with_its_peak_moment(self) -> None:
        motion = motion_signals(30, residual=lambda k: 0.2 if k == 12 else 0.01)

        observed = describe_motion(motion, TIMELINE, 0, 120, ASPECT, SETTINGS)

        assert observed.ok
        assert observed.peak_energy == pytest.approx(0.2)
        assert observed.peak_time is not None and observed.peak_time.frame == 26
        assert observed.mean_energy is not None and observed.mean_energy < 0.1

    def test_still_pairs_make_the_static_fraction(self) -> None:
        motion = motion_signals(
            20, tx=lambda k: -0.2 * PAIR_SECONDS if k < 10 else 0.0, residual=lambda _k: 0.001
        )

        observed = describe_motion(motion, TIMELINE, 0, 120, ASPECT, SETTINGS)

        assert observed.static_fraction == pytest.approx(0.5)

    def test_no_pair_inside_the_shot_is_unknown_with_a_reason(self) -> None:
        observed = describe_motion(motion_signals(5), TIMELINE, 100, 120, ASPECT, SETTINGS)

        assert observed.state is AnalyzerState.UNKNOWN and observed.reasons


class TestPictureQuality:
    def reasons_of(self, **metrics: float) -> tuple[str, ...]:
        frames = list(range(0, 120, 4))
        result = derived(TIMELINE, None, quality_signals(frames, **metrics))
        quality = result.shots[0].quality
        assert quality.ok
        return quality.reasons

    def test_a_healthy_picture_has_no_reason(self) -> None:
        assert self.reasons_of() == ()

    def test_blur_noise_clipping_and_crushing_are_described(self) -> None:
        assert "soft_focus" in self.reasons_of(sharpness=0.05)
        assert "noisy" in self.reasons_of(noise=0.05)
        assert "clipped_highlights" in self.reasons_of(clipped=0.2)
        assert "crushed_shadows" in self.reasons_of(crushed=0.6)

    def test_dark_and_bright_footage_is_described_by_its_median(self) -> None:
        assert "dark_exposure" in self.reasons_of(p1=0.0, p50=0.08, p99=0.5)
        assert "bright_exposure" in self.reasons_of(p1=0.4, p50=0.9, p99=1.0)

    def test_flat_footage_is_not_called_underexposed(self) -> None:
        # a log-like picture: lifted blacks, low contrast, a low median
        reasons = self.reasons_of(p1=0.18, p50=0.13, p99=0.3)

        assert "flat_or_log_footage" in reasons
        assert not {"dark_exposure", "crushed_shadows", "clipped_highlights"} & set(reasons)

    def test_a_dark_picture_with_crushed_blacks_is_not_mistaken_for_flat(self) -> None:
        reasons = self.reasons_of(p1=0.0, p50=0.08, p99=0.2)

        assert "dark_exposure" in reasons and "flat_or_log_footage" not in reasons

    def test_hdr_transfers_are_not_judged_for_exposure(self) -> None:
        frames = list(range(0, 120, 4))
        hdr = source(color_transfer="smpte2084")

        result = derived(
            TIMELINE, None, quality_signals(frames, p50=0.9, clipped=0.5), info=hdr
        ).shots[0]

        assert "exposure_not_judged_non_display_transfer" in result.quality.reasons
        assert "bright_exposure" not in result.quality.reasons
        assert "clipped_highlights" not in result.quality.reasons

    def test_flicker_is_measured_from_every_frame_not_the_samples(self) -> None:
        flickering = shot_signals(120, luma=lambda i: 0.5 + (0.03 if i % 2 else -0.03))

        result = derived(flickering, None, quality_signals(list(range(0, 120, 4))))

        assert "flicker_present" in result.shots[0].quality.reasons
        assert result.shots[0].quality.metrics.flicker == pytest.approx(0.06, rel=0.1)

    def test_too_few_samples_in_a_shot_is_unknown(self) -> None:
        result = derived(TIMELINE, None, quality_signals([10]))

        quality = result.shots[0].quality
        assert quality.state is AnalyzerState.UNKNOWN
        assert quality.reasons == ("too_few_sampled_frames",)
        assert quality.metrics.sampled_frames == 1

    def test_metrics_stay_raw_and_separate_from_the_reasons(self) -> None:
        result = derived(TIMELINE, None, quality_signals(list(range(0, 120, 4)), sharpness=0.07))

        metrics = result.shots[0].quality.metrics
        assert metrics.sharpness == pytest.approx(0.07)
        assert metrics.tonal_spread == pytest.approx(0.9)
        assert metrics.sampled_frames == 30

    def test_confidence_grows_with_the_number_of_samples(self) -> None:
        few = derived(TIMELINE, None, quality_signals([0, 40])).shots[0].quality
        many = derived(TIMELINE, None, quality_signals(list(range(0, 120, 4)))).shots[0].quality

        assert few.confidence is not None and many.confidence is not None
        assert few.confidence < many.confidence == 1.0


class TestShotsHandlesAndKeyframes:
    def test_shots_tile_the_video_and_have_deterministic_ids(self) -> None:
        timeline = shot_signals(120, cuts=(40, 90))

        first = derived(timeline).shots
        again = derived(timeline).shots

        assert [s.shot_id for s in first] == ["shot_0000000", "shot_0000040", "shot_0000090"]
        assert first == again
        assert [(s.range.start.frame, s.range.end.frame) for s in first] == [
            (0, 40),
            (40, 90),
            (90, 120),
        ]
        assert first[-1].range.end.pts == timeline.end_pts

    def test_boundaries_chain_start_cut_cut_end(self) -> None:
        shots = derived(shot_signals(120, cuts=(40, 90))).shots

        kinds = [shots[0].boundary_in.kind] + [s.boundary_out.kind for s in shots]
        assert kinds == [
            BoundaryKind.START,
            BoundaryKind.HARD_CUT,
            BoundaryKind.HARD_CUT,
            BoundaryKind.END,
        ]
        assert shots[0].boundary_out is shots[1].boundary_in

    def test_handles_count_the_stable_frames_at_each_end(self) -> None:
        # busy motion in the middle of the shot, quiet at both ends
        timeline = shot_signals(120, steps=lambda i: 0.1 if 40 <= i <= 80 else 0.003)

        handles = derived(timeline).shots[0].handles

        assert handles.ok
        assert handles.head_frames == 39
        assert handles.tail_frames == 39
        assert handles.head_seconds == pytest.approx(39 / 30)

    def test_handles_never_exceed_half_the_shot(self) -> None:
        handles = derived(shot_signals(120)).shots[0].handles

        assert handles.head_frames == handles.tail_frames == 60

    def test_a_very_short_shot_cannot_state_handles(self) -> None:
        shots = derived(shot_signals(120, cuts=(60, 63))).shots

        short = next(s for s in shots if s.frames <= 3)
        assert short.handles.state is AnalyzerState.UNKNOWN
        assert short.handles.reasons == ("shot_too_short_for_handles",)

    def test_keyframes_are_sampled_frames_of_the_shot_with_the_sharpest_named(self) -> None:
        frames = list(range(0, 120, 4))
        quality = quality_signals(frames)
        sharpness = list(quality.sharpness)
        sharpness[10] = 0.9  # frame 40 is the sharpest
        quality = replace(quality, sharpness=tuple(sharpness))

        keys = derived(shot_signals(120), None, quality).shots[0].keyframes

        assert keys.ok
        assert keys.sharpest is not None and keys.sharpest.frame == 40
        assert keys.representative is not None and keys.representative.frame in frames

    def test_a_shot_without_sampled_frames_says_so(self) -> None:
        quality = quality_signals([10, 14])
        shots = derived(shot_signals(120, cuts=(60,)), None, quality).shots

        assert shots[1].keyframes.state is AnalyzerState.NOT_ANALYZED
        assert shots[1].keyframes.reasons == ("no_sampled_frame_in_shot",)

    def test_analyzers_that_did_not_run_leave_explicit_states_not_blanks(self) -> None:
        shot = derived(shot_signals(120)).shots[0]

        assert shot.camera.state is AnalyzerState.NOT_ANALYZED
        assert shot.motion.state is AnalyzerState.NOT_ANALYZED
        assert shot.quality.state is AnalyzerState.NOT_ANALYZED
        assert shot.keyframes.state is AnalyzerState.NOT_ANALYZED
        assert shot.camera.reasons == ("motion_analyzer_not_run",)

    def test_curves_carry_pts_aligned_to_the_signals(self) -> None:
        motion = motion_signals(10)
        quality = quality_signals([0, 4, 8])

        result = derived(shot_signals(120), motion, quality)

        names = {c.name for c in result.curves}
        assert names == {"motion_energy", "sharpness", "luma_median"}
        energy = next(c for c in result.curves if c.name == "motion_energy")
        assert energy.pts == tuple(f * 1000 for f in energy.frames)
        assert energy.measured_on is MeasuredOn.ORIGINAL

    def test_flashes_and_interpolated_timestamps_are_reported_as_warnings(self) -> None:
        timeline = replace(shot_signals(120, flashes=(30,)), estimated_timestamps=3)

        warnings = derived(timeline).warnings

        assert any("flash" in w for w in warnings)
        assert any("interpolated" in w for w in warnings)


class TestAdaptiveSampling:
    SETTINGS = MeasurementSettings(
        candidate_fps=10.0, static_gap_seconds=1.0, change_threshold=0.05
    )

    def test_the_stride_follows_the_source_rate_and_the_profile(self) -> None:
        assert decode_stride(30.0, self.SETTINGS) == 3
        assert decode_stride(60.0, self.SETTINGS) == 6
        assert decode_stride(None, self.SETTINGS) == 3
        assert decode_stride(5.0, self.SETTINGS) == 1

    def test_a_static_stretch_is_sampled_sparsely_and_a_busy_one_densely(self) -> None:
        timeline = shot_signals(300, steps=lambda i: 0.02 if 100 <= i < 200 else 0.0005)

        plan = plan_samples(timeline, 3, self.SETTINGS)

        busy = [f for f in plan if 100 <= f < 200]
        still = [f for f in plan if f < 100]
        assert len(busy) > 3 * len(still)
        assert plan[0] == 0

    def test_no_stretch_is_left_longer_than_the_static_gap(self) -> None:
        plan = plan_samples(shot_signals(300, steps=lambda _i: 0.0), 3, self.SETTINGS)

        gaps = [b - a for a, b in pairwise(plan)]
        assert max(gaps) <= 30 and min(gaps) >= 3

    def test_the_plan_is_deterministic_and_only_uses_candidate_frames(self) -> None:
        timeline = shot_signals(300, cuts=(120,))

        plan = plan_samples(timeline, 3, self.SETTINGS)

        assert plan == plan_samples(timeline, 3, self.SETTINGS)
        assert all(f % 3 == 0 for f in plan)

    def test_a_cut_forces_a_sample_right_after_it(self) -> None:
        plan = plan_samples(shot_signals(300, cuts=(121,)), 3, self.SETTINGS)

        assert any(121 <= f <= 124 for f in plan)

    def test_the_standard_profile_samples_denser_than_triage(self) -> None:
        timeline = shot_signals(600, steps=lambda i: 0.004)
        standard = get_profile("standard").measurement
        triage = get_profile("triage").measurement

        dense = plan_samples(timeline, decode_stride(30.0, standard), standard)
        sparse = plan_samples(timeline, decode_stride(30.0, triage), triage)

        assert len(dense) > len(sparse)
