"""Decide and re-check: evidence-driven planning, stage guards, delivery verification."""

from collections.abc import Mapping

import pytest

from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.planning import plan_processing
from media_house.modules.audio_improvement.domain.profiles import resolve_profile
from media_house.modules.audio_improvement.domain.values import Parameter
from media_house.modules.audio_improvement.domain.values import ProcessingStage as Stage
from media_house.modules.audio_improvement.domain.verification import (
    meets_delivery,
    stage_regression,
    verify_output,
)
from media_house.shared.errors import InvariantViolation
from tests.support.improvement_fakes import measurements

PROFILE = resolve_profile()


def num(params: Mapping[str, Parameter], key: str) -> float:
    value = params[key]
    assert isinstance(value, float)
    return value


def stages(m: QualityMeasurements) -> dict[Stage, object]:
    return {s.stage: s for s in plan_processing(m, PROFILE).stages}


# --- planning ----------------------------------------------------------------------------------
def test_clean_audio_gets_no_enhancement_at_all() -> None:
    plan = plan_processing(measurements(), PROFILE)

    assert plan.planned == ()
    assert plan.warnings == ()
    assert [s.stage for s in plan.stages] == [
        Stage.CLIPPING_REPAIR,
        Stage.NOISE_REDUCTION,
        Stage.DEREVERBERATION,
        Stage.EQUALIZATION,
        Stage.DYNAMICS,
        Stage.DE_ESSING,
    ]
    assert all(s.reason for s in plan.stages)  # every skip says why


def test_noise_reduction_grows_with_the_noise_and_uses_the_measured_floor() -> None:
    mild = plan_processing(measurements(snr_db=30.0, noise_floor_dbfs=-48.0), PROFILE).stages[1]
    heavy = plan_processing(measurements(snr_db=12.0, noise_floor_dbfs=-30.0), PROFILE).stages[1]

    assert mild.apply and heavy.apply
    assert (
        PROFILE.noise.min_strength <= num(mild.params, "strength") < num(heavy.params, "strength")
    )
    assert num(heavy.params, "strength") <= PROFILE.noise.max_strength
    assert heavy.params["noise_floor_dbfs"] == -30.0


def test_hum_alone_plans_a_notch_without_broadband_reduction() -> None:
    planned = plan_processing(measurements(hum_hz=50.0, hum_prominence_db=30.0), PROFILE).stages[1]

    assert planned.apply
    assert planned.params == {"broadband": False, "hum_hz": 50.0}


def test_unmeasurable_noise_is_not_guessed() -> None:
    planned = plan_processing(measurements(snr_db=None, noise_floor_dbfs=None), PROFILE).stages[1]

    assert not planned.apply
    assert "evidence" in planned.reason


def test_reverb_is_only_treated_when_the_room_is_clearly_wet() -> None:
    dry = plan_processing(measurements(reverb_rt60=0.4), PROFILE).stages[2]
    wet = plan_processing(measurements(reverb_rt60=1.0), PROFILE).stages[2]
    unknown = plan_processing(measurements(reverb_rt60=None), PROFILE).stages[2]

    assert (dry.apply, wet.apply, unknown.apply) == (False, True, False)
    assert wet.params["rt60"] == 1.0


def test_clipping_is_repaired_and_severe_clipping_is_not_promised() -> None:
    plan = plan_processing(measurements(clipping_ratio=0.03), PROFILE)

    assert plan.stages[0].apply
    assert any("severe clipping" in w for w in plan.warnings)
    assert not plan_processing(measurements(clipping_ratio=0.0001), PROFILE).stages[0].apply


def test_eq_touches_only_the_bands_that_are_off_and_never_beyond_the_cap() -> None:
    planned = plan_processing(
        measurements(rumble_db=-10.0, mud_db=30.0, harshness_db=-15.0), PROFILE
    ).stages[3]

    assert planned.params["highpass_hz"] == PROFILE.eq.highpass_hz
    assert planned.params["mud_gain_db"] == -PROFILE.eq.mud_max_cut_db
    assert "harshness_gain_db" not in planned.params  # within range: untouched


def test_dynamics_and_de_essing_follow_their_measurements() -> None:
    uneven = plan_processing(measurements(speech_dynamics_db=45.0, sibilance_peak_db=0.0), PROFILE)
    dynamics, de_ess = uneven.stages[4], uneven.stages[5]

    assert dynamics.apply
    assert dynamics.params["threshold_dbfs"] == pytest.approx(
        -18.0 - PROFILE.dynamics.threshold_below_speech_db
    )
    assert de_ess.apply
    assert (
        PROFILE.de_ess.min_intensity
        <= num(de_ess.params, "intensity")
        <= PROFILE.de_ess.max_intensity
    )


def test_planning_is_deterministic() -> None:
    m = measurements(snr_db=20.0, reverb_rt60=1.1, sibilance_peak_db=-1.0)

    assert plan_processing(m, PROFILE) == plan_processing(m, PROFILE)


# --- stage guards ------------------------------------------------------------------------------
def guard(
    stage: Stage, before: QualityMeasurements, after: QualityMeasurements, **params: object
) -> str | None:
    return stage_regression(stage, before, after, PROFILE, params)  # type: ignore[arg-type]


def test_a_stage_that_changes_the_duration_is_a_regression() -> None:
    assert "duration" in (
        guard(Stage.EQUALIZATION, measurements(), measurements(duration=10.5)) or ""
    )


def test_a_stage_that_adds_clipping_is_a_regression() -> None:
    reason = guard(Stage.DYNAMICS, measurements(), measurements(clipping_ratio=0.01))

    assert reason is not None
    assert "clipping" in reason


def test_noise_reduction_must_actually_improve_the_snr_without_over_cleaning() -> None:
    before = measurements(snr_db=20.0, speech_level_dbfs=-18.0)

    assert guard(Stage.NOISE_REDUCTION, before, measurements(snr_db=34.0), broadband=True) is None
    assert "only" in (
        guard(Stage.NOISE_REDUCTION, before, measurements(snr_db=20.5), broadband=True) or ""
    )
    assert "over-cleaning" in (
        guard(
            Stage.NOISE_REDUCTION,
            before,
            measurements(snr_db=40.0, speech_level_dbfs=-24.0),
            broadband=True,
        )
        or ""
    )


def test_a_hum_notch_must_remove_the_hum() -> None:
    before = measurements(hum_hz=50.0, hum_prominence_db=30.0)

    assert guard(Stage.NOISE_REDUCTION, before, measurements(), broadband=False) is None
    assert guard(
        Stage.NOISE_REDUCTION,
        before,
        measurements(hum_hz=50.0, hum_prominence_db=29.0),
        broadband=False,
    )


def test_each_enhancement_stage_is_judged_by_its_own_goal() -> None:
    assert (
        guard(
            Stage.CLIPPING_REPAIR,
            measurements(clipping_ratio=0.02),
            measurements(clipping_ratio=0.0),
        )
        is None
    )
    assert guard(
        Stage.CLIPPING_REPAIR, measurements(clipping_ratio=0.02), measurements(clipping_ratio=0.02)
    )
    assert (
        guard(Stage.DEREVERBERATION, measurements(reverb_rt60=1.0), measurements(reverb_rt60=0.6))
        is None
    )
    assert guard(
        Stage.DEREVERBERATION, measurements(reverb_rt60=1.0), measurements(reverb_rt60=0.98)
    )
    assert (
        guard(
            Stage.DYNAMICS,
            measurements(speech_dynamics_db=18.0),
            measurements(speech_dynamics_db=9.0),
        )
        is None
    )
    assert guard(
        Stage.DYNAMICS, measurements(speech_dynamics_db=18.0), measurements(speech_dynamics_db=18.0)
    )
    assert (
        guard(
            Stage.DE_ESSING,
            measurements(sibilance_peak_db=0.0),
            measurements(sibilance_peak_db=-8.0),
        )
        is None
    )
    assert guard(
        Stage.DE_ESSING, measurements(sibilance_peak_db=0.0), measurements(sibilance_peak_db=0.0)
    )
    assert guard(
        Stage.EQUALIZATION,
        measurements(speech_level_dbfs=-18.0),
        measurements(speech_level_dbfs=-25.0),
    )


def test_evidence_that_cannot_be_measured_never_counts_against_a_stage() -> None:
    unknown = measurements(
        snr_db=None, reverb_rt60=None, speech_dynamics_db=None, sibilance_peak_db=None
    )

    for stage in (Stage.NOISE_REDUCTION, Stage.DEREVERBERATION, Stage.DYNAMICS, Stage.DE_ESSING):
        assert guard(stage, unknown, unknown, broadband=True) is None


# --- delivery ----------------------------------------------------------------------------------
def test_delivery_is_judged_by_measured_loudness_and_true_peak() -> None:
    target = PROFILE.mastering

    assert meets_delivery(measurements(integrated_lufs=-14.2, true_peak_dbtp=-1.2), target)
    assert not meets_delivery(measurements(integrated_lufs=-16.0), target)
    assert not meets_delivery(measurements(true_peak_dbtp=0.5), target)
    assert not meets_delivery(measurements(integrated_lufs=None), target)


def test_verification_states_each_target_as_met_or_not() -> None:
    good = {c.name: c.passed for c in verify_output(measurements(), measurements(), PROFILE)}
    bad = {
        c.name: c.passed
        for c in verify_output(
            measurements(),
            measurements(
                integrated_lufs=-20.0, true_peak_dbtp=1.0, clipping_ratio=0.05, duration=11.0
            ),
            PROFILE,
        )
    }

    assert all(good.values())
    assert not any(bad.values())
    assert set(good) == {
        "loudness_on_target",
        "true_peak_below_ceiling",
        "no_added_clipping",
        "timing_preserved",
    }


def test_measurements_refuse_non_finite_and_impossible_values() -> None:
    with pytest.raises(InvariantViolation):
        measurements(snr_db=float("nan"))
    with pytest.raises(InvariantViolation):
        measurements(clipping_ratio=1.5)
    with pytest.raises(InvariantViolation):
        measurements(duration=0.0)


def test_measurements_round_trip_through_json() -> None:
    original = measurements(snr_db=17.5, hum_hz=60.0, hum_prominence_db=20.0)

    assert QualityMeasurements.from_json_value(original.to_json_value()) == original
