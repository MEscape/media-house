"""Processing settings: THE home of every tunable video-engine value.

Nothing else in the module contains a tuning number. Every field is a stable, deterministic
default meant to be tuned later (profiles today, a settings UI tomorrow, calibration against
reference footage after that). ``trigger``/``deadband`` values say WHEN a correction acts (good
footage gets none), ``strength`` says HOW MUCH of the measured error is corrected, ``max_*``
values bound the correction, and ``guard_*`` values say when a result counts as a regression
and the stage is left out.

Adaptive processing therefore decides WITHIN these bounds: two clips of the same kind cannot
end up with different looks, only with different amounts of the same correction.
"""

import math
from dataclasses import dataclass, field

from media_house.modules.video_improvement.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvariantViolation(message)


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


@dataclass(frozen=True, slots=True)
class ColorSettings:
    """Exposure, white balance, tone and saturation, all in the colour-managed transform."""

    enabled: bool = True
    # --- exposure (stops of linear gain) -------------------------------------------------------
    #: Median scene luminance (linear, 0.18 = mid grey) the footage is brought towards.
    target_median: float = 0.18
    exposure_deadband_stops: float = 0.30
    exposure_strength: float = 0.8
    max_exposure_stops: float = 1.5
    #: Brightening never pushes the 99th percentile of linear luminance past this.
    highlight_headroom: float = 0.90
    #: With a highlight roll-off the shoulder absorbs highlights this many times above
    #: the headroom, so dark footage can be brightened although it has bright spots.
    rolloff_absorb: float = 1.3
    #: Footage still darker than the target after exposure is lifted with a gamma curve
    #: (white stays white, shadows and midtones rise); gamma never goes below this.
    min_gamma: float = 0.80
    lift_strength: float = 0.8
    #: Darkening only when highlights are blown: at least this share of pixels at the signal top.
    overexposed_clip_share: float = 0.02
    # --- white balance ---------------------------------------------------------------------------
    white_balance_strength: float = 0.6
    white_balance_deadband: float = 0.03
    max_white_balance_shift: float = 0.08
    #: Share of near-neutral midtone pixels needed to trust the colour-cast estimate.
    min_neutral_share: float = 0.05
    # --- tone ------------------------------------------------------------------------------
    #: User contrast about mid grey (1 = untouched), on top of any automatic correction.
    contrast: float = 1.0
    #: Footage whose luma (p1..p99) spans less than this is flat; contrast is raised to reach it.
    min_tonal_range: float = 0.55
    max_contrast_boost: float = 1.25
    #: Highlights roll off softly above this display level when they would otherwise clip.
    highlight_knee: float = 0.80
    #: Roll-off starts when the 99th percentile of linear luminance exceeds this.
    rolloff_trigger: float = 0.92
    #: Blacks lifted above this display luma (p1) are brought down towards it.
    black_point_trigger: float = 0.07
    max_black_point_shift: float = 0.04
    # --- saturation ------------------------------------------------------------------------
    #: Mean saturation of the finished picture is steered into this band, never beyond it.
    saturation_min: float = 0.15
    saturation_max: float = 0.50
    saturation_strength: float = 0.7
    max_saturation_change: float = 0.25
    #: 0 = saturation scales every colour equally, 1 = already vivid colours change less.
    vibrance: float = 0.5
    # --- guards: the finished output must satisfy these or the stage is left out -----------
    guard_max_clip_increase: float = 0.01
    guard_max_crush_increase: float = 0.02
    #: Predicted and measured median luma of the output may differ by this much.
    guard_max_prediction_error: float = 0.03
    #: The grade may raise the measured luma noise at most this many times (lifted
    #: shadows amplify noise).
    guard_max_noise_gain: float = 1.8

    def __post_init__(self) -> None:
        _require(0.02 <= self.target_median <= 0.5, "target_median must be 0.02-0.5")
        _require(
            _finite(self.exposure_deadband_stops, self.max_exposure_stops)
            and 0 <= self.exposure_deadband_stops < self.max_exposure_stops <= 4,
            "exposure deadband must be below the 0-4 stop maximum",
        )
        for name in (
            "exposure_strength",
            "white_balance_strength",
            "saturation_strength",
            "vibrance",
        ):
            _require(0.0 <= getattr(self, name) <= 1.0, f"{name} must be 0-1")
        _require(0.3 <= self.highlight_headroom <= 1.5, "highlight_headroom must be 0.3-1.5")
        _require(1.0 <= self.rolloff_absorb <= 4.0, "rolloff_absorb must be 1-4")
        _require(0.5 <= self.min_gamma <= 1.0, "min_gamma must be 0.5-1")
        _require(0.0 <= self.lift_strength <= 1.0, "lift_strength must be 0-1")
        _require(1.0 <= self.guard_max_noise_gain <= 10.0, "guard_max_noise_gain must be 1-10")
        _require(0.0 < self.overexposed_clip_share < 1.0, "overexposed_clip_share must be 0-1")
        _require(
            0 <= self.white_balance_deadband < self.max_white_balance_shift <= 0.3,
            "white balance deadband must be below the 0-0.3 maximum shift",
        )
        _require(0.0 < self.min_neutral_share <= 1.0, "min_neutral_share must be 0-1")
        _require(0.5 <= self.contrast <= 2.0, "contrast must be 0.5-2")
        _require(0.2 <= self.min_tonal_range <= 1.0, "min_tonal_range must be 0.2-1")
        _require(1.0 <= self.max_contrast_boost <= 2.0, "max_contrast_boost must be 1-2")
        _require(0.5 <= self.highlight_knee < 1.0, "highlight_knee must be 0.5-1")
        _require(0.3 <= self.rolloff_trigger <= 2.0, "rolloff_trigger must be 0.3-2")
        _require(
            0.0 <= self.black_point_trigger <= 0.3 and 0.0 <= self.max_black_point_shift <= 0.1,
            "black point values out of range",
        )
        _require(
            0.0 < self.saturation_min < self.saturation_max <= 1.0,
            "saturation band must satisfy 0 < min < max <= 1",
        )
        _require(0.0 <= self.max_saturation_change <= 1.0, "max_saturation_change must be 0-1")
        _require(
            _finite(
                self.guard_max_clip_increase,
                self.guard_max_crush_increase,
                self.guard_max_prediction_error,
            )
            and min(
                self.guard_max_clip_increase,
                self.guard_max_crush_increase,
                self.guard_max_prediction_error,
            )
            >= 0,
            "guards must not be negative",
        )


@dataclass(frozen=True, slots=True)
class DenoiseSettings:
    """Spatio-temporal denoising, scaled by measured noise; clean footage is left alone."""

    enabled: bool = True
    #: Luma noise (sigma, 0-1 signal, measured on a native-resolution crop) below this: no denoise.
    trigger_sigma: float = 0.006
    #: Noise at which the strength reaches ``max_strength``.
    full_strength_sigma: float = 0.025
    min_strength: float = 0.25
    max_strength: float = 0.8
    #: Guard: the finished picture must keep this share of the source's measured sharpness.
    guard_min_sharpness_retention: float = 0.6

    def __post_init__(self) -> None:
        _require(
            0.0 < self.trigger_sigma < self.full_strength_sigma <= 0.2,
            "noise thresholds must satisfy 0 < trigger < full <= 0.2",
        )
        _require(
            0.0 < self.min_strength <= self.max_strength <= 1.0,
            "denoise strengths must satisfy 0 < min <= max <= 1",
        )
        _require(
            0.0 < self.guard_min_sharpness_retention <= 1.0,
            "guard_min_sharpness_retention must be 0-1",
        )


@dataclass(frozen=True, slots=True)
class SharpenSettings:
    """Sharpening only for soft footage; noisy or already crisp footage is never sharpened."""

    enabled: bool = True
    #: Measured sharpness below this counts as soft.
    soft_below: float = 0.08
    #: Footage noisier than this (sigma) is not sharpened: it would amplify the noise.
    max_noise_sigma: float = 0.008
    min_amount: float = 0.2
    max_amount: float = 0.7

    def __post_init__(self) -> None:
        _require(self.soft_below > 0 and self.max_noise_sigma > 0, "sharpen thresholds must be > 0")
        _require(
            0.0 < self.min_amount <= self.max_amount <= 1.5,
            "sharpen amounts must satisfy 0 < min <= max <= 1.5",
        )


@dataclass(frozen=True, slots=True)
class LookSettings:
    """An optional ``.cube`` 3D LUT applied last, in the Rec.709 delivery space.

    It is traceable: its content hash is part of the fingerprint and of the provenance. It is a
    deliberate, configured choice and is never applied implicitly.
    """

    lut_path: str = ""
    strength: float = 1.0

    def __post_init__(self) -> None:
        _require(0.0 <= self.strength <= 1.0, "look strength must be 0-1")
        _require("\x00" not in self.lut_path, "invalid LUT path")


@dataclass(frozen=True, slots=True)
class OutputSettings:
    """The delivery encode. Audio and metadata are copied, never re-encoded."""

    #: ``h264``, ``h265`` or ``prores`` (editing intermediate).
    codec: str = "h264"
    #: Constant-quality target of the software encoders (lower = better and larger).
    crf: int = 16
    preset: str = "veryfast"
    #: 0 keeps the source's bit depth.
    bit_depth: int = 0

    @property
    def suffix(self) -> str:
        """File extension of the delivery container."""
        return ".mov" if self.codec == "prores" else ".mp4"

    def __post_init__(self) -> None:
        _require(self.codec in {"h264", "h265", "prores"}, "codec must be h264, h265 or prores")
        _require(0 <= self.crf <= 51, "crf must be 0-51")
        _require(self.bit_depth in {0, 8, 10}, "bit_depth must be 0, 8 or 10")
        _require(
            self.preset
            in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"},
            "unknown encoder preset",
        )


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """How the work is done. ``hardware`` never changes WHAT is done and is not part of the
    fingerprint; the encoder that actually ran is (see ``VideoRenderer.encoder_for``)."""

    #: ``auto`` uses a GPU decoder/encoder when one is verified to work, else the CPU.
    hardware: str = "auto"
    #: Frames analysed per video (evenly spread).
    sample_count: int = 24
    #: Edge of the baked 3D LUT (65 keeps strong log curves smooth).
    lut_size: int = 65

    def __post_init__(self) -> None:
        _require(self.hardware in {"auto", "cpu"}, "hardware must be auto or cpu")
        _require(4 <= self.sample_count <= 240, "sample_count must be 4-240")
        _require(17 <= self.lut_size <= 129, "lut_size must be 17-129")


@dataclass(frozen=True, slots=True)
class ProcessingProfile:
    """How the footage is meant to look: the look-and-strength half of a configuration."""

    name: str = "natural"
    color: ColorSettings = field(default_factory=ColorSettings)
    denoise: DenoiseSettings = field(default_factory=DenoiseSettings)
    sharpen: SharpenSettings = field(default_factory=SharpenSettings)
    look: LookSettings = field(default_factory=LookSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)

    def to_config(self) -> dict[str, JsonValue]:
        """Everything that decides the result (execution speed knobs excluded)."""
        return {
            "name": self.name,
            "color": _flat(self.color),
            "denoise": _flat(self.denoise),
            "sharpen": _flat(self.sharpen),
            "look": {"strength": self.look.strength, "enabled": bool(self.look.lut_path)},
            "output": _flat(self.output),
            "lut_size": self.execution.lut_size,
            "sample_count": self.execution.sample_count,
        }


def _flat(settings: object) -> dict[str, JsonValue]:
    return {name: getattr(settings, name) for name in settings.__dataclass_fields__}  # type: ignore[attr-defined]
