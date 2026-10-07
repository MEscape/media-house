"""Processing settings: THE home of every tunable audio-engine value.

Nothing else in the module contains a tuning number. Every field is a stable, deterministic
default meant to be tuned later (profiles today, UI settings tomorrow, per-speaker calibration
after that). Each settings group says what drives the decision and what a stage guard demands.

Classification of the values: all are *user-configurable engine parameters* with conservative
defaults. Thresholds say WHEN a stage acts (clean audio gets no processing); strengths say HOW
MUCH; ``max_*``/``min_*`` guard fields say when a result counts as a regression and is reverted.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

from media_house.modules.audio_improvement.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvariantViolation(message)


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


@dataclass(frozen=True, slots=True)
class ClippingSettings:
    """Restore only when clipping is measurable; severe clipping is reported, not promised."""

    #: Share of samples in clipped runs that triggers restoration.
    min_ratio: float = 0.0005
    #: At or above this share restoration is unreliable: the result carries a warning.
    severe_ratio: float = 0.01

    def __post_init__(self) -> None:
        _require(
            0.0 < self.min_ratio < self.severe_ratio <= 1.0,
            "Clipping ratios must satisfy 0 < min < severe <= 1",
        )


@dataclass(frozen=True, slots=True)
class NoiseSettings:
    """Noise reduction, scaled by how far the recording is from a clean signal-to-noise ratio."""

    #: Recordings at or above this SNR (dB) need no denoising (unless hum is present).
    target_snr_db: float = 35.0
    #: SNR deficit (dB below target) that maps to ``max_strength``.
    full_strength_deficit_db: float = 25.0
    min_strength: float = 0.3
    max_strength: float = 0.9
    #: Hum line this many dB above its surroundings is notched out.
    hum_trigger_db: float = 12.0
    #: Guard: the stage must improve SNR by at least this much (dB) or it is reverted.
    min_snr_gain_db: float = 1.0
    #: Guard: the speech level may not drop by more than this (dB): over-cleaning.
    max_speech_loss_db: float = 2.0

    def __post_init__(self) -> None:
        _require(
            0.0 < self.min_strength <= self.max_strength <= 1.0,
            "Noise strengths must satisfy 0 < min <= max <= 1",
        )
        _require(
            self.full_strength_deficit_db > 0 and self.hum_trigger_db > 0,
            "Noise thresholds must be positive",
        )
        _require(
            self.min_snr_gain_db >= 0 and self.max_speech_loss_db >= 0,
            "Noise guards must not be negative",
        )


@dataclass(frozen=True, slots=True)
class DereverbSettings:
    """Late-reverberation suppression, only for clearly reverberant rooms."""

    #: Reverberation time (s) above which the room counts as problematic.
    min_rt60: float = 0.5
    #: Reverberation time (s) that maps to ``max_strength``.
    full_strength_rt60: float = 1.2
    min_strength: float = 0.3
    max_strength: float = 0.8
    #: Guard: the estimated reverberation time must fall by at least this share (0..1).
    min_rt60_reduction: float = 0.1
    max_speech_loss_db: float = 3.0

    def __post_init__(self) -> None:
        _require(
            0.0 < self.min_rt60 < self.full_strength_rt60,
            "Dereverb RT60 range must satisfy 0 < min < full",
        )
        _require(
            0.0 < self.min_strength <= self.max_strength <= 1.0,
            "Dereverb strengths must satisfy 0 < min <= max <= 1",
        )
        _require(
            0.0 <= self.min_rt60_reduction < 1.0 and self.max_speech_loss_db >= 0,
            "Dereverb guards out of range",
        )


@dataclass(frozen=True, slots=True)
class EqSettings:
    """Corrective EQ only: each band is touched when the speech balance is measurably off."""

    highpass_hz: float = 80.0
    #: Rumble below 70 Hz relative to the speech body (dB); above this the high-pass is applied.
    #: Clean speech measures about -34.
    rumble_trigger_db: float = -26.0
    mud_center_hz: float = 300.0
    #: Clean speech measures about +3.
    mud_trigger_db: float = 8.0
    mud_max_cut_db: float = 4.0
    harshness_center_hz: float = 3500.0
    #: Clean speech measures about -19.
    harshness_trigger_db: float = -10.0
    harshness_max_cut_db: float = 3.0
    #: Share of the measured excess that is corrected (0.5 = half of it): natural character stays.
    correction_ratio: float = 0.5
    #: Guard: the speech level may not move by more than this (dB).
    max_level_change_db: float = 3.0

    def __post_init__(self) -> None:
        _require(
            _finite(self.highpass_hz, self.mud_center_hz, self.harshness_center_hz)
            and self.highpass_hz > 0,
            "EQ frequencies must be positive",
        )
        _require(0.0 < self.correction_ratio <= 1.0, "EQ correction ratio must be in (0, 1]")
        _require(
            self.mud_max_cut_db >= 0 and self.harshness_max_cut_db >= 0,
            "EQ cuts must not be negative",
        )


@dataclass(frozen=True, slots=True)
class DynamicsSettings:
    """Gentle compression for uneven speech; already even delivery is left untouched."""

    #: Spread of the speech frame levels (dB, quiet tenth to loudest 5%) above which compression
    #: is applied. Clean, naturally delivered speech measures about 30 dB (calibrated on a real
    #: recording); only clearly uneven delivery should cross this.
    dynamics_trigger_db: float = 36.0
    ratio: float = 2.0
    attack_ms: float = 15.0
    release_ms: float = 150.0
    #: Threshold sits this far (dB) below the measured speech level.
    threshold_below_speech_db: float = 8.0
    #: Guard: the level spread must fall by at least this much (dB).
    min_dynamics_reduction_db: float = 1.0

    def __post_init__(self) -> None:
        _require(
            self.ratio >= 1.0 and self.attack_ms > 0 and self.release_ms > 0,
            "Compressor ratio/times out of range",
        )
        _require(
            self.threshold_below_speech_db >= 0 and self.min_dynamics_reduction_db >= 0,
            "Compressor thresholds must not be negative",
        )


@dataclass(frozen=True, slots=True)
class DeEsserSettings:
    """Sibilance reduction scaled by how far the sibilant peaks exceed the trigger.

    Sibilance is measured as the 95th-percentile level of the 5-9 kHz band relative to the mean
    speech power (dB). Clean speech measures about -16 (calibrated on a real recording); harsh
    sibilance is several dB above that.
    """

    trigger_db: float = -9.0
    #: Excess (dB over trigger) that maps to full strength.
    full_strength_excess_db: float = 8.0
    min_intensity: float = 0.3
    max_intensity: float = 0.8
    #: Largest share of the sibilant band that may be removed (0..1): avoids a lisp.
    max_reduction: float = 0.5
    #: Guard: sibilance must fall by at least this much (dB).
    min_reduction_db: float = 0.5

    def __post_init__(self) -> None:
        _require(
            0.0 < self.min_intensity <= self.max_intensity <= 1.0,
            "De-esser intensities must satisfy 0 < min <= max <= 1",
        )
        _require(
            0.0 < self.max_reduction <= 1.0 and self.full_strength_excess_db > 0,
            "De-esser limits out of range",
        )
        _require(self.min_reduction_db >= 0, "De-esser guard must not be negative")


@dataclass(frozen=True, slots=True)
class MasteringSettings:
    """Final loudness (LUFS) and true-peak targets: the delivery standard of the profile."""

    target_lufs: float = -14.0
    true_peak_ceiling_dbtp: float = -1.0
    #: Integrated loudness within this distance (LU) of the target counts as on target.
    tolerance_lu: float = 0.5
    #: Largest gain change (dB) mastering may apply: protects against amplifying noise.
    max_gain_db: float = 24.0
    limiter_release_ms: float = 60.0
    #: Gain/limiter refinement passes (limiting lowers loudness, so one pass may undershoot).
    max_passes: int = 3
    #: Leave audio that is already on target and below the ceiling bit-exact.
    skip_if_compliant: bool = True

    def __post_init__(self) -> None:
        _require(
            -40.0 <= self.target_lufs <= -5.0, "Target loudness must be between -40 and -5 LUFS"
        )
        _require(
            -6.0 <= self.true_peak_ceiling_dbtp <= 0.0,
            "True-peak ceiling must be between -6 and 0 dBTP",
        )
        _require(
            self.tolerance_lu > 0 and self.max_gain_db > 0 and self.limiter_release_ms > 0,
            "Mastering tolerances must be positive",
        )
        _require(self.max_passes >= 1, "Mastering needs at least one pass")


@dataclass(frozen=True, slots=True)
class QualitySettings:
    """Guards that apply to every stage, plus verification of the final result."""

    #: Processing must not change the length by more than this (seconds).
    max_duration_error: float = 0.002
    #: A stage may not increase the clipped share by more than this.
    max_clipping_increase: float = 0.0002

    def __post_init__(self) -> None:
        _require(
            self.max_duration_error >= 0 and self.max_clipping_increase >= 0,
            "Quality guards must not be negative",
        )


@dataclass(frozen=True, slots=True)
class MixSettings:
    """Voice and music: music sits relative to the voice and ducks while the voice speaks."""

    #: Music loudness relative to the voice (LU); negative = quieter than the voice.
    music_offset_lu: float = -18.0
    #: Sidechain level (dBFS) of the voice above which the music is ducked.
    duck_threshold_db: float = -40.0
    #: Higher = deeper ducking while the voice is present.
    duck_ratio: float = 6.0
    duck_attack_ms: float = 20.0
    #: Slow release keeps the music from pumping between words and brings it back in pauses.
    duck_release_ms: float = 500.0
    #: The music fades out over this long at the end of the voice (seconds).
    music_fade_out_s: float = 2.0

    def __post_init__(self) -> None:
        _require(-40.0 <= self.music_offset_lu <= 0.0, "Music offset must be between -40 and 0 LU")
        _require(
            self.duck_ratio >= 1.0 and self.duck_attack_ms > 0 and self.duck_release_ms > 0,
            "Ducking parameters out of range",
        )
        _require(
            self.duck_threshold_db < 0 and self.music_fade_out_s >= 0,
            "Ducking threshold/fade out of range",
        )


@dataclass(frozen=True, slots=True)
class AudioProfile:
    """The resolved processing configuration: built-in profile + user overrides, validated."""

    name: str = "youtube"
    clipping: ClippingSettings = field(default_factory=ClippingSettings)
    noise: NoiseSettings = field(default_factory=NoiseSettings)
    dereverb: DereverbSettings = field(default_factory=DereverbSettings)
    eq: EqSettings = field(default_factory=EqSettings)
    dynamics: DynamicsSettings = field(default_factory=DynamicsSettings)
    de_ess: DeEsserSettings = field(default_factory=DeEsserSettings)
    mastering: MasteringSettings = field(default_factory=MasteringSettings)
    quality: QualitySettings = field(default_factory=QualitySettings)
    mix: MixSettings = field(default_factory=MixSettings)

    def __post_init__(self) -> None:
        _require(bool(self.name.strip()), "Profile name must not be empty")

    def to_config(self) -> dict[str, JsonValue]:
        """Every setting (the processing identity): a new field can never be left out."""
        encoded = _encode(self)
        assert isinstance(encoded, dict)  # noqa: S101  # a dataclass always encodes to a mapping
        return encoded


def _encode(value: Any) -> JsonValue:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    raise TypeError(f"cannot encode {type(value).__name__}")
