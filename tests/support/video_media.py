"""Synthetic but realistic test footage: a lit scene with sky, ground, skin, a grey card and
saturated objects, rendered to real video files with known exposure, colour and detail.

Every clip is a display-referred Rec.709 picture that is then transformed (darkened, tinted,
flattened into a log curve, blurred, noised, ...) before it is encoded, so a test knows exactly
what is wrong with the footage it feeds in.
"""

import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.infrastructure import color_science as cs
from tests.support.media_factories import ffmpeg

type Image = NDArray[np.float64]

WIDTH, HEIGHT = 640, 360
BT709_TAGS = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"


def scene(width: int = WIDTH, height: int = HEIGHT) -> Image:
    """A display-referred picture (0-1): well exposed, neutral, with texture and a grey card."""
    y = np.linspace(0.0, 1.0, height)[:, None, None]
    x = np.linspace(0.0, 1.0, width)[None, :, None]
    sky = np.array([0.35, 0.55, 0.85]) * (0.75 + 0.25 * (1 - y))
    ground = np.array([0.42, 0.36, 0.28]) * (0.8 + 0.2 * y)
    image = np.where(y < 0.45, sky, ground) * np.ones((1, width, 1))
    texture = 0.035 * np.sin(x * 160.0) * np.sin(y * 110.0) + 0.02 * np.sin(x * 420.0 + y * 90.0)
    image = image + texture
    rows, cols = np.arange(height)[:, None], np.arange(width)[None, :]

    def patch(top: float, left: float, bottom: float, right: float, rgb: Sequence[float]) -> None:
        mask = (
            (rows >= top * height)
            & (rows < bottom * height)
            & (cols >= left * width)
            & (cols < right * width)
        )
        image[mask] = np.asarray(rgb)

    patch(0.55, 0.06, 0.95, 0.30, (0.47, 0.47, 0.47))  # neutral grey card
    patch(0.55, 0.34, 0.80, 0.50, (0.80, 0.60, 0.50))  # skin
    patch(0.55, 0.54, 0.80, 0.66, (0.75, 0.20, 0.18))  # red object
    patch(0.55, 0.70, 0.80, 0.82, (0.20, 0.60, 0.25))  # green object
    patch(0.55, 0.86, 0.80, 0.97, (0.85, 0.85, 0.80))  # bright wall
    patch(0.84, 0.34, 0.97, 0.50, (0.02, 0.02, 0.02))  # deep shadow
    patch(0.84, 0.54, 0.97, 0.66, (0.92, 0.92, 0.92))  # white highlight
    return np.asarray(np.clip(image, 0.0, 1.0))


# --- ways footage can be wrong (each takes and returns display-referred Rec.709 RGB) -------------
def exposure(image: Image, stops: float) -> Image:
    linear = np.power(image, 2.4) * 2.0**stops
    return np.asarray(np.clip(np.power(np.clip(linear, 0, 1), 1 / 2.4), 0, 1))


def tint(image: Image, red: float, blue: float) -> Image:
    linear = np.power(image, 2.4) * np.array([red, 1.0, blue])
    return np.asarray(np.clip(np.power(np.clip(linear, 0, 1), 1 / 2.4), 0, 1))


def saturate(image: Image, factor: float) -> Image:
    luma = image @ np.array([0.2126, 0.7152, 0.0722])
    return np.asarray(np.clip(luma[..., None] + factor * (image - luma[..., None]), 0, 1))


def flatten(image: Image, low: float = 0.15, high: float = 0.85) -> Image:
    """A flat, low-contrast picture: the tonal range squeezed into [low, high]."""
    return np.asarray(low + (high - low) * image)


def blur(image: Image, sigma: float) -> Image:
    from scipy.ndimage import gaussian_filter

    return np.asarray(gaussian_filter(image, sigma=(sigma, sigma, 0)))


def encode_log(image: Image, color: ColorSpec) -> Image:
    """The scene as a camera writing ``color`` would record it (scene-referred capture)."""
    linear = np.power(image, 2.4)
    return np.asarray(np.clip(cs.TRANSFERS[color.transfer].encode(linear), 0.0, 1.0))


PROTUNE_FLAT = ColorSpec(Transfer.GOPRO_PROTUNE, Primaries.BT709)


def write_clip(
    path: Path,
    frame: Callable[[int], Image],
    *,
    frames: int = 16,
    fps: str = "30000/1001",
    pix_fmt: str = "yuv420p",
    tags: str = BT709_TAGS,
    metadata: Mapping[str, str] | None = None,
    audio: bool = True,
    extra: Sequence[str] = (),
) -> Path:
    """Encode ``frame(i)`` (RGB 0-1) as H.264 video, optionally with a tone as audio."""
    height, width = frame(0).shape[:2]
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "frames.rgb"
        with raw.open("wb") as handle:
            for index in range(frames):
                data = np.round(np.clip(frame(index), 0, 1) * 65535).astype("<u2")
                handle.write(data.tobytes())
        command = [
            "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{width}x{height}", "-r", fps,
            "-i", str(raw),
        ]  # fmt: skip
        if audio:
            seconds = frames * (1001 / 30000 if fps == "30000/1001" else 1 / float(fps))
            command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds:.3f}"]
        command += [
            "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,"
                   f"format={pix_fmt}" + (f",{tags}" if tags else ""),
            "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
        ]  # fmt: skip
        if audio:
            command += ["-c:a", "aac", "-shortest"]
        for key, value in (metadata or {}).items():
            command += ["-metadata", f"{key}={value}"]
        command += ["-movflags", "+use_metadata_tags", *extra, str(path)]
        ffmpeg(*command)
    return path


def rng(seed: int = 3) -> np.random.Generator:
    return np.random.default_rng(seed)


def noisy(image: Image, sigma: float, seed: int) -> Image:
    return np.asarray(np.clip(image + rng(seed).standard_normal(image.shape) * sigma, 0, 1))


def probe(path: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-show_streams", "-of", "default=nw=1", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout
