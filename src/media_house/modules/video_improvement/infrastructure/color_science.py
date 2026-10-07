"""Colour science with NumPy: transfer functions, gamut matrices, the grade, and 3D LUTs.

Everything works on float64 arrays of shape ``(..., 3)`` with 0-1 signals; nothing is quantised
until the encoder. The whole colour stage is one function of the source RGB (``ColorTransform``)
so it can be (a) evaluated on sampled frames to PREDICT the result and (b) sampled on a lattice
and written as a ``.cube`` 3D LUT that FFmpeg applies to every frame with tetrahedral
interpolation. Matrices are derived from published chromaticities, never typed in as constants.

Not graded here, by design: HDR transfer functions (PQ, HLG) and unknown colour spaces.
"""

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.errors import InvalidLut
from media_house.modules.video_improvement.domain.planning import ColorPlan

type Array = NDArray[np.float64]

#: Revision of this module's maths (part of the processing identity).
REVISION = "1"
MID_GREY = 0.18
#: Rec.709 luma weights, applied to linear light.
_LUMA = np.array([0.2126, 0.7152, 0.0722])
_EPSILON = 1e-6


# --------------------------------------------------------------------------------------------
# transfer functions: decode (encoded -> linear light) and encode (linear -> encoded)
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TransferFunction:
    decode: Callable[[Array], Array]
    encode: Callable[[Array], Array]


def _bt1886_decode(v: Array) -> Array:
    return np.power(np.clip(v, 0.0, None), 2.4)


def _bt1886_encode(x: Array) -> Array:
    return np.power(np.clip(x, 0.0, None), 1.0 / 2.4)


def _srgb_decode(v: Array) -> Array:
    v = np.clip(v, 0.0, None)
    return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))


def _srgb_encode(x: Array) -> Array:
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _protune_decode(v: Array) -> Array:
    """GoPro Protune: y = log(112 x + 1) / log(113)."""
    return (np.power(113.0, np.clip(v, 0.0, None)) - 1.0) / 112.0


def _protune_encode(x: Array) -> Array:
    return np.log(112.0 * np.clip(x, 0.0, None) + 1.0) / math.log(113.0)


_SLOG3_CUT = 171.2102946929


def _slog3_decode(v: Array) -> Array:
    code = np.clip(v, 0.0, None) * 1023.0
    high = np.power(10.0, (code - 420.0) / 261.5) * (MID_GREY + 0.01) - 0.01
    low = (code - 95.0) * 0.01125 / (_SLOG3_CUT - 95.0)
    return np.where(code >= _SLOG3_CUT, high, low)


def _slog3_encode(x: Array) -> Array:
    x = np.asarray(x, dtype=np.float64)
    high = (420.0 + np.log10((np.maximum(x, 0.0) + 0.01) / (MID_GREY + 0.01)) * 261.5) / 1023.0
    low = (x * (_SLOG3_CUT - 95.0) / 0.01125 + 95.0) / 1023.0
    return np.where(x >= 0.01125, high, low)


_VLOG_CUT = 0.181


def _vlog_decode(v: Array) -> Array:
    v = np.clip(v, 0.0, None)
    high = np.power(10.0, (v - 0.598206) / 0.241514) - 0.00873
    low = (v - 0.125) / 5.6
    return np.where(v < _VLOG_CUT, low, high)


def _vlog_encode(x: Array) -> Array:
    x = np.asarray(x, dtype=np.float64)
    high = 0.241514 * np.log10(np.maximum(x, 0.0) + 0.00873) + 0.598206
    return np.where(x < 0.01, 5.6 * x + 0.125, high)


TRANSFERS: dict[Transfer, TransferFunction] = {
    Transfer.BT709: TransferFunction(_bt1886_decode, _bt1886_encode),
    Transfer.SRGB: TransferFunction(_srgb_decode, _srgb_encode),
    Transfer.GOPRO_PROTUNE: TransferFunction(_protune_decode, _protune_encode),
    Transfer.SLOG3: TransferFunction(_slog3_decode, _slog3_encode),
    Transfer.VLOG: TransferFunction(_vlog_decode, _vlog_encode),
}


# --------------------------------------------------------------------------------------------
# primaries: chromaticities -> matrices
# --------------------------------------------------------------------------------------------
_D65 = (0.3127, 0.3290)
#: (red, green, blue) CIE xy chromaticities; all use the D65 white point.
CHROMATICITIES: dict[Primaries, tuple[tuple[float, float], ...]] = {
    Primaries.BT709: ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
    Primaries.BT2020: ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
    Primaries.SGAMUT3_CINE: ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087)),
    Primaries.VGAMUT: ((0.730, 0.280), (0.165, 0.840), (0.100, -0.030)),
}


def _xyz(xy: tuple[float, float]) -> Array:
    x, y = xy
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def rgb_to_xyz_matrix(primaries: Primaries) -> Array:
    columns = np.stack([_xyz(xy) for xy in CHROMATICITIES[primaries]], axis=1)
    scale = np.linalg.solve(columns, _xyz(_D65))
    return np.asarray(columns * scale, dtype=np.float64)


def gamut_matrix(source: Primaries, target: Primaries) -> Array:
    """The 3x3 that takes linear RGB of ``source`` primaries to ``target`` primaries."""
    return np.asarray(
        np.linalg.inv(rgb_to_xyz_matrix(target)) @ rgb_to_xyz_matrix(source), dtype=np.float64
    )


def supported(color: ColorSpec) -> bool:
    return color.transfer in TRANSFERS and color.primaries in CHROMATICITIES


# --------------------------------------------------------------------------------------------
# working space (linear Rec.709) in and out
# --------------------------------------------------------------------------------------------
def to_working(rgb: Array, color: ColorSpec) -> Array:
    """Encoded RGB of ``color`` -> linear Rec.709 light."""
    if not supported(color):
        raise ValueError(f"colour space {color} cannot be transformed")
    linear = TRANSFERS[color.transfer].decode(rgb)
    matrix = gamut_matrix(color.primaries, Primaries.BT709)
    return np.asarray(linear @ matrix.T, dtype=np.float64)


def from_working(linear: Array) -> Array:
    """Linear Rec.709 light -> Rec.709 delivery encoding, clipped to the legal 0-1 range."""
    return np.asarray(np.clip(_bt1886_encode(np.clip(linear, 0.0, None)), 0.0, 1.0))


def luminance(linear: Array) -> Array:
    return np.asarray(linear @ _LUMA)


# --------------------------------------------------------------------------------------------
# the grade
# --------------------------------------------------------------------------------------------
def _contrast(x: Array, contrast: float) -> Array:
    """Power curve about mid grey; 1.0 is the identity."""
    if abs(contrast - 1.0) < 1e-9:
        return x
    return np.asarray(MID_GREY * np.power(np.clip(x, 0.0, None) / MID_GREY, contrast))


def rolloff(x: Array, knee: float, white: float) -> Array:
    """Linear below ``knee``; above it a smooth shoulder that maps ``white`` to exactly 1.0."""
    span = 1.0 - knee
    top = math.tanh((max(white, 1.0 + _EPSILON) - knee) / span)
    shoulder = knee + span * np.tanh(np.clip(x - knee, 0.0, None) / span) / top
    return np.asarray(np.where(x <= knee, x, shoulder))


def _saturate(linear: Array, factor: float, vibrance: float) -> Array:
    if abs(factor - 1.0) < 1e-9:
        return linear
    luma = luminance(linear)[..., None]
    peak = np.max(linear, axis=-1, keepdims=True)
    chroma = (peak - np.min(linear, axis=-1, keepdims=True)) / np.maximum(peak, _EPSILON)
    weight = 1.0 + (factor - 1.0) * (1.0 - vibrance * chroma)
    return np.asarray(np.clip(luma + weight * (linear - luma), 0.0, None))


@dataclass(frozen=True, slots=True)
class ColorTransform:
    """Source-encoded RGB -> Rec.709 delivery RGB, as one deterministic function."""

    plan: ColorPlan
    look: "CubeLut | None" = None

    def __call__(self, rgb: Array) -> Array:
        plan = self.plan
        x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
        if plan.black_point:
            x = np.clip((x - plan.black_point) / (1.0 - plan.black_point), 0.0, 1.0)
        linear = to_working(x, plan.input_color)
        gain = 2.0**plan.exposure_stops
        linear = linear * gain * np.asarray(plan.gains)
        if abs(plan.gamma - 1.0) > 1e-9:
            linear = np.power(np.clip(linear, 0.0, None), plan.gamma)
        linear = _contrast(linear, plan.contrast)
        if plan.knee is not None:
            peak = float(TRANSFERS[plan.input_color.transfer].decode(np.array(1.0)))
            white = float(_contrast(np.array((peak * gain) ** plan.gamma), plan.contrast))
            linear = rolloff(linear, plan.knee, white)
        linear = _saturate(linear, plan.saturation, plan.vibrance)
        graded = from_working(linear)
        if self.look is not None and plan.look_strength > 0:
            looked = apply_lut(self.look, graded)
            graded = (1.0 - plan.look_strength) * graded + plan.look_strength * looked
        return graded


# --------------------------------------------------------------------------------------------
# 3D LUTs (.cube)
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CubeLut:
    """A 3D LUT indexed ``[red, green, blue]`` holding RGB, on a domain of 0-1."""

    table: Array
    sha256: str

    @property
    def size(self) -> int:
        return int(self.table.shape[0])


def read_cube(path: Path) -> CubeLut:
    """Parse an Adobe/Resolve ``.cube`` 3D LUT; ``InvalidLut`` for anything else."""
    try:
        raw = path.read_bytes()
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidLut(f"{path.name} cannot be read ({exc})") from exc
    size = 0
    values: list[list[float]] = []
    for line in lines:
        text = line.split("#", 1)[0].strip()
        if not text or text.startswith("TITLE"):
            continue
        head = text.split()
        if head[0] == "LUT_3D_SIZE":
            try:
                size = int(head[1])
            except (IndexError, ValueError) as exc:
                raise InvalidLut("LUT_3D_SIZE is not a number") from exc
        elif head[0] == "LUT_1D_SIZE":
            raise InvalidLut("only 3D LUTs are supported")
        elif head[0] in {"DOMAIN_MIN", "DOMAIN_MAX"}:
            expected = ["0.0", "0.0", "0.0"] if head[0] == "DOMAIN_MIN" else ["1.0", "1.0", "1.0"]
            if [f"{float(v):.1f}" for v in head[1:4]] != expected:
                raise InvalidLut("only LUTs on the domain 0-1 are supported")
        else:
            try:
                row = [float(v) for v in head]
            except ValueError as exc:
                raise InvalidLut(f"unexpected line {text[:30]!r}") from exc
            if len(row) != 3 or not all(math.isfinite(v) for v in row):
                raise InvalidLut("a table row needs three finite numbers")
            values.append(row)
    if not 2 <= size <= 256:
        raise InvalidLut("LUT_3D_SIZE must be between 2 and 256")
    if len(values) != size**3:
        raise InvalidLut(f"expected {size**3} table rows, found {len(values)}")
    table = np.array(values, dtype=np.float64).reshape(size, size, size, 3)  # [b, g, r]
    return CubeLut(
        np.ascontiguousarray(table.transpose(2, 1, 0, 3)), hashlib.sha256(raw).hexdigest()
    )


def write_cube(path: Path, table: Array, title: str) -> None:
    """``table`` indexed ``[red, green, blue]``; the file has red varying fastest."""
    size = table.shape[0]
    rows = np.clip(table.transpose(2, 1, 0, 3).reshape(-1, 3), 0.0, 1.0)
    lines = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    lines.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rows)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def apply_lut(lut: CubeLut, rgb: Array) -> Array:
    """Trilinear lookup of ``rgb`` (0-1) in ``lut``."""
    n = lut.size
    pos = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0) * (n - 1)
    low = np.minimum(np.floor(pos).astype(np.intp), n - 2)
    frac = pos - low
    r, g, b = low[..., 0], low[..., 1], low[..., 2]
    fr, fg, fb = (frac[..., i, None] for i in range(3))
    table = lut.table
    c00 = table[r, g, b] * (1 - fr) + table[r + 1, g, b] * fr
    c10 = table[r, g + 1, b] * (1 - fr) + table[r + 1, g + 1, b] * fr
    c01 = table[r, g, b + 1] * (1 - fr) + table[r + 1, g, b + 1] * fr
    c11 = table[r, g + 1, b + 1] * (1 - fr) + table[r + 1, g + 1, b + 1] * fr
    return np.asarray((c00 * (1 - fg) + c10 * fg) * (1 - fb) + (c01 * (1 - fg) + c11 * fg) * fb)


def bake(transform: ColorTransform, size: int) -> Array:
    """The transform sampled on a ``size``-cubed lattice, indexed ``[red, green, blue]``."""
    axis = np.linspace(0.0, 1.0, size)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
    return np.asarray(transform(grid.reshape(-1, 3)).reshape(size, size, size, 3))
