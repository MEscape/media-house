"""Float32 compositing helpers (straight colour in [0, 1], alpha ``(H, W)``). Pure functions.

shortcut: a few of these mirror visual_asset_generation's private image_ops (another module's
infrastructure may not be imported); move both to a shared imaging package if a third needs them.
"""

import math

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw
from scipy import ndimage

type Array = NDArray[np.float32]


def hex_rgb(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return (int(v[0:2], 16) / 255, int(v[2:4], 16) / 255, int(v[4:6], 16) / 255)


def load_rgba(path: object) -> tuple[Array, Array]:
    with Image.open(path) as im:  # type: ignore[arg-type]
        a = np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0
    return a[..., :3].copy(), a[..., 3].copy()


def crop_visible(rgb: Array, alpha: Array) -> tuple[Array, Array]:
    ys, xs = np.nonzero(alpha > 2 / 255)
    if ys.size == 0:
        return rgb, alpha
    top, bottom, left, right = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return rgb[top:bottom, left:right].copy(), alpha[top:bottom, left:right].copy()


def resize(rgb: Array, alpha: Array, width: int) -> tuple[Array, Array]:
    """Lanczos on premultiplied planes, so edge colour never bleeds in from transparency."""
    height = max(1, round(alpha.shape[0] * width / alpha.shape[1]))
    planes = [rgb[..., c] * alpha for c in range(3)] + [alpha]
    out = [
        np.asarray(
            Image.fromarray(p, mode="F").resize((width, height), Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
        for p in planes
    ]
    a = np.clip(out[3], 0.0, 1.0)
    safe = np.where(a > 1e-6, a, 1.0)[..., None]
    return np.clip(np.stack(out[:3], axis=-1) / safe, 0.0, 1.0).astype(np.float32), a


def rotate(rgb: Array, alpha: Array, degrees: float) -> tuple[Array, Array]:
    if abs(degrees) < 1e-3:
        return rgb, alpha
    planes = [rgb[..., c] * alpha for c in range(3)] + [alpha]
    turned = [
        np.asarray(
            Image.fromarray(p, mode="F").rotate(
                degrees, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=0.0
            ),
            dtype=np.float32,
        )
        for p in planes
    ]
    a = np.clip(turned[3], 0.0, 1.0)
    safe = np.where(a > 1e-6, a, 1.0)[..., None]
    return np.clip(np.stack(turned[:3], axis=-1) / safe, 0.0, 1.0).astype(np.float32), a


def glow(
    rgb: Array,
    alpha: Array,
    *,
    spread: float,
    sigma: float,
    opacity: float,
    colour: tuple[float, float, float],
) -> tuple[Array, Array, int]:
    """Subject over a soft outer glow; returns the padded planes and the padding."""
    pad = int(spread + 3 * sigma) + 4
    rgb = np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)))
    alpha = np.pad(alpha, pad)
    distance = ndimage.distance_transform_edt(alpha < 0.5)
    grown = np.maximum(alpha, np.clip(spread + 1.0 - distance, 0.0, 1.0))
    halo = ndimage.gaussian_filter(grown, sigma, truncate=3.0, mode="constant") * opacity
    behind = np.clip(halo, 0, 1) * (1.0 - alpha)
    out_a = alpha + behind
    pre = rgb * alpha[..., None] + np.asarray(colour, np.float32) * behind[..., None]
    safe = np.where(out_a > 1e-6, out_a, 1.0)[..., None]
    return np.clip(pre / safe, 0, 1).astype(np.float32), np.clip(out_a, 0, 1), pad


def shadow(alpha: Array, *, dx: float, dy: float, sigma: float, opacity: float) -> Array:
    moved = ndimage.shift(alpha, (dy, dx), order=1, mode="constant", cval=0.0)
    if sigma > 0:
        moved = ndimage.gaussian_filter(moved, sigma, truncate=3.0, mode="constant")
    return np.asarray(np.clip(moved * opacity, 0.0, 1.0), dtype=np.float32)


def inner_rim(alpha: Array, width: float) -> Array:
    inside = ndimage.distance_transform_edt(alpha >= 0.5)
    return np.asarray(alpha * np.clip(1.0 - inside / width, 0.0, 1.0), dtype=np.float32)


def screen(rgb: Array, colour: tuple[float, float, float], weight: Array) -> Array:
    light = np.asarray(colour, dtype=np.float32) * weight[..., None]
    return np.asarray(1.0 - (1.0 - rgb) * (1.0 - light), dtype=np.float32)


def contrast(rgb: Array, amount: float) -> Array:
    return np.asarray(np.clip((rgb - 0.5) * (1.0 + amount) + 0.5, 0.0, 1.0), dtype=np.float32)


def place(canvas: Array, rgb: Array, alpha: Array, x: int, y: int) -> tuple[int, int, int, int]:
    """OVER onto the opaque canvas at (x, y), clipped; returns the drawn box (unclipped)."""
    h, w = alpha.shape
    canvas_h, canvas_w = canvas.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, canvas_w), min(y + h, canvas_h)
    if x0 < x1 and y0 < y1:
        a = alpha[y0 - y : y1 - y, x0 - x : x1 - x, None]
        region = canvas[y0:y1, x0:x1]
        canvas[y0:y1, x0:x1] = rgb[y0 - y : y1 - y, x0 - x : x1 - x] * a + region * (1 - a)
    return x, y, x + w, y + h


def darken(canvas: Array, alpha: Array, x: int, y: int) -> None:
    """Multiply a shadow mask (``alpha``) into the canvas at (x, y)."""
    black = np.zeros((*alpha.shape, 3), np.float32)
    place(canvas, black, alpha, x, y)


def rgba_layer(size: tuple[int, int], draw: object) -> tuple[Array, Array]:
    """Run ``draw(ImageDraw, Image)`` on a transparent layer; return straight rgb + alpha."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(im), im)  # type: ignore[operator]
    a = np.asarray(im, dtype=np.float32) / 255.0
    return a[..., :3].copy(), a[..., 3].copy()


def bar_mask(
    size: tuple[int, int], p0: tuple[float, float], p1: tuple[float, float], thick: float
) -> Array:
    (x0, y0), (x1, y1) = p0, p1
    nx, ny = -(y1 - y0), (x1 - x0)
    n = math.hypot(nx, ny)
    nx, ny = nx / n * thick / 2, ny / n * thick / 2
    im = Image.new("L", size, 0)
    ImageDraw.Draw(im).polygon(
        [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], fill=255
    )
    return np.asarray(im, dtype=np.float32) / 255.0
