"""Renders the adjusted PNG: segment -> clean -> crop -> decontaminate -> fit -> glow -> save."""

from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

from media_house.modules.image_adjustment.application.ports import RenderReport
from media_house.modules.image_adjustment.domain.errors import ImageUnreadable, SegmentationRejected
from media_house.modules.image_adjustment.domain.values import (
    AdjustmentSettings,
    Centering,
    MaskStats,
)
from media_house.modules.image_adjustment.infrastructure import image_ops as ops
from media_house.modules.image_adjustment.infrastructure.image_ops import Array

#: Larger sources are shrunk first; the output canvas is at most 4096 px anyway.
MAX_WORKING_EDGE = 4096
_MAX_ASSET_PIXELS = 200_000_000


class Segmenter(Protocol):
    def matte(self, image: Image.Image, model: str) -> NDArray[np.float32]:
        """Soft foreground alpha ``(H, W)`` in [0, 1] for an RGB image."""
        ...


class PillowImageRenderer:
    """Implements ``ImageRenderer``; the segmenter is injected so tests need no AI model."""

    def __init__(self, segmenter: Segmenter) -> None:
        self._segmenter = segmenter

    def render(self, source: Path, destination: Path, settings: AdjustmentSettings) -> RenderReport:
        rgba = _load(source)
        rgb, alpha, mask = self._subject(rgba, settings)

        box = ops.visible_bbox(alpha)
        if box is None:
            raise SegmentationRejected("no visible subject remains after clean-up")
        left, top, right, bottom = box
        rgb = ops.decontaminate(rgb[top:bottom, left:right], alpha[top:bottom, left:right])
        alpha = alpha[top:bottom, left:right]

        canvas, glow = settings.canvas, settings.glow
        margin = canvas.padding + glow.extent
        scale = min(
            (canvas.width - 2 * margin) / alpha.shape[1],
            (canvas.height - 2 * margin) / alpha.shape[0],
        )
        size = (
            max(1, min(canvas.width - 2 * margin, round(alpha.shape[1] * scale))),
            max(1, min(canvas.height - 2 * margin, round(alpha.shape[0] * scale))),
        )
        rgb, alpha = ops.resize(rgb, alpha, size)
        x, y = _position(alpha, settings, margin)

        layer_rgb = np.zeros((canvas.height, canvas.width, 3), dtype=np.float32)
        layer_alpha = np.zeros((canvas.height, canvas.width), dtype=np.float32)
        layer_rgb[y : y + size[1], x : x + size[0]] = rgb
        layer_alpha[y : y + size[1], x : x + size[0]] = alpha

        if glow.enabled:
            color = tuple(c / 255.0 for c in glow.color.rgb)
            glow_alpha = ops.make_glow(
                layer_alpha,
                spread=glow.spread,
                sigma=glow.sigma,
                opacity=glow.opacity,
            )
            layer_rgb, layer_alpha = ops.composite_over_glow(
                layer_rgb,
                layer_alpha,
                glow_alpha,
                (color[0], color[1], color[2]),
            )
        else:
            layer_rgb = _fill_transparent(layer_rgb, layer_alpha)

        _save(layer_rgb, layer_alpha, destination)
        return RenderReport(mask=mask, scale=scale, subject_box=(x, y, x + size[0], y + size[1]))

    def _subject(
        self,
        rgba: Image.Image,
        settings: AdjustmentSettings,
    ) -> tuple[Array, Array, MaskStats]:
        """Straight RGB and alpha of the subject at working resolution, plus raw mask stats."""
        data = np.asarray(rgba, dtype=np.float32) / 255.0
        rgb, source_alpha = data[..., :3], data[..., 3]
        if not settings.background_removal.enabled:
            return rgb, source_alpha, _stats(source_alpha)

        flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
        matte = self._segmenter.matte(flat.convert("RGB"), settings.background_removal.model)
        if matte.shape != source_alpha.shape:
            raise SegmentationRejected("segmentation returned a mask of the wrong size")
        stats = _stats(matte)
        settings.mask_limits.check(stats)
        return rgb, ops.clean_matte(matte) * source_alpha, stats


def _load(path: Path) -> Image.Image:
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > _MAX_ASSET_PIXELS:
                raise ImageUnreadable("image is too large")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageUnreadable(str(exc)) from exc
    if max(image.size) > MAX_WORKING_EDGE:
        image.thumbnail((MAX_WORKING_EDGE, MAX_WORKING_EDGE), Image.Resampling.LANCZOS)
    return image


def _stats(matte: Array) -> MaskStats:
    return MaskStats(float((matte >= 0.5).mean()), ops.mask_bbox(matte, 0.5))


def _position(alpha: Array, settings: AdjustmentSettings, margin: int) -> tuple[int, int]:
    """Top-left of the subject on the canvas, always inside the padded + glow-safe area."""
    canvas = settings.canvas
    height, width = alpha.shape
    x = (canvas.width - width) // 2
    y = (canvas.height - height) // 2
    if canvas.centering is Centering.CENTER_OF_MASS:
        total = float(alpha.sum())
        if total > 0:
            cx = float((alpha.sum(axis=0) * np.arange(width)).sum()) / total
            cy = float((alpha.sum(axis=1) * np.arange(height)).sum()) / total
            x = round(canvas.width / 2 - cx - 0.5)
            y = round(canvas.height / 2 - cy - 0.5)
    x = min(max(x, margin), canvas.width - margin - width)
    y = min(max(y, margin), canvas.height - margin - height)
    return x, y


def _fill_transparent(rgb: Array, alpha: Array) -> Array:
    """Give invisible pixels the subject's mean colour so resampling later never darkens edges."""
    weight = float(alpha.sum())
    if weight <= 0:
        return rgb
    mean = (rgb * alpha[..., None]).sum(axis=(0, 1)) / weight
    return np.asarray(np.where(alpha[..., None] > 0, rgb, mean), dtype=np.float32)


def _save(rgb: Array, alpha: Array, destination: Path) -> None:
    pixels = np.dstack([rgb, alpha])
    data = np.rint(np.clip(pixels, 0.0, 1.0) * 255.0).astype(np.uint8)
    Image.fromarray(data, mode="RGBA").save(destination, format="PNG")
