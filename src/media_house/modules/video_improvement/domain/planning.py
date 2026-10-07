"""Decide what to do: measured facts -> processing decisions -> applied corrections.

Every operation is recorded with its status and the reason, so a result is explainable: what was
MEASURED, what was DECIDED (within the bounds of the profile) and what was APPLIED. The rule
that shapes everything here: if the footage is already good, do less. Each correction has a
deadband below which it does not act, and a maximum above which the profile does not allow it.

Planning happens in two steps because contrast and saturation are judged on the picture as it
will look, which needs the colour transform to be evaluated:

1. ``plan_processing``: exposure, white balance, black point, highlight roll-off, rendering of
   scene-referred footage, denoising, sharpening (from measurements of the SOURCE);
2. ``refine_plan``: contrast and saturation (from measurements PREDICTED for step 1's result).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from media_house.modules.video_improvement.domain.color import ColorSpec
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.settings import ProcessingProfile
from media_house.modules.video_improvement.domain.source import SourceProfile, VideoFacts
from media_house.modules.video_improvement.domain.values import (
    STAGE_ORDER,
    Parameter,
    ProcessingStage,
    StageStatus,
)

_EPSILON = 1e-4
#: A correction smaller than this is not worth a pass over every pixel.
_MIN_CONTRAST_CHANGE = 0.02
_MIN_SATURATION_CHANGE = 0.03
_MIN_SHIFT = 1e-3
_MIN_LIFT = 0.03


@dataclass(frozen=True, slots=True)
class PlannedOperation:
    stage: ProcessingStage
    name: str
    status: StageStatus
    reason: str
    parameters: Mapping[str, Parameter] = field(default_factory=dict)
    #: The measured numbers the decision rests on.
    measured: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ColorPlan:
    """Numbers of the colour transform, in the order they are applied (see color_science.py)."""

    input_color: ColorSpec
    #: Black point pulled down in the SOURCE encoding (display-referred footage only).
    black_point: float = 0.0
    exposure_stops: float = 0.0
    #: Linear white-balance gains (red, green, blue).
    gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    #: Gamma on linear light (below 1 lifts shadows and midtones, white is kept).
    gamma: float = 1.0
    #: Total contrast about mid grey (rendering of scene-referred footage included).
    contrast: float = 1.0
    #: Highlight roll-off knee (display level), or ``None`` for no roll-off.
    knee: float | None = None
    saturation: float = 1.0
    vibrance: float = 0.0
    #: Optional look LUT (``.cube``) applied last in the delivery space.
    look_lut: str = ""
    look_strength: float = 1.0

    @property
    def is_identity(self) -> bool:
        """Applying it would not change a pixel (so it is not applied at all)."""
        return (
            not self.input_color.scene_referred
            and abs(self.black_point) < _MIN_SHIFT
            and abs(self.exposure_stops) < _MIN_SHIFT
            and all(abs(g - 1.0) < _MIN_SHIFT for g in self.gains)
            and abs(self.gamma - 1.0) < _MIN_SHIFT
            and abs(self.contrast - 1.0) < _MIN_SHIFT
            and self.knee is None
            and abs(self.saturation - 1.0) < _MIN_SHIFT
            and not self.look_lut
        )


@dataclass(frozen=True, slots=True)
class DenoisePlan:
    #: 0-1; the renderer maps it to the denoiser's own scale.
    strength: float


@dataclass(frozen=True, slots=True)
class SharpenPlan:
    amount: float


@dataclass(frozen=True, slots=True)
class ProcessingPlan:
    operations: tuple[PlannedOperation, ...]
    color: ColorPlan | None
    denoise: DenoisePlan | None
    sharpen: SharpenPlan | None
    warnings: tuple[str, ...] = ()

    def applied(self, stage: ProcessingStage) -> bool:
        return any(o.stage is stage and o.status is StageStatus.APPLIED for o in self.operations)

    @property
    def stages_applied(self) -> tuple[ProcessingStage, ...]:
        return tuple(s for s in STAGE_ORDER if self.applied(s))

    def operation(self, name: str) -> PlannedOperation | None:
        return next((o for o in self.operations if o.name == name), None)

    def bypass(self, stage: ProcessingStage, reason: str) -> "ProcessingPlan":
        """The same plan with ``stage`` left out; its applied operations become ``bypassed``."""
        operations = tuple(
            replace(o, status=StageStatus.BYPASSED, reason=reason)
            if o.stage is stage and o.status is StageStatus.APPLIED
            else o
            for o in self.operations
        )
        return replace(
            self,
            operations=operations,
            color=None if stage is ProcessingStage.COLOR else self.color,
            denoise=None if stage is ProcessingStage.DENOISE else self.denoise,
            sharpen=None if stage is ProcessingStage.SHARPEN else self.sharpen,
        )


def _op(
    stage: ProcessingStage,
    name: str,
    applied: bool,
    reason: str,
    parameters: Mapping[str, Parameter] | None = None,
    measured: Mapping[str, float] | None = None,
) -> PlannedOperation:
    status = StageStatus.APPLIED if applied else StageStatus.SKIPPED
    return PlannedOperation(stage, name, status, reason, parameters or {}, measured or {})


def _lerp(low: float, high: float, fraction: float) -> float:
    return low + (high - low) * min(1.0, max(0.0, fraction))


# ------------------------------------------------------------------------------------------
# colour: exposure, white balance, black point, roll-off, rendering
# ------------------------------------------------------------------------------------------
def _exposure(m: SceneMeasurements, profile: ProcessingProfile) -> tuple[float, PlannedOperation]:
    c = profile.color
    measured = {
        "linear_p50": m.linear_p50,
        "linear_p99": m.linear_p99,
        "highlight_clip": m.highlight_clip,
    }
    off = math.log2(c.target_median / max(m.linear_p50, _EPSILON))
    stage = ProcessingStage.COLOR
    if off > c.exposure_deadband_stops:
        room = math.log2(c.highlight_headroom * c.rolloff_absorb / max(m.linear_p99, _EPSILON))
        stops = min(off, room, c.max_exposure_stops) * c.exposure_strength
        if stops < c.exposure_deadband_stops:
            return 0.0, _op(
                stage,
                "exposure",
                False,
                "dark, but the highlights leave no room to brighten",
                {},
                measured,
            )
        return stops, _op(
            stage,
            "exposure",
            True,
            "underexposed: brightened within the headroom of the highlights",
            {"stops": round(stops, 3)},
            measured,
        )
    if off < -c.exposure_deadband_stops and m.highlight_clip >= c.overexposed_clip_share:
        stops = -min(-off, c.max_exposure_stops) * c.exposure_strength
        return stops, _op(
            stage,
            "exposure",
            True,
            "overexposed with blown highlights: darkened",
            {"stops": round(stops, 3)},
            measured,
        )
    reason = (
        "bright, but the highlights are not blown: left as shot"
        if off < -c.exposure_deadband_stops
        else "median exposure within the deadband"
    )
    return 0.0, _op(stage, "exposure", False, reason, {}, measured)


def _lift(
    m: SceneMeasurements, stops: float, profile: ProcessingProfile
) -> tuple[float, PlannedOperation]:
    """A gamma lift for what exposure alone could not fix (a dark picture with bright spots)."""
    c = profile.color
    stage = ProcessingStage.COLOR
    median = m.linear_p50 * 2.0**stops
    measured = {"linear_p50_after_exposure": median}
    if median >= c.target_median * 2.0**-c.exposure_deadband_stops or stops < 0:
        return 1.0, _op(stage, "midtone_lift", False, "midtones are bright enough", {}, measured)
    wanted = math.log(c.target_median) / math.log(max(median, _EPSILON))
    gamma = 1.0 - (1.0 - max(c.min_gamma, wanted)) * c.lift_strength
    if 1.0 - gamma < _MIN_LIFT:
        return 1.0, _op(stage, "midtone_lift", False, "the lift would be negligible", {}, measured)
    return gamma, _op(
        stage,
        "midtone_lift",
        True,
        "dark midtones lifted with a gamma curve that keeps white and softens highlights",
        {"gamma": round(gamma, 3)},
        measured,
    )


def _white_balance(
    m: SceneMeasurements, profile: ProcessingProfile
) -> tuple[tuple[float, float, float], PlannedOperation]:
    c = profile.color
    stage = ProcessingStage.COLOR
    measured = {"neutral_share": m.neutral_share}
    if c.white_balance_strength <= 0:
        return (1.0, 1.0, 1.0), _op(
            stage, "white_balance", False, "disabled by the profile", {}, measured
        )
    if m.cast_red is None or m.cast_blue is None or m.neutral_share < c.min_neutral_share:
        return (1.0, 1.0, 1.0), _op(
            stage,
            "white_balance",
            False,
            "too few neutral pixels to judge a colour cast",
            {},
            measured,
        )
    measured |= {"cast_red": m.cast_red, "cast_blue": m.cast_blue}
    if max(abs(m.cast_red - 1.0), abs(m.cast_blue - 1.0)) <= c.white_balance_deadband:
        return (1.0, 1.0, 1.0), _op(stage, "white_balance", False, "no colour cast", {}, measured)

    def gain(cast: float) -> float:
        wanted = math.pow(1.0 / cast, c.white_balance_strength)
        return min(1.0 + c.max_white_balance_shift, max(1.0 - c.max_white_balance_shift, wanted))

    gains = (gain(m.cast_red), 1.0, gain(m.cast_blue))
    return gains, _op(
        stage,
        "white_balance",
        True,
        "colour cast corrected within the profile's limit",
        {"red_gain": round(gains[0], 4), "blue_gain": round(gains[2], 4)},
        measured,
    )


def _black_point(
    m: SceneMeasurements, profile: ProcessingProfile, scene_referred: bool
) -> tuple[float, PlannedOperation]:
    c = profile.color
    stage = ProcessingStage.COLOR
    measured = {"luma_p1": m.luma_p1}
    if scene_referred:
        return 0.0, _op(
            stage,
            "black_point",
            False,
            "set by the rendering of scene-referred footage",
            {},
            measured,
        )
    if m.luma_p1 <= c.black_point_trigger:
        return 0.0, _op(stage, "black_point", False, "blacks are deep enough", {}, measured)
    shift = min(m.luma_p1 - c.black_point_trigger / 2, c.max_black_point_shift)
    return shift, _op(
        stage, "black_point", True, "milky blacks pulled down", {"shift": round(shift, 4)}, measured
    )


def _plan_color(
    m: SceneMeasurements,
    source: SourceProfile,
    profile: ProcessingProfile,
    look_sha256: str,
) -> tuple[ColorPlan | None, list[PlannedOperation]]:
    stage = ProcessingStage.COLOR
    if not profile.color.enabled:
        return None, [_op(stage, "color", False, "disabled by the profile")]
    color = source.input_color
    if color.is_hdr:
        return None, [_op(stage, "color", False, "HDR footage is not graded")]
    if not color.known:
        return None, [_op(stage, "color", False, "input colour space unknown: not guessed")]

    scene = color.scene_referred
    c = profile.color
    black, black_op = _black_point(m, profile, scene)
    stops, exposure_op = _exposure(m, profile)
    gains, wb_op = _white_balance(m, profile)
    gamma, lift_op = _lift(m, stops, profile)
    operations = [black_op, exposure_op, lift_op, wb_op]

    contrast = c.contrast
    knee: float | None = None
    if scene:
        contrast *= source.rendering.contrast
        knee = min(source.rendering.shoulder_knee, c.highlight_knee)
        operations.append(
            _op(
                stage,
                "input_transform",
                True,
                f"{color} to the linear working space, rendered for display",
                {"contrast": round(contrast, 3), "knee": round(knee, 3)},
            )
        )
    else:
        p99_after = (m.linear_p99 * 2.0**stops) ** gamma
        blown = m.highlight_clip >= c.overexposed_clip_share
        if p99_after > c.rolloff_trigger or blown:
            knee = c.highlight_knee
        operations.append(
            _op(
                stage,
                "highlight_rolloff",
                knee is not None,
                "bright highlights rolled off softly instead of clipping"
                if knee is not None
                else "highlights have room",
                {"knee": knee} if knee is not None else {},
                {"linear_p99": m.linear_p99, "highlight_clip": m.highlight_clip},
            )
        )
    if abs(c.contrast - 1.0) >= _MIN_CONTRAST_CHANGE:
        operations.append(
            _op(stage, "contrast", True, "profile contrast", {"contrast": round(c.contrast, 3)})
        )

    look = bool(profile.look.lut_path)
    if look:
        operations.append(
            _op(
                stage,
                "look",
                True,
                "configured look LUT",
                {"strength": profile.look.strength, "sha256": look_sha256},
            )
        )
    plan = ColorPlan(
        input_color=color,
        black_point=black,
        exposure_stops=stops,
        gains=gains,
        gamma=gamma,
        contrast=contrast,
        knee=knee,
        vibrance=c.vibrance,
        look_lut=profile.look.lut_path,
        look_strength=profile.look.strength,
    )
    return plan, operations


# ------------------------------------------------------------------------------------------
# denoise and sharpen
# ------------------------------------------------------------------------------------------
def _plan_denoise(
    m: SceneMeasurements, facts: VideoFacts, profile: ProcessingProfile
) -> tuple[DenoisePlan | None, PlannedOperation]:
    d = profile.denoise
    stage = ProcessingStage.DENOISE
    measured = {"noise_sigma": m.noise_sigma}
    if not d.enabled:
        return None, _op(stage, "denoise", False, "disabled by the profile")
    if facts.interlaced:
        return None, _op(stage, "denoise", False, "interlaced footage: left alone", {}, measured)
    if m.noise_sigma < d.trigger_sigma:
        return None, _op(stage, "denoise", False, "signal is clean", {}, measured)
    fraction = (m.noise_sigma - d.trigger_sigma) / (d.full_strength_sigma - d.trigger_sigma)
    strength = _lerp(d.min_strength, d.max_strength, fraction)
    return DenoisePlan(strength), _op(
        stage,
        "denoise",
        True,
        "noise reduced in proportion to the measured noise",
        {"strength": round(strength, 3)},
        measured,
    )


def _plan_sharpen(
    m: SceneMeasurements, profile: ProcessingProfile
) -> tuple[SharpenPlan | None, PlannedOperation]:
    s = profile.sharpen
    stage = ProcessingStage.SHARPEN
    measured = {"sharpness": m.sharpness, "noise_sigma": m.noise_sigma}
    if not s.enabled:
        return None, _op(stage, "sharpen", False, "disabled by the profile")
    if m.sharpness >= s.soft_below:
        return None, _op(stage, "sharpen", False, "already crisp", {}, measured)
    if m.noise_sigma > s.max_noise_sigma:
        return None, _op(stage, "sharpen", False, "too noisy to sharpen", {}, measured)
    amount = _lerp(s.min_amount, s.max_amount, (s.soft_below - m.sharpness) / s.soft_below)
    return SharpenPlan(amount), _op(
        stage, "sharpen", True, "soft footage sharpened", {"amount": round(amount, 3)}, measured
    )


# ------------------------------------------------------------------------------------------
# public steps
# ------------------------------------------------------------------------------------------
def plan_processing(
    measurements: SceneMeasurements,
    facts: VideoFacts,
    source: SourceProfile,
    profile: ProcessingProfile,
    *,
    look_sha256: str = "",
) -> ProcessingPlan:
    """Step 1: decisions from the measured SOURCE."""
    color, operations = _plan_color(measurements, source, profile, look_sha256)
    denoise, denoise_op = _plan_denoise(measurements, facts, profile)
    sharpen, sharpen_op = _plan_sharpen(measurements, profile)
    warnings: list[str] = []
    if measurements.exposure_variation_stops > 1.0:
        warnings.append(
            f"exposure varies by {measurements.exposure_variation_stops:.1f} stops through "
            "the clip; a static correction cannot follow it"
        )
    return ProcessingPlan(
        (*operations, denoise_op, sharpen_op), color, denoise, sharpen, tuple(warnings)
    )


def refine_plan(
    plan: ProcessingPlan,
    predicted: SceneMeasurements,
    profile: ProcessingProfile,
) -> ProcessingPlan:
    """Step 2: contrast and saturation, judged on the picture as step 1 will render it. The
    result is final: a colour transform that changes nothing is dropped here."""
    if plan.color is None:
        return plan
    c = profile.color
    color = plan.color
    stage = ProcessingStage.COLOR
    operations = [o for o in plan.operations if o.name not in {"saturation"}]

    measured = {"tonal_range": predicted.tonal_range}
    if predicted.tonal_range < c.min_tonal_range:
        boost = min(
            c.max_contrast_boost, (c.min_tonal_range / max(predicted.tonal_range, _EPSILON)) ** 0.5
        )
        if boost - 1.0 >= _MIN_CONTRAST_CHANGE:
            color = replace(color, contrast=color.contrast * boost)
            operations = [o for o in operations if o.name != "contrast"]
            operations.append(
                _op(
                    stage,
                    "contrast",
                    True,
                    "flat picture: contrast raised to a normal range",
                    {"boost": round(boost, 3)},
                    measured,
                )
            )

    saturation_measured = {"mean_saturation": predicted.mean_saturation}
    current = predicted.mean_saturation
    target = None
    if current > c.saturation_max:
        target = c.saturation_max
    elif 0 < current < c.saturation_min:
        target = c.saturation_min
    if target is not None:
        factor = 1.0 + (target / current - 1.0) * c.saturation_strength
        factor = min(1.0 + c.max_saturation_change, max(1.0 - c.max_saturation_change, factor))
        if abs(factor - 1.0) >= _MIN_SATURATION_CHANGE:
            color = replace(color, saturation=factor)
            operations.append(
                _op(
                    stage,
                    "saturation",
                    True,
                    "oversaturated: reduced into the profile's band"
                    if factor < 1.0
                    else "washed out: raised into the profile's band",
                    {"factor": round(factor, 3)},
                    saturation_measured,
                )
            )
    if not any(o.name == "saturation" for o in operations):
        operations.append(
            _op(
                stage,
                "saturation",
                False,
                "saturation within the profile's band",
                {},
                saturation_measured,
            )
        )
    return _settle_color(replace(plan, operations=tuple(operations), color=color))


def _settle_color(plan: ProcessingPlan) -> ProcessingPlan:
    """A colour transform that changes nothing is dropped, so good footage is not re-encoded
    through a colour conversion for no reason."""
    if plan.color is None or not plan.color.is_identity:
        return plan
    operations = tuple(
        replace(o, status=StageStatus.SKIPPED, reason="nothing to correct")
        if o.stage is ProcessingStage.COLOR and o.status is StageStatus.APPLIED
        else o
        for o in plan.operations
    )
    return replace(plan, operations=operations, color=None)
