"""Measures a written image for QC: decodes it fully and reads its real alpha."""

import struct
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from media_house.modules.visual_asset_generation.domain.qc import ImageFacts
from media_house.modules.visual_asset_generation.infrastructure.image_ops import Array

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EIGHT_CONNECTED = np.ones((3, 3), dtype=bool)
#: Rim pixels farther than this from the solid core are a wide soft area, not an edge.
_RIM_REACH = 4.0
#: A detached component smaller than this share of the largest one is residue.
_RESIDUE_SHARE = 0.001


class PillowImageMeasurer:
    """Implements ``ImageMeasurer``. A file that does not decode is a fact, not an exception."""

    def measure(self, path: Path) -> ImageFacts:
        try:
            with Image.open(path) as image:
                image.load()
                fmt, mode = image.format or "", image.mode
                rgba = image.convert("RGBA") if _has_alpha(image) else None
                size = image.size
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            return ImageFacts(error=str(exc) or type(exc).__name__)
        facts = ImageFacts(
            format=fmt,
            mode=mode,
            width=size[0],
            height=size[1],
            has_alpha=rgba is not None,
            bits_per_channel=_png_bit_depth(path) if fmt == "PNG" else 8,
            transparent_ratio=0.0,
            opaque_ratio=1.0,
            border_alpha=1.0,
        )
        if rgba is None:
            return facts
        data = np.asarray(rgba, dtype=np.float32) / 255.0
        rgb, alpha = data[..., :3], data[..., 3]
        ring = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
        return replace(
            facts,
            transparent_ratio=float((alpha == 0.0).mean()),
            opaque_ratio=float((alpha == 1.0).mean()),
            border_alpha=float(ring.max()),
            rim_deviation=_rim_deviation(rgb, alpha),
            residue_ratio=_residue(alpha),
        )


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "RGBa", "La", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _png_bit_depth(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(25)
    if len(header) < 25 or not header.startswith(_PNG_SIGNATURE):
        return 8
    return int(struct.unpack(">B", header[24:25])[0])


def _rim_deviation(rgb: Array, alpha: Array) -> float | None:
    """Mean colour distance between soft edge pixels and the nearest solid subject pixel."""
    core = alpha >= 0.95
    rim = (alpha > 0.05) & (alpha < 0.95)
    if not core.any() or not rim.any():
        return None
    distance, indices = ndimage.distance_transform_edt(~core, return_indices=True)
    near = rim & (distance <= _RIM_REACH)
    if not near.any():
        return None
    nearest = rgb[indices[0], indices[1]]
    return float(np.abs(rgb[near] - nearest[near]).mean())


def _residue(alpha: Array) -> float:
    visible = alpha > 0.0
    labels, count = ndimage.label(visible, structure=_EIGHT_CONNECTED)
    if count <= 1:
        return 0.0
    areas = np.bincount(labels.ravel(), minlength=count + 1)[1:]
    small = np.flatnonzero(areas < _RESIDUE_SHARE * areas.max()) + 1
    return float(np.isin(labels, small).sum() / alpha.size)
