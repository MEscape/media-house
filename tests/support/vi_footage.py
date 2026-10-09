"""Synthetic footage with known ground truth for Video Intelligence tests.

Every clip is generated from a seeded random texture, so a test knows exactly where the cuts are,
how the camera moved and how blurred or noisy the picture is. Nothing is committed: clips are
rendered into a temporary directory by the tests that need them.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter, zoom

from tests.support.video_media import BT709_TAGS, write_clip

type Gray = NDArray[np.float64]
type FrameFn = Callable[[int], Gray]

WIDTH, HEIGHT = 320, 180
FPS = 30


def texture(seed: int, width: int = WIDTH, height: int = HEIGHT) -> Gray:
    """A textured picture (0.08-0.92) with detail at several scales; same seed, same picture."""
    rng = np.random.default_rng(seed)
    layers = sum(
        gaussian_filter(rng.standard_normal((height, width)), sigma) * weight
        for sigma, weight in ((1.2, 1.0), (4.0, 2.2), (14.0, 5.0))
    )
    layers = (layers - layers.mean()) / layers.std()
    return np.asarray(np.clip(0.5 + 0.17 * layers, 0.08, 0.92))


def to_rgb(gray: Gray) -> NDArray[np.float64]:
    return np.repeat(np.clip(gray, 0.0, 1.0)[:, :, None], 3, axis=2)


def write(
    path: Path,
    frame: FrameFn,
    frames: int,
    *,
    fps: str = str(FPS),
    extra: Sequence[str] = (),
) -> Path:
    """Encode ``frame(i)`` (gray 0-1) as a Rec.709 H.264 clip without audio."""
    return write_clip(
        path,
        lambda i: to_rgb(frame(i)),
        frames=frames,
        fps=fps,
        audio=False,
        tags=BT709_TAGS,
        extra=extra,
    )


# --- camera ---------------------------------------------------------------------------------------
def still(seed: int) -> FrameFn:
    picture = texture(seed)
    return lambda _i: picture


def pan(seed: int, pixels_per_frame: float, vertical: bool = False) -> FrameFn:
    """The camera moves; content shifts the other way. Positive speed: camera right (or down).

    Shifts are sub-pixel (linear interpolation), like real footage, not whole-pixel jumps.
    """
    margin = int(abs(pixels_per_frame) * 120) + 8
    extra = margin + 2  # one more pixel for the interpolation
    canvas = texture(seed, WIDTH + (0 if vertical else extra), HEIGHT + (extra if vertical else 0))
    axis = 0 if vertical else 1
    size = HEIGHT if vertical else WIDTH

    def frame(i: int) -> Gray:
        position = pixels_per_frame * i if pixels_per_frame >= 0 else margin + pixels_per_frame * i
        whole, fraction = int(np.floor(position)), position - np.floor(position)
        window = np.take(canvas, range(whole, whole + size + 1), axis=axis)
        low = np.take(window, range(size), axis=axis)
        high = np.take(window, range(1, size + 1), axis=axis)
        return np.asarray(low * (1 - fraction) + high * fraction)

    return frame


def push(seed: int, per_frame: float) -> FrameFn:
    """Zoom about the centre; positive ``per_frame`` grows the picture (a push in)."""
    canvas = texture(seed, WIDTH * 2, HEIGHT * 2)

    def frame(i: int) -> Gray:
        scale = (1.0 + per_frame) ** i
        crop_w, crop_h = WIDTH * 2 / (scale * 2), HEIGHT * 2 / (scale * 2)
        left, top = (WIDTH * 2 - crop_w) / 2, (HEIGHT * 2 - crop_h) / 2
        cw, ch = round(crop_w), round(crop_h)
        crop = canvas[int(top) : int(top) + ch, int(left) : int(left) + cw]
        fitted = zoom(crop, (HEIGHT / crop.shape[0], WIDTH / crop.shape[1]), order=1)
        return np.asarray(fitted[:HEIGHT, :WIDTH])

    return frame


def handheld(seed: int, amplitude: float, noise_seed: int = 5) -> FrameFn:
    """A static scene filmed by a shaking camera: random shifts of up to ``amplitude`` pixels."""
    margin = int(amplitude) + 2
    canvas = texture(seed, WIDTH + 2 * margin, HEIGHT + 2 * margin)
    rng = np.random.default_rng(noise_seed)
    shifts = rng.integers(-int(amplitude), int(amplitude) + 1, size=(4000, 2))

    def frame(i: int) -> Gray:
        dx, dy = shifts[i]
        return canvas[margin + dy : margin + dy + HEIGHT, margin + dx : margin + dx + WIDTH]

    return frame


# --- picture --------------------------------------------------------------------------------------
def blurred(frame: FrameFn, sigma: float) -> FrameFn:
    return lambda i: np.asarray(gaussian_filter(frame(i), sigma))


def noisy(frame: FrameFn, sigma: float, seed: int = 11) -> FrameFn:
    return lambda i: np.asarray(
        np.clip(
            frame(i) + np.random.default_rng(seed + i).standard_normal((HEIGHT, WIDTH)) * sigma,
            0,
            1,
        )
    )


def dimmed(frame: FrameFn, gain: float) -> FrameFn:
    return lambda i: frame(i) * gain


# --- editing --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Segment:
    """``frames`` frames of ``source``; ``fade_in`` / ``dissolve`` shape how it starts."""

    source: FrameFn
    frames: int
    #: Frames of a dissolve from the previous segment (overlapping its end) or fade from black.
    dissolve: int = 0
    fade_from_black: int = 0
    flash_at: tuple[int, ...] = ()


def edit(segments: Sequence[Segment]) -> tuple[FrameFn, int, list[int]]:
    """A programme of segments: its frame function, its length and the true boundary frames.

    A dissolve of ``n`` frames overlaps the previous segment's last ``n`` frames, so the total
    length is the sum of the lengths minus the overlaps. The boundary of a dissolve is its centre.
    """
    starts: list[int] = []
    cursor = 0
    boundaries: list[int] = []
    for index, segment in enumerate(segments):
        start = cursor - segment.dissolve if index else 0
        starts.append(start)
        if index:
            boundaries.append(start + segment.dissolve // 2 if segment.dissolve else start)
        cursor = start + segment.frames
    total = cursor

    def frame(i: int) -> Gray:
        index = max(k for k, s in enumerate(starts) if s <= i)
        segment = segments[index]
        local = i - starts[index]
        picture = segment.source(local)
        if segment.dissolve and local < segment.dissolve:
            before = segments[index - 1]
            previous = before.source(i - starts[index - 1])
            weight = (local + 1) / (segment.dissolve + 1)
            picture = previous * (1 - weight) + picture * weight
        if segment.fade_from_black and local < segment.fade_from_black:
            picture = picture * (local + 1) / (segment.fade_from_black + 1)
        if local in segment.flash_at:
            picture = np.clip(picture + 0.45, 0.0, 1.0)
        return np.asarray(picture)

    return frame, total, boundaries


def through_black(
    first: FrameFn, second: FrameFn, ramp: int, hold: int, lead: int
) -> tuple[FrameFn, int, int]:
    """``first`` fades to black over ``ramp`` frames, stays black ``hold``, ``second`` fades in.

    Returns the frame function, total length and the middle of the black hold (the true boundary).
    """
    total = lead + ramp + hold + ramp + lead

    def frame(i: int) -> Gray:
        if i < lead:
            return first(i)
        if i < lead + ramp:
            return first(i) * (1 - (i - lead + 1) / (ramp + 1))
        if i < lead + ramp + hold:
            return np.zeros((HEIGHT, WIDTH))
        if i < lead + ramp + hold + ramp:
            return second(i) * ((i - lead - ramp - hold + 1) / (ramp + 1))
        return second(i)

    return frame, total, lead + ramp + hold // 2
