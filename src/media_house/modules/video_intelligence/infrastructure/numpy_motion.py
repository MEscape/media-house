"""Global camera motion between sampled frames by phase correlation (NumPy FFT, no ML).

For each pair of consecutive planned sample frames:

1. the whole-frame phase correlation gives the dominant translation (any size, sub-pixel);
2. the overlap of the two pictures, aligned by that translation, is cut into a grid of blocks and
   each block's remaining shift is measured the same way;
3. a weighted least-squares fit of those block shifts gives the translation correction, the
   scale change and the rotation of a similarity transform about the frame centre;
4. the mean luma difference left after the alignment is the residual picture motion.

Confidence is the height of the whole-frame correlation peak: close to 1 for a clean shift, near 0
for a textureless or unrelated picture.
"""

import math

import numpy as np
from numpy.typing import NDArray

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.signals import MotionSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerId, DeviceKind
from media_house.modules.video_intelligence.infrastructure.numpy_signals import (
    Plane,
    engine_identity,
    guarded,
    own,
    rounded,
    sample_frame,
)
from media_house.shared.concurrency import CancellationToken

_EPSILON = 1e-9
#: Blocks smaller than this many pixels on a side give no usable correlation.
_MIN_BLOCK = 24
#: The overlap must keep at least this share of the picture for a meaningful estimate.
_MIN_OVERLAP = 0.25
_MIN_BLOCK_CONFIDENCE = 0.05

type Window = NDArray[np.float32]


def _hann(shape: tuple[int, int]) -> Window:
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1])).astype(np.float32)


def _correlate(a: Plane, b: Plane, window: Window) -> tuple[float, float, float]:
    """Shift (dx, dy) of the content of ``b`` relative to ``a`` in pixels, and the peak height."""
    height, width = a.shape
    spectrum_a = np.fft.rfft2((a - a.mean()) * window)
    spectrum_b = np.fft.rfft2((b - b.mean()) * window)
    cross = spectrum_b * np.conj(spectrum_a)
    cross /= np.abs(cross) + _EPSILON
    surface = np.fft.irfft2(cross, s=(height, width))
    row, column = np.unravel_index(int(np.argmax(surface)), surface.shape)
    py, px = int(row), int(column)
    peak = float(surface[py, px])
    dx = _refine(surface[py, (px - 1) % width], peak, surface[py, (px + 1) % width], px, width)
    dy = _refine(surface[(py - 1) % height, px], peak, surface[(py + 1) % height, px], py, height)
    return dx, dy, float(np.clip(peak, 0.0, 1.0))


def _refine(before: float, peak: float, after: float, position: int, size: int) -> float:
    """Sub-pixel peak position by a parabola through the peak and its neighbours (wrapped)."""
    denominator = before - 2.0 * peak + after
    offset = 0.5 * (before - after) / denominator if abs(denominator) > _EPSILON else 0.0
    value = position + max(-1.0, min(1.0, offset))
    return value - size if value > size / 2 else value


def _overlap(a: Plane, b: Plane, dx: int, dy: int) -> tuple[Plane, Plane, int, int]:
    """Parts of ``a`` and ``b`` that show the same content, and the offset of ``a``'s part."""
    height, width = a.shape
    ax, ay = max(0, -dx), max(0, -dy)
    bx, by = max(0, dx), max(0, dy)
    w, h = width - abs(dx), height - abs(dy)
    return a[ay : ay + h, ax : ax + w], b[by : by + h, bx : bx + w], ax, ay


class NumpyMotionAnalyzer:
    """Camera translation, scale and rotation between planned sample frames."""

    analyzer = AnalyzerId.MOTION

    def identity(self) -> dict[str, str]:
        return engine_identity()

    def unavailable(self, allow_downloads: bool) -> str | None:
        return None

    def device(self, requested: DeviceKind) -> str:
        return "cpu"

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> MotionSignals:
        frames = own(video)
        grid = request.settings.motion_grid
        usable = [f for f in request.plan if f // frames.stride < frames.sample_count]

        def work() -> MotionSignals:
            columns: dict[str, list[float]] = {
                k: [] for k in ("tx", "ty", "scale", "rotation", "residual", "confidence")
            }
            later: list[int] = []
            earlier: list[int] = []
            window: Window | None = None
            previous: Plane | None = None
            for index, frame in enumerate(usable):
                cancellation.raise_if_cancelled()
                picture = sample_frame(frames, frame)
                if previous is not None:
                    if window is None:
                        window = _hann(picture.shape)
                    estimate = _estimate(previous, picture, window, grid)
                    later.append(frame)
                    earlier.append(usable[index - 1])
                    for key, value in zip(columns, estimate, strict=True):
                        columns[key].append(value)
                previous = picture
            return MotionSignals(
                stride=frames.stride,
                frames=tuple(later),
                prev_frames=tuple(earlier),
                tx=rounded(np.array(columns["tx"])),
                ty=rounded(np.array(columns["ty"])),
                log_scale=rounded(np.array(columns["scale"])),
                rotation=rounded(np.array(columns["rotation"])),
                residual=rounded(np.array(columns["residual"])),
                confidence=rounded(np.array(columns["confidence"])),
            )

        return guarded("Measuring camera motion", work)


def _estimate(
    a: Plane, b: Plane, window: Window, grid: int
) -> tuple[float, float, float, float, float, float]:
    """(tx, ty, log scale, rotation, residual, confidence) of the content from ``a`` to ``b``.

    ``tx`` and ``ty`` are fractions of the frame width and height.
    """
    height, width = a.shape
    gx, gy, confidence = _correlate(a, b, window)
    dx, dy = round(gx), round(gy)
    crop_a, crop_b, ox, oy = _overlap(a, b, dx, dy)
    ch, cw = crop_a.shape
    if ch * cw < _MIN_OVERLAP * height * width:
        return gx / width, gy / height, 0.0, 0.0, 1.0, 0.0

    residual = float(np.abs(crop_a - crop_b).mean())
    tx, ty, scale, rotation = float(dx), float(dy), 0.0, 0.0
    blocks = _block_shifts(crop_a, crop_b, grid)
    if blocks:
        # a zoom breaks the whole-frame correlation but not the small blocks': trust the better one
        confidence = max(confidence, float(np.median([peak for *_, peak in blocks])))
        centres = np.array(
            [[ox + bx - width / 2.0, oy + by - height / 2.0] for bx, by, _, _, _ in blocks]
        )
        shifts = np.array([[sx, sy] for _, _, sx, sy, _ in blocks])
        weights = np.sqrt(np.array([w for *_, w in blocks]))
        # sx = tx + s*cx + r*cy ; sy = ty + s*cy - r*cx   (similarity about the frame centre)
        rows = len(blocks)
        design = np.zeros((2 * rows, 4))
        design[:rows, 0] = 1.0
        design[:rows, 2] = centres[:, 0]
        design[:rows, 3] = centres[:, 1]
        design[rows:, 1] = 1.0
        design[rows:, 2] = centres[:, 1]
        design[rows:, 3] = -centres[:, 0]
        target = np.concatenate([shifts[:, 0], shifts[:, 1]])
        w2 = np.concatenate([weights, weights])
        if rows >= 3:
            solution = np.linalg.lstsq(design * w2[:, None], target * w2, rcond=None)[0]
            tx += float(solution[0])
            ty += float(solution[1])
            scale = float(solution[2])
            rotation = float(solution[3])
        else:
            tx += float(np.average(shifts[:, 0], weights=weights))
            ty += float(np.average(shifts[:, 1], weights=weights))
    else:
        tx, ty = gx, gy
    return tx / width, ty / height, math.log1p(scale), rotation, residual, confidence


def _block_shifts(a: Plane, b: Plane, grid: int) -> list[tuple[float, float, float, float, float]]:
    """(x, y of the block centre in ``a``, remaining shift x, y, weight) for each usable block."""
    height, width = a.shape
    size_h, size_w = height // grid, width // grid
    while grid > 1 and min(size_h, size_w) < _MIN_BLOCK:
        grid -= 1
        size_h, size_w = height // grid, width // grid
    if min(size_h, size_w) < _MIN_BLOCK:
        return []
    window = _hann((size_h, size_w))
    found: list[tuple[float, float, float, float, float]] = []
    for row in range(grid):
        for column in range(grid):
            top, left = row * size_h, column * size_w
            sx, sy, peak = _correlate(
                a[top : top + size_h, left : left + size_w],
                b[top : top + size_h, left : left + size_w],
                window,
            )
            if peak >= _MIN_BLOCK_CONFIDENCE:
                found.append((left + size_w / 2.0, top + size_h / 2.0, sx, sy, peak))
    return found
