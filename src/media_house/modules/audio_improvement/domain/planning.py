"""Decide: from measurements and a profile to the stages that are worth running.

Pure and deterministic. Planning knows WHAT to do and how strongly, in engine-neutral terms
(strengths, dB, Hz, ratios); it never knows an engine, a filter name or a file. Clean audio
therefore gets an empty plan: every stage needs measured evidence of a problem.
"""

from dataclasses import dataclass, field

from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.settings import AudioProfile
from media_house.modules.audio_improvement.domain.values import (
    ENHANCEMENT_ORDER,
    Parameter,
    ProcessingStage,
)


@dataclass(frozen=True, slots=True)
class PlannedStage:
    stage: ProcessingStage
    apply: bool
    #: The evidence behind the decision, in words (kept in the provenance).
    reason: str
    params: dict[str, Parameter] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessingPlan:
    #: One entry per enhancement stage, in processing order.
    stages: tuple[PlannedStage, ...]
    warnings: tuple[str, ...] = ()

    @property
    def planned(self) -> tuple[PlannedStage, ...]:
        return tuple(s for s in self.stages if s.apply)


def _scaled(value: float, low: float, high: float, minimum: float, maximum: float) -> float:
    """``value`` mapped from ``[low, high]`` onto ``[minimum, maximum]`` (clamped)."""
    share = min(1.0, max(0.0, (value - low) / (high - low)))
    return minimum + (maximum - minimum) * share


def plan_processing(m: QualityMeasurements, profile: AudioProfile) -> ProcessingPlan:
    deciders = {
        ProcessingStage.CLIPPING_REPAIR: _clipping,
        ProcessingStage.NOISE_REDUCTION: _noise,
        ProcessingStage.DEREVERBERATION: _reverb,
        ProcessingStage.EQUALIZATION: _eq,
        ProcessingStage.DYNAMICS: _dynamics,
        ProcessingStage.DE_ESSING: _de_ess,
    }
    stages = tuple(deciders[stage](m, profile) for stage in ENHANCEMENT_ORDER)
    warnings: list[str] = []
    if m.clipping_ratio >= profile.clipping.severe_ratio:
        warnings.append(
            f"severe clipping ({m.clipping_ratio:.1%} of samples): restoration is limited and "
            "the original distortion may remain audible"
        )
    if m.speech_ratio == 0.0:
        warnings.append("no speech detected: speech-specific stages were not applied")
    return ProcessingPlan(stages, tuple(warnings))


def _skip(stage: ProcessingStage, reason: str) -> PlannedStage:
    return PlannedStage(stage, False, reason)


def _clipping(m: QualityMeasurements, profile: AudioProfile) -> PlannedStage:
    stage = ProcessingStage.CLIPPING_REPAIR
    if m.clipping_ratio < profile.clipping.min_ratio:
        return _skip(stage, f"no measurable clipping ({m.clipping_ratio:.3%})")
    return PlannedStage(stage, True, f"{m.clipping_ratio:.2%} of samples are clipped")


def _noise(m: QualityMeasurements, profile: AudioProfile) -> PlannedStage:
    stage, cfg = ProcessingStage.NOISE_REDUCTION, profile.noise
    hum = m.hum_hz is not None and (m.hum_prominence_db or 0.0) >= cfg.hum_trigger_db
    noisy = m.snr_db is not None and m.snr_db < cfg.target_snr_db
    if not noisy and not hum:
        why = (
            "not enough speech/noise evidence"
            if m.snr_db is None
            else f"clean (SNR {m.snr_db:.1f} dB)"
        )
        return _skip(stage, why)
    params: dict[str, Parameter] = {"broadband": noisy}
    reasons: list[str] = []
    if noisy and m.snr_db is not None:
        deficit = cfg.target_snr_db - m.snr_db
        params["strength"] = _scaled(
            deficit, 0.0, cfg.full_strength_deficit_db, cfg.min_strength, cfg.max_strength
        )
        if m.noise_floor_dbfs is not None:
            params["noise_floor_dbfs"] = m.noise_floor_dbfs
        reasons.append(f"SNR {m.snr_db:.1f} dB is below {cfg.target_snr_db:.0f} dB")
    if hum and m.hum_hz is not None:
        params["hum_hz"] = m.hum_hz
        reasons.append(
            f"{m.hum_hz:.0f} Hz hum ({m.hum_prominence_db:.0f} dB above its surroundings)"
        )
    return PlannedStage(stage, True, "; ".join(reasons), params)


def _reverb(m: QualityMeasurements, profile: AudioProfile) -> PlannedStage:
    stage, cfg = ProcessingStage.DEREVERBERATION, profile.dereverb
    if m.reverb_rt60 is None:
        return _skip(stage, "reverberation not measurable")
    if m.reverb_rt60 <= cfg.min_rt60:
        return _skip(stage, f"dry enough (RT60 {m.reverb_rt60:.2f} s)")
    strength = _scaled(
        m.reverb_rt60, cfg.min_rt60, cfg.full_strength_rt60, cfg.min_strength, cfg.max_strength
    )
    return PlannedStage(
        stage, True, f"RT60 {m.reverb_rt60:.2f} s", {"strength": strength, "rt60": m.reverb_rt60}
    )


def _eq(m: QualityMeasurements, profile: AudioProfile) -> PlannedStage:
    stage, cfg = ProcessingStage.EQUALIZATION, profile.eq
    params: dict[str, Parameter] = {}
    reasons: list[str] = []
    if m.rumble_db is not None and m.rumble_db > cfg.rumble_trigger_db:
        params["highpass_hz"] = cfg.highpass_hz
        reasons.append(f"rumble {m.rumble_db:.0f} dB below the speech body is too strong")
    if m.mud_db is not None and m.mud_db > cfg.mud_trigger_db:
        cut = min(cfg.mud_max_cut_db, (m.mud_db - cfg.mud_trigger_db) * cfg.correction_ratio)
        params["mud_center_hz"], params["mud_gain_db"] = cfg.mud_center_hz, -cut
        reasons.append(f"muddy low mids (+{m.mud_db:.1f} dB)")
    if m.harshness_db is not None and m.harshness_db > cfg.harshness_trigger_db:
        cut = min(
            cfg.harshness_max_cut_db,
            (m.harshness_db - cfg.harshness_trigger_db) * cfg.correction_ratio,
        )
        params["harshness_center_hz"], params["harshness_gain_db"] = cfg.harshness_center_hz, -cut
        reasons.append(f"harsh upper mids (+{m.harshness_db:.1f} dB)")
    if not params:
        return _skip(stage, "spectral balance within range")
    return PlannedStage(stage, True, "; ".join(reasons), params)


def _dynamics(m: QualityMeasurements, profile: AudioProfile) -> PlannedStage:
    stage, cfg = ProcessingStage.DYNAMICS, profile.dynamics
    if m.speech_dynamics_db is None or m.speech_level_dbfs is None:
        return _skip(stage, "dynamics not measurable")
    if m.speech_dynamics_db <= cfg.dynamics_trigger_db:
        return _skip(stage, f"already even (level spread {m.speech_dynamics_db:.1f} dB)")
    return PlannedStage(
        stage,
        True,
        f"uneven speech (level spread {m.speech_dynamics_db:.1f} dB)",
        {
            "ratio": cfg.ratio,
            "attack_ms": cfg.attack_ms,
            "release_ms": cfg.release_ms,
            "threshold_dbfs": m.speech_level_dbfs - cfg.threshold_below_speech_db,
        },
    )


def _de_ess(m: QualityMeasurements, profile: AudioProfile) -> PlannedStage:
    stage, cfg = ProcessingStage.DE_ESSING, profile.de_ess
    if m.sibilance_peak_db is None:
        return _skip(stage, "sibilance not measurable")
    if m.sibilance_peak_db <= cfg.trigger_db:
        return _skip(stage, f"sibilance within range ({m.sibilance_peak_db:.1f} dB)")
    intensity = _scaled(
        m.sibilance_peak_db - cfg.trigger_db,
        0.0,
        cfg.full_strength_excess_db,
        cfg.min_intensity,
        cfg.max_intensity,
    )
    return PlannedStage(
        stage,
        True,
        f"sibilant-band peaks at {m.sibilance_peak_db:.1f} dB relative to the mean speech power",
        {"intensity": intensity, "max_reduction": cfg.max_reduction},
    )
