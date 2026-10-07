"""Measured quality of one audio signal: the shared input of planning, guards and verification.

Every value is MEASURED, never judged. ``None`` means "not measurable here" (no speech, too
short, no evidence), never zero. Levels are in dBFS / LUFS / dBTP, times in seconds, ratios in
0..1, band balances in dB (relative to the speech body of the same recording).
"""

import math
from dataclasses import dataclass, fields

from media_house.modules.audio_improvement.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class QualityMeasurements:
    duration: float
    sample_rate: int
    channels: int
    # loudness (EBU R128 / ITU-R BS.1770)
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None
    rms_dbfs: float | None
    # speech and noise
    speech_ratio: float
    speech_level_dbfs: float | None
    noise_floor_dbfs: float | None
    snr_db: float | None
    #: Mains hum frequency (50 or 60 Hz) if a prominent line was found.
    hum_hz: float | None
    hum_prominence_db: float | None
    #: Share (0..1) of the noise power above 4 kHz: high = hiss, low = rumble/hum.
    noise_hf_share: float | None
    # signal integrity
    #: Share (0..1) of samples inside flat-topped runs at the signal's peak.
    clipping_ratio: float
    #: Peak minus speech level (dB): informational; very spiky vs already compressed.
    crest_factor_db: float | None
    #: Spread (dB) of the speech frame levels, quiet tenth to loudest 5%: how much the voice
    #: level varies. Large = uneven delivery, small = already even.
    speech_dynamics_db: float | None
    # spectral balance of the speech (dB)
    sibilance_peak_db: float | None
    rumble_db: float | None
    mud_db: float | None
    harshness_db: float | None
    #: Blind reverberation time estimate (seconds); ``None`` without enough decay evidence.
    reverb_rt60: float | None

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.channels <= 0 or not self.duration > 0:
            raise InvariantViolation("Measurements need a positive duration, rate and channels")
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, float) and not math.isfinite(value):
                raise InvariantViolation(f"Measurement {field.name} must be finite or None")
        if not 0.0 <= self.speech_ratio <= 1.0 or not 0.0 <= self.clipping_ratio <= 1.0:
            raise InvariantViolation("Measured ratios must lie within [0, 1]")

    def to_json_value(self) -> dict[str, JsonValue]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_json_value(cls, value: dict[str, JsonValue]) -> "QualityMeasurements":
        """Raises ``KeyError``/``TypeError``/``InvariantViolation`` for a damaged document."""
        return cls(**{f.name: value[f.name] for f in fields(cls)})  # type: ignore[arg-type]
