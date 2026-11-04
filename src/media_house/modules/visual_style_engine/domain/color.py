"""Colour arithmetic for graphic tokens: HEX parsing and WCAG 2.x contrast (sRGB).

Graphic colours only. Footage colour (grading, white balance) belongs to ``video_improvement``.
"""

import colorsys
import re

_HEX = re.compile(r"^#[0-9A-F]{6}$")


def is_hex(value: str) -> bool:
    """``#RRGGBB`` in upper case (the one canonical spelling, so equal colours compare equal)."""
    return bool(_HEX.match(value))


def rgb(hex_value: str) -> tuple[int, int, int]:
    if not is_hex(hex_value.upper()):
        raise ValueError(f"{hex_value!r} is not a #RRGGBB colour")
    return int(hex_value[1:3], 16), int(hex_value[3:5], 16), int(hex_value[5:7], 16)


def hsl(hex_value: str) -> tuple[float, float, float]:
    """Hue in degrees, saturation and lightness as 0-1 (rounded to 4 places)."""
    r, g, b = (c / 255 for c in rgb(hex_value))
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360, 4), round(s, 4), round(light, 4)


def relative_luminance(hex_value: str) -> float:
    """WCAG 2.x relative luminance of an sRGB colour."""

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb(hex_value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio (1-21), rounded to 2 places."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)
