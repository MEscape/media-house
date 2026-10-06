"""Pixel operations on float32 arrays in [0, 1]. Pure functions, no I/O.

Colour convention: ``rgb`` is *straight* (not multiplied by alpha) unless a name says
``premultiplied``. Alpha is always ``(H, W)``.
"""

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import ndimage

type Array = NDArray[np.float32]

_EIGHT_CONNECTED = np.ones((3, 3), dtype=bool)
#: Components smaller than this share of the largest one are specks, not parts of the subject.
_SPECK_RATIO = 0.001
_SPECK_MIN_PIXELS = 16
#: Enclosed holes smaller than this share of the subject are matting errors, not product holes.
_HOLE_RATIO = 0.002
#: Alpha below/above these is snapped to 0/1 so near-invisible haze does not widen the crop.
_LEVEL_LOW, _LEVEL_HIGH = 0.03, 0.97
_VISIBLE = 2.0 / 255.0
#: Pixels this close to the solid core get their colour from it (background fringe zone).
_FRINGE_WIDTH = 4.0
#: Enlarging more than this factor smooths the matte first.
_SMOOTH_UPSCALE_FROM = 1.5


def clean_matte(matte: Array) -> Array:
    """Conservative clean-up of an AI matte: drop specks, fill tiny holes, smooth only the edge.

    Never erodes: every pixel the model kept stays (thin cables, handles and text survive).
    """
    solid = matte >= 0.5
    if not solid.any():
        return matte
    labels, count = ndimage.label(solid, structure=_EIGHT_CONNECTED)
    areas = np.bincount(labels.ravel(), minlength=count + 1)[1:]
    keep_ids = np.flatnonzero(areas >= max(_SPECK_MIN_PIXELS, _SPECK_RATIO * areas.max())) + 1
    kept = np.isin(labels, keep_ids)
    # Keep the soft rim around kept regions; clear everything farther away.
    support = ndimage.binary_dilation(kept, iterations=2)
    result = np.where(support, matte, 0.0).astype(np.float32)

    hole_labels, hole_count = ndimage.label(ndimage.binary_fill_holes(kept) & ~kept)
    if hole_count:
        hole_areas = np.bincount(hole_labels.ravel(), minlength=hole_count + 1)[1:]
        small = np.flatnonzero(hole_areas <= _HOLE_RATIO * kept.sum()) + 1
        result = np.where(np.isin(hole_labels, small), 1.0, result).astype(np.float32)

    # Smooth the transition band only: halve toward a light blur so contours stay put.
    band = ndimage.binary_dilation((result > 0.02) & (result < 0.98))
    blurred = ndimage.gaussian_filter(result, 0.8)
    result = np.where(band, 0.5 * result + 0.5 * blurred, result)
    levelled = np.clip((result - _LEVEL_LOW) / (_LEVEL_HIGH - _LEVEL_LOW), 0.0, 1.0)
    return np.asarray(levelled, dtype=np.float32)


def mask_bbox(alpha: Array, threshold: float) -> tuple[int, int, int, int] | None:
    """``(left, top, right, bottom)`` (right/bottom exclusive) of pixels above ``threshold``."""
    ys, xs = np.nonzero(alpha > threshold)
    if ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def visible_bbox(alpha: Array) -> tuple[int, int, int, int] | None:
    return mask_bbox(alpha, _VISIBLE)


def decontaminate(rgb: Array, alpha: Array) -> Array:
    """Replace the colour of soft edge pixels with the nearest solid subject colour.

    Removes background-coloured fringes (white/dark halos) that the matte lets through.
    """
    core = ndimage.binary_erosion(alpha >= 0.95)
    if core.sum() < _SPECK_MIN_PIXELS:
        core = alpha >= 0.95
    if not core.any() or core.all():
        return rgb
    distance, indices = ndimage.distance_transform_edt(~core, return_indices=True)
    nearest = rgb[indices[0], indices[1]]
    # Only the thin rim: wide semi-transparent areas keep their own colour (no streaks).
    replace = ~core & (distance <= _FRINGE_WIDTH)
    return np.asarray(np.where(replace[..., None], nearest, rgb), dtype=np.float32)


def resize(rgb: Array, alpha: Array, size: tuple[int, int]) -> tuple[Array, Array]:
    """Lanczos resize of colour and alpha as separate planes.

    Colour is safe to resample on its own because ``decontaminate`` already extended clean
    subject colour past the edge; premultiplying instead lets Lanczos ringing skew the colour
    of low-alpha edge pixels.
    """
    width, height = size
    upscaling = width / alpha.shape[1] > _SMOOTH_UPSCALE_FROM
    if upscaling:  # a low-res matte would otherwise show stair-steps along the contour
        alpha = ndimage.gaussian_filter(alpha, 1.2)
    planes = [rgb[..., 0], rgb[..., 1], rgb[..., 2], alpha]
    resized = [
        np.asarray(Image.fromarray(c, mode="F").resize((width, height), Image.Resampling.LANCZOS))
        for c in planes
    ]
    out_rgb = np.clip(np.stack(resized[:3], axis=-1), 0.0, 1.0)
    out_alpha = np.clip(resized[3], 0.0, 1.0)
    if upscaling:  # re-sharpen the softened contour with a smoothstep around 0.5
        t = np.clip((out_alpha - 0.2) / 0.6, 0.0, 1.0)
        out_alpha = t * t * (3.0 - 2.0 * t)
    return out_rgb.astype(np.float32), out_alpha.astype(np.float32)


def make_glow(alpha: Array, *, spread: float, sigma: float, opacity: float) -> Array:
    """Glow alpha from the subject silhouette: grow by ``spread`` px, blur, scale by opacity."""
    distance = ndimage.distance_transform_edt(alpha < 0.5)
    grown = np.maximum(alpha, np.clip(spread + 1.0 - distance, 0.0, 1.0))
    if sigma > 0:
        grown = ndimage.gaussian_filter(grown, sigma, truncate=3.0, mode="constant")
    return np.asarray(np.clip(grown * opacity, 0.0, 1.0), dtype=np.float32)


def composite_over_glow(
    rgb: Array,
    alpha: Array,
    glow_alpha: Array,
    glow_color: tuple[float, float, float],
) -> tuple[Array, Array]:
    """Subject OVER glow. Where the subject is opaque the glow contributes exactly nothing."""
    color = np.asarray(glow_color, dtype=np.float32)
    behind = glow_alpha * (1.0 - alpha)
    out_alpha = alpha + behind
    premultiplied = rgb * alpha[..., None] + color * behind[..., None]
    safe = np.where(out_alpha > 1e-6, out_alpha, 1.0)[..., None]
    out_rgb = np.where(out_alpha[..., None] > 1e-6, premultiplied / safe, color)
    return np.clip(out_rgb, 0.0, 1.0).astype(np.float32), np.clip(out_alpha, 0.0, 1.0)
