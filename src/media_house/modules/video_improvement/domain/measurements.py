"""What is measured on sampled frames: the MEASURED FACTS that decisions are based on.

The same record describes the source (before), the predicted result and the real output (after),
so they can be compared directly. Luma values are of the signal as encoded (0-1, full range);
linear values are scene or display luminance after the input transform (0.18 = mid grey).
Resolution-dependent quantities (noise, sharpness) come from a native-resolution crop.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SceneMeasurements:
    frames: int
    #: Percentiles of encoded luma.
    luma_p1: float
    luma_p50: float
    luma_p99: float
    #: Percentiles of linear luminance in the working space.
    linear_p50: float
    linear_p99: float
    #: Share of pixels at the top and at the bottom of the signal range.
    highlight_clip: float
    shadow_crush: float
    #: Mean HSV saturation of non-dark pixels.
    mean_saturation: float
    #: Red/green and blue/green balance of near-neutral midtone pixels (1 = neutral), and how
    #: many such pixels there were; ``None`` when there were too few to say anything.
    cast_red: float | None
    cast_blue: float | None
    neutral_share: float
    #: Luma noise sigma and a sharpness index (native-resolution crop, signal 0-1).
    noise_sigma: float
    sharpness: float
    #: Spread of the per-frame median luminance, in stops (1 = a doubling).
    exposure_variation_stops: float

    def __post_init__(self) -> None:
        values = (
            self.luma_p1,
            self.luma_p50,
            self.luma_p99,
            self.linear_p50,
            self.linear_p99,
            self.highlight_clip,
            self.shadow_crush,
            self.mean_saturation,
            self.neutral_share,
            self.noise_sigma,
            self.sharpness,
            self.exposure_variation_stops,
        )
        if not all(math.isfinite(v) for v in values):
            raise ValueError("measurements must be finite numbers")

    @property
    def tonal_range(self) -> float:
        return self.luma_p99 - self.luma_p1

    def summary(self) -> dict[str, float]:
        """The numbers stored in the provenance (rounded: they describe, they do not decide)."""
        return {
            "luma_p1": round(self.luma_p1, 4),
            "luma_p50": round(self.luma_p50, 4),
            "luma_p99": round(self.luma_p99, 4),
            "linear_p50": round(self.linear_p50, 4),
            "linear_p99": round(self.linear_p99, 4),
            "highlight_clip": round(self.highlight_clip, 5),
            "shadow_crush": round(self.shadow_crush, 5),
            "mean_saturation": round(self.mean_saturation, 4),
            "noise_sigma": round(self.noise_sigma, 5),
            "sharpness": round(self.sharpness, 5),
            "exposure_variation_stops": round(self.exposure_variation_stops, 3),
        }
