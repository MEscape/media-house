"""Re-check: judge a stage's result against its input, and the final result against the profile.

Enhancement engines are never trusted blindly. After every stage the output is re-measured and
``stage_regression`` says whether it made the signal worse (the stage is then reverted); after
mastering ``verify_output`` states which delivery targets were actually met.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.settings import AudioProfile, MasteringSettings
from media_house.modules.audio_improvement.domain.values import Parameter, ProcessingStage

#: A hum line must fall by at least this much (dB) for a notch-only pass to count as working.
_MIN_HUM_REDUCTION_DB = 3.0
#: Sample-domain slack when comparing the true peak of the result with the ceiling (dB).
_PEAK_SLACK_DB = 0.1


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str


def meets_delivery(m: QualityMeasurements, target: MasteringSettings) -> bool:
    """Is the measured loudness on target (within tolerance) and the true peak under the ceiling?"""
    return (
        m.integrated_lufs is not None
        and m.true_peak_dbtp is not None
        and abs(m.integrated_lufs - target.target_lufs) <= target.tolerance_lu
        and m.true_peak_dbtp <= target.true_peak_ceiling_dbtp + _PEAK_SLACK_DB
    )


def _speech_loss(before: QualityMeasurements, after: QualityMeasurements) -> float | None:
    if before.speech_level_dbfs is None or after.speech_level_dbfs is None:
        return None
    return before.speech_level_dbfs - after.speech_level_dbfs


def stage_regression(
    stage: ProcessingStage,
    before: QualityMeasurements,
    after: QualityMeasurements,
    profile: AudioProfile,
    params: Mapping[str, Parameter],
) -> str | None:
    """Why ``after`` is worse than ``before`` for this stage, or ``None`` if it is acceptable.

    Evidence that cannot be measured on either side never counts against a stage.
    """
    guards = profile.quality
    if abs(after.duration - before.duration) > guards.max_duration_error:
        return f"changed the duration by {after.duration - before.duration:+.4f} s"
    if (
        stage is not ProcessingStage.CLIPPING_REPAIR
        and after.clipping_ratio > before.clipping_ratio + guards.max_clipping_increase
    ):
        return f"increased clipping from {before.clipping_ratio:.3%} to {after.clipping_ratio:.3%}"
    return _specific(stage, before, after, profile, params)


def _specific(
    stage: ProcessingStage,
    before: QualityMeasurements,
    after: QualityMeasurements,
    profile: AudioProfile,
    params: Mapping[str, Parameter],
) -> str | None:
    loss = _speech_loss(before, after)
    match stage:
        case ProcessingStage.CLIPPING_REPAIR:
            if after.clipping_ratio >= before.clipping_ratio:
                return "did not reduce the clipped share"
        case ProcessingStage.NOISE_REDUCTION:
            noise = profile.noise
            if loss is not None and loss > noise.max_speech_loss_db:
                return f"lowered the speech by {loss:.1f} dB (over-cleaning)"
            if params.get("broadband") and before.snr_db is not None and after.snr_db is not None:
                if after.snr_db - before.snr_db < noise.min_snr_gain_db:
                    return f"improved SNR by only {after.snr_db - before.snr_db:.1f} dB"
            elif not params.get("broadband") and before.hum_prominence_db is not None:
                remaining = after.hum_prominence_db if after.hum_hz is not None else 0.0
                if before.hum_prominence_db - (remaining or 0.0) < _MIN_HUM_REDUCTION_DB:
                    return "did not remove the hum"
        case ProcessingStage.DEREVERBERATION:
            reverb = profile.dereverb
            if loss is not None and loss > reverb.max_speech_loss_db:
                return f"lowered the speech by {loss:.1f} dB"
            if before.reverb_rt60 is not None and after.reverb_rt60 is not None:
                reduction = 1.0 - after.reverb_rt60 / before.reverb_rt60
                if reduction < reverb.min_rt60_reduction:
                    return f"reduced the reverberation time by only {reduction:.0%}"
        case ProcessingStage.EQUALIZATION:
            if loss is not None and abs(loss) > profile.eq.max_level_change_db:
                return f"moved the speech level by {-loss:+.1f} dB"
        case ProcessingStage.DYNAMICS:
            if before.speech_dynamics_db is not None and after.speech_dynamics_db is not None:
                gain = before.speech_dynamics_db - after.speech_dynamics_db
                if gain < profile.dynamics.min_dynamics_reduction_db:
                    return f"evened the speech level by only {gain:.1f} dB"
        case ProcessingStage.DE_ESSING:
            if before.sibilance_peak_db is not None and after.sibilance_peak_db is not None:
                gain = before.sibilance_peak_db - after.sibilance_peak_db
                if gain < profile.de_ess.min_reduction_db:
                    return f"reduced sibilance by only {gain:.1f} dB"
        case _:
            pass
    return None


def verify_output(
    before: QualityMeasurements,
    after: QualityMeasurements,
    profile: AudioProfile,
) -> tuple[Check, ...]:
    """The delivery targets and integrity facts of the final result, each stated as met or not."""
    target = profile.mastering
    checks: list[Check] = []
    lufs = after.integrated_lufs
    checks.append(
        Check(
            "loudness_on_target",
            lufs is not None and abs(lufs - target.target_lufs) <= target.tolerance_lu,
            "not measurable (silent or too short)"
            if lufs is None
            else f"{lufs:.1f} LUFS, target {target.target_lufs:.1f} ±{target.tolerance_lu:.1f}",
        )
    )
    peak = after.true_peak_dbtp
    checks.append(
        Check(
            "true_peak_below_ceiling",
            peak is not None and peak <= target.true_peak_ceiling_dbtp + _PEAK_SLACK_DB,
            "not measurable"
            if peak is None
            else f"{peak:.1f} dBTP, ceiling {target.true_peak_ceiling_dbtp:.1f}",
        )
    )
    checks.append(
        Check(
            "no_added_clipping",
            after.clipping_ratio <= before.clipping_ratio + profile.quality.max_clipping_increase,
            f"{before.clipping_ratio:.3%} -> {after.clipping_ratio:.3%}",
        )
    )
    checks.append(
        Check(
            "timing_preserved",
            abs(after.duration - before.duration) <= profile.quality.max_duration_error,
            f"{before.duration:.4f} s -> {after.duration:.4f} s",
        )
    )
    return tuple(checks)
