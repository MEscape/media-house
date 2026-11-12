"""A gentle, deterministic auto grade per recording, as an ASC CDL on the clip's first node.

The measured picture average says how far the recording leans towards a colour. Half of that
cast is taken out (gray world at 50 %: a warm room stays a little warm), the contrast is lifted a
touch around a mid grey and the saturation by 5 %. Nothing is guessed when nothing was measured:
no grade at all.
"""

from dataclasses import dataclass

#: Share of the measured cast that is neutralised, the contrast lift and its pivot.
_NEUTRALISE = 0.5
_CONTRAST = 1.05
_PIVOT = 0.45
_SATURATION = 1.05
#: A white balance step never goes further than this (a strongly coloured scene is not a cast).
_MAX_STEP = 0.12


@dataclass(frozen=True, slots=True)
class Cdl:
    slope: tuple[float, float, float]
    offset: tuple[float, float, float]
    power: tuple[float, float, float]
    saturation: float


def rgb_of_yuv(y: float, u: float, v: float) -> tuple[float, float, float]:
    """8-bit limited-range BT.709 averages -> normalised RGB (clamped to 0..1)."""
    yn, un, vn = (y - 16) / 219, (u - 128) / 224, (v - 128) / 224
    r = yn + 1.5748 * vn
    g = yn - 0.1873 * un - 0.4681 * vn
    b = yn + 1.8556 * un
    return (_unit(r), _unit(g), _unit(b))


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def auto_grade(mean_rgb: tuple[float, float, float]) -> Cdl | None:
    """The CDL for a recording whose picture averages ``mean_rgb`` (normalised RGB)."""
    if min(mean_rgb) <= 0.02:
        return None  # nearly black: no reliable cast
    grey = sum(mean_rgb) / 3
    r, g, b = (
        round(
            _CONTRAST
            * min(max(1.0 + _NEUTRALISE * (grey / c - 1.0), 1 - _MAX_STEP), 1 + _MAX_STEP),
            4,
        )
        for c in mean_rgb
    )
    offset = round(_PIVOT * (1.0 - _CONTRAST), 4)
    return Cdl(
        slope=(r, g, b),
        offset=(offset, offset, offset),
        power=(1.0, 1.0, 1.0),
        saturation=_SATURATION,
    )
