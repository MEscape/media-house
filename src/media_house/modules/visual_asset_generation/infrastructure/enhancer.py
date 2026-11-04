"""Enhanced variants of a CLEAN cutout: silhouette, contrast, rim, tilt, outline, glow, shadow.

Works in float32 on straight colour. The subject keeps its exact pixels (scale 1.0) whenever the
effects fit the canvas; otherwise it is shrunk once (Lanczos) to make room, never cropped.
Back-to-front: shadow, glow, outline, subject. Opaque subject pixels are never touched by an
effect behind them.
"""

import math
from pathlib import Path

import numpy as np

from media_house.modules.visual_asset_generation.application.ports import EnhanceReport
from media_house.modules.visual_asset_generation.domain.errors import ImageUnreadable
from media_house.modules.visual_asset_generation.domain.profile import Enhancement
from media_house.modules.visual_asset_generation.domain.values import Canvas, HexColor
from media_house.modules.visual_asset_generation.infrastructure import image_ops as ops
from media_house.modules.visual_asset_generation.infrastructure.image_ops import Array
from media_house.modules.visual_asset_generation.infrastructure.pillow_renderer import (
    fill_transparent,
    load_rgba,
    save_rgba,
)


def _rgb(color: HexColor) -> tuple[float, float, float]:
    r, g, b = color.rgb
    return r / 255.0, g / 255.0, b / 255.0


class PillowCutoutEnhancer:
    """Implements ``CutoutEnhancer``."""

    def enhance(
        self,
        cutout: Path,
        destination: Path,
        enhancement: Enhancement,
        canvas: Canvas,
    ) -> EnhanceReport:
        data = np.asarray(load_rgba(cutout), dtype=np.float32) / 255.0
        rgb, alpha = data[..., :3], data[..., 3]
        box = ops.visible_bbox(alpha)
        if box is None:
            raise ImageUnreadable("the cutout has no visible pixels")
        left, top, right, bottom = box
        rgb, alpha = rgb[top:bottom, left:right].copy(), alpha[top:bottom, left:right].copy()

        rgb = self._recolour(rgb, alpha, enhancement)
        scale = _fit_scale(alpha.shape, enhancement, canvas)
        if scale < 1.0:
            size = (
                max(1, math.floor(alpha.shape[1] * scale)),
                max(1, math.floor(alpha.shape[0] * scale)),
            )
            rgb, alpha = ops.resize(rgb, alpha, size)
        if enhancement.tilt.enabled and enhancement.tilt.degrees != 0.0:
            rgb, alpha = ops.rotate(rgb, alpha, enhancement.tilt.degrees)
            turned = ops.visible_bbox(alpha)
            if turned is not None:
                x0, y0, x1, y1 = turned
                rgb, alpha = rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1]

        pad = enhancement.extent
        height, width = alpha.shape
        layer_rgb = np.zeros((height + 2 * pad, width + 2 * pad, 3), dtype=np.float32)
        layer_alpha = np.zeros((height + 2 * pad, width + 2 * pad), dtype=np.float32)
        layer_rgb[pad : pad + height, pad : pad + width] = rgb
        layer_alpha[pad : pad + height, pad : pad + width] = alpha
        layer_rgb, layer_alpha = _effects_behind(layer_rgb, layer_alpha, enhancement)

        out_rgb = np.zeros((canvas.height, canvas.width, 3), dtype=np.float32)
        out_alpha = np.zeros((canvas.height, canvas.width), dtype=np.float32)
        lh, lw = layer_alpha.shape
        if lw > canvas.width or lh > canvas.height:  # guarded by _fit_scale; never crop silently
            raise ImageUnreadable("the effects do not fit the canvas")
        x, y = (canvas.width - lw) // 2, (canvas.height - lh) // 2
        out_rgb[y : y + lh, x : x + lw] = layer_rgb
        out_alpha[y : y + lh, x : x + lw] = layer_alpha
        save_rgba(fill_transparent(out_rgb, out_alpha), out_alpha, destination)
        visible = ops.visible_bbox(out_alpha) or (x, y, x + lw, y + lh)
        return EnhanceReport(scale=scale, visible_box=visible)

    def _recolour(self, rgb: Array, alpha: Array, enhancement: Enhancement) -> Array:
        if enhancement.silhouette.enabled:
            rgb = ops.solid(alpha.shape, _rgb(enhancement.silhouette.color))
        if enhancement.contrast.enabled:
            rgb = ops.contrast(rgb, enhancement.contrast.amount)
        rim = enhancement.rim
        if rim.enabled:
            rgb = ops.screen(rgb, _rgb(rim.color), ops.inner_rim(alpha, rim.width) * rim.intensity)
        return rgb


def _fit_scale(shape: tuple[int, ...], enhancement: Enhancement, canvas: Canvas) -> float:
    """1.0 when the (tilted) subject plus its effects fit, else the shrink that makes them fit."""
    height, width = float(shape[0]), float(shape[1])
    if enhancement.tilt.enabled:
        angle = math.radians(enhancement.tilt.degrees)
        cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
        width, height = width * cos + height * sin, width * sin + height * cos
    # Two spare pixels absorb rounding of the resize and of the rotated bounds.
    room_w = canvas.width - 2 * enhancement.extent - 2
    room_h = canvas.height - 2 * enhancement.extent - 2
    if room_w < 1 or room_h < 1:
        raise ImageUnreadable("the effects leave no room on the canvas")
    return min(1.0, room_w / width, room_h / height)


def _effects_behind(rgb: Array, alpha: Array, enhancement: Enhancement) -> tuple[Array, Array]:
    shape = (alpha.shape[0], alpha.shape[1])
    outline = enhancement.outline
    if outline.enabled:
        stroke = ops.grow(alpha, outline.width) * outline.opacity
        rgb, alpha = ops.over(rgb, alpha, ops.solid(shape, _rgb(outline.color)), stroke)
    silhouette = alpha.copy()
    glow = enhancement.glow
    if glow.enabled:
        halo = ops.make_glow(silhouette, spread=glow.spread, sigma=glow.sigma, opacity=glow.opacity)
        rgb, alpha = ops.over(rgb, alpha, ops.solid(shape, _rgb(glow.color)), halo)
    shadow = enhancement.shadow
    if shadow.enabled:
        cast = ops.soft_shadow(
            silhouette,
            dx=shadow.offset_x,
            dy=shadow.offset_y,
            sigma=shadow.blur / 2.0,
            opacity=shadow.opacity,
        )
        rgb, alpha = ops.over(rgb, alpha, ops.solid(shape, _rgb(shadow.color)), cast)
    return rgb, alpha
