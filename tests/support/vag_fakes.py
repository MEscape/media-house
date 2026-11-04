"""Test doubles and picture builders for visual_asset_generation (no AI model, no FFmpeg)."""

from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

BACKGROUND = (80, 120, 160)
RED = (200, 30, 40)


class ColourSegmenter:
    """Stand-in for the AI model: finds the red subject by colour and counts its calls.

    ``mode`` simulates the failure cases: ``"empty"`` (nothing found), ``"full"`` (everything).
    """

    def __init__(self, mode: str = "subject") -> None:
        self.mode = mode
        self.calls = 0

    def matte(self, image: Image.Image, model: str) -> NDArray[np.float32]:
        self.calls += 1
        pixels = np.asarray(image, dtype=np.float32)
        if self.mode == "empty":
            return np.zeros(pixels.shape[:2], dtype=np.float32)
        if self.mode == "full":
            return np.ones(pixels.shape[:2], dtype=np.float32)
        distance = np.abs(pixels - np.asarray(RED, dtype=np.float32)).sum(axis=-1)
        return (distance < 40).astype(np.float32)


def product_png(
    path: Path,
    box: tuple[int, int, int, int] = (120, 80, 280, 220),
    size: tuple[int, int] = (400, 300),
) -> Path:
    """A red ellipse (the "product") on a blue-grey background."""
    image = Image.new("RGB", size, BACKGROUND)
    ImageDraw.Draw(image).ellipse(box, fill=RED)
    image.save(path)
    return path


def rgba_of(path: Path) -> NDArray[np.uint8]:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA")).copy()


def border_alpha_max(pixels: NDArray[np.uint8]) -> int:
    alpha = pixels[..., 3]
    return int(max(alpha[0].max(), alpha[-1].max(), alpha[:, 0].max(), alpha[:, -1].max()))
