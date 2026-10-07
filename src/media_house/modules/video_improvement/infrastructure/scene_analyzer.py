"""Measure a video from a handful of frames (NumPy), and predict what a colour plan will do.

FFmpeg decodes evenly spread frames once, as 16-bit full-range planar RGB: the whole frame
scaled down (exposure, colour, saturation) and a native-resolution centre crop (noise and
sharpness, which depend on pixel scale). Memory is bounded by the sample count, never by the
video length.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from media_house.modules.video_improvement.application.ports import Samples
from media_house.modules.video_improvement.domain.color import OUTPUT_COLOR, ColorSpec, Transfer
from media_house.modules.video_improvement.domain.errors import UnreadableSource
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.planning import ColorPlan
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.infrastructure import color_science as cs
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import (
    FfmpegTool,
    matrix_name,
    range_name,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ProcessFailedError

#: Revision of how samples are taken and measured (part of the processing identity).
REVISION = "1"
_SAMPLE_WIDTH = 640
_CROP_WIDTH, _CROP_HEIGHT = 512, 288
_CLIP_LEVEL = 0.985
_CRUSH_LEVEL = 0.02
_DARK_FOR_SATURATION = 0.1
_NEUTRAL_SATURATION = 0.18
_NEUTRAL_LUMINANCE = (0.04, 0.5)
_MIN_NEUTRAL_PIXELS = 500
#: For Gaussian noise, median(|Immerkaer response|) = 0.6745 * 6 * sigma.
_NOISE_DIVISOR = 0.6745 * 6.0
_MIN_SPREAD = 0.1
_MIN_SAMPLES = 3
type Frames = NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class SampleSet:
    """Decoded sample frames: ``frames`` (n, h, w, 3) and native ``crops`` (n, h, w, 3), 0-1."""

    frames: Frames
    crops: Frames
    #: Noise and sharpness of the crops, computed once (they do not depend on colour).
    _detail: list[tuple[float, float]] = field(default_factory=list, compare=False, repr=False)

    def detail(self) -> tuple[float, float]:
        if not self._detail:
            self._detail.append(_detail(self.crops))
        return self._detail[0]


def _own(samples: Samples) -> SampleSet:
    if not isinstance(samples, SampleSet):
        raise TypeError("samples were not produced by this analyzer")
    return samples


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


class NumpySceneAnalyzer:
    """Structurally implements ``application.ports.SceneAnalyzer``."""

    def __init__(self, tool: FfmpegTool) -> None:
        self._tool = tool

    @property
    def identity(self) -> str:
        return f"scene-analyzer-{REVISION}+color-{cs.REVISION}+{self._tool.version}"

    # --- sampling -----------------------------------------------------------------------------
    def sample(
        self,
        path: Path,
        facts: VideoFacts,
        count: int,
        work_dir: Path,
        cancellation: CancellationToken,
    ) -> SampleSet:
        width = min(_SAMPLE_WIDTH, facts.width) // 2 * 2 or 2
        height = _even(facts.height * width / facts.width)
        crop_w = min(facts.width, _CROP_WIDTH) // 2 * 2 or 2
        crop_h = min(facts.height, _CROP_HEIGHT) // 2 * 2 or 2
        convert = (
            f"in_range={range_name(facts)}:in_color_matrix={matrix_name(facts)}:"
            "out_range=pc:flags=area+accurate_rnd+full_chroma_int"
        )
        graph = (
            f"[0:v:0]fps={count / facts.duration:.9f}:"
            f"start_time={facts.duration / (2 * count):.6f},split=2[a][b];"
            f"[a]scale={width}:{height}:{convert},format=gbrp16le[s];"
            f"[b]crop={crop_w}:{crop_h},scale={convert},format=gbrp16le[c]"
        )
        scaled_file, crop_file = work_dir / "scaled.rgb", work_dir / "crop.rgb"
        # key frames only: evenly spread and far cheaper than decoding every frame, but a video
        # with few key frames yields too few samples, then everything is decoded instead
        for key_frames_only in (True, False):
            self._extract(path, graph, count, scaled_file, crop_file, key_frames_only, cancellation)
            frames = _read(scaled_file, height, width)
            crops = _read(crop_file, crop_h, crop_w)
            if len(frames) >= max(_MIN_SAMPLES, count // 2) and len(crops) == len(frames):
                return SampleSet(frames, crops)
        if len(frames) == 0 or len(crops) != len(frames):
            raise UnreadableSource("no frames could be decoded")
        return SampleSet(frames, crops)

    def _extract(
        self,
        path: Path,
        graph: str,
        count: int,
        scaled_file: Path,
        crop_file: Path,
        key_frames_only: bool,
        cancellation: CancellationToken,
    ) -> None:
        decode = ["-skip_frame", "nokey"] if key_frames_only else []
        try:
            self._tool.run(
                [
                    "-loglevel",
                    "error",
                    "-noautorotate",  # frames stay as stored: measurements do not need the rotation
                    *decode,
                    "-i",
                    str(path),
                    "-an",
                    "-sn",
                    "-filter_complex",
                    graph,
                    "-map",
                    "[s]",
                    "-frames:v",
                    str(count),
                    "-f",
                    "rawvideo",
                    str(scaled_file),
                    "-map",
                    "[c]",
                    "-frames:v",
                    str(count),
                    "-f",
                    "rawvideo",
                    str(crop_file),
                ],
                cancellation,
            )
        except ProcessFailedError as exc:
            raise UnreadableSource(f"frames could not be decoded ({exc})") from exc

    # --- measuring ------------------------------------------------------------------------------
    def measure(self, samples: Samples, color: ColorSpec) -> SceneMeasurements:
        """Measure the sampled frames; ``color`` says how their RGB is encoded."""
        own = _own(samples)
        return _measure(own.frames, own.detail(), color)

    def predict(self, samples: Samples, plan: ColorPlan) -> SceneMeasurements:
        """What the measurements would be after applying the colour plan to the frames."""
        transform = cs.ColorTransform(
            plan, cs.read_cube(Path(plan.look_lut)) if plan.look_lut else None
        )
        own = _own(samples)
        # pointwise colour maths: every second pixel in each direction measures the same picture;
        # noise and sharpness are taken from the source crops (the grade does not change them)
        frames = _transform(own.frames[:, ::2, ::2], transform)
        return _measure(frames, own.detail(), OUTPUT_COLOR)


def _read(path: Path, height: int, width: int) -> Frames:
    """Planar 16-bit frames (FFmpeg's ``gbrp16le``: green, blue and red planes) as RGB 0-1."""
    data = np.fromfile(path, dtype="<u2")
    per_frame = height * width * 3
    count = data.size // per_frame
    if count == 0:
        return np.zeros((0, height, width, 3), dtype=np.float32)
    planes = data[: count * per_frame].reshape(count, 3, height, width)
    rgb = planes[:, [2, 0, 1]].transpose(0, 2, 3, 1)
    scaled: Frames = (rgb / 65535.0).astype(np.float32)
    return scaled


def _transform(frames: Frames, transform: cs.ColorTransform) -> Frames:
    shape = frames.shape
    flat = frames.reshape(-1, 3).astype(np.float64)
    transformed: Frames = transform(flat).reshape(shape).astype(np.float32)
    return transformed


def _luma(rgb: NDArray[np.floating]) -> NDArray[np.float64]:
    return np.asarray(rgb @ np.array([0.2126, 0.7152, 0.0722]), dtype=np.float64)


def _measure(frames: Frames, detail: tuple[float, float], color: ColorSpec) -> SceneMeasurements:
    count = frames.shape[0]
    pixels = frames.reshape(-1, 3).astype(np.float64)
    luma = _luma(pixels)
    p1, p50, p99 = (float(v) for v in np.percentile(luma, [1, 50, 99]))

    # linear light; a colour space that cannot be transformed is read as Rec.709 (statistics only)
    basis = color if cs.supported(color) else ColorSpec(Transfer.BT709, OUTPUT_COLOR.primaries)
    linear = cs.to_working(pixels, basis)
    lin_luma = cs.luminance(linear)
    lin_p50, lin_p99 = (float(v) for v in np.percentile(lin_luma, [50, 99]))

    peak = pixels.max(axis=1)
    lowest = pixels.min(axis=1)
    saturation = np.where(peak > 0, (peak - lowest) / np.maximum(peak, 1e-6), 0.0)
    lit = peak > _DARK_FOR_SATURATION
    mean_saturation = float(saturation[lit].mean()) if lit.any() else 0.0

    neutral = (
        (saturation < _NEUTRAL_SATURATION)
        & (lin_luma > _NEUTRAL_LUMINANCE[0])
        & (lin_luma < _NEUTRAL_LUMINANCE[1])
        & (linear[:, 1] > 1e-6)
    )
    cast_red = cast_blue = None
    if int(neutral.sum()) >= _MIN_NEUTRAL_PIXELS:
        chosen = linear[neutral]
        cast_red = float(np.median(chosen[:, 0] / chosen[:, 1]))
        cast_blue = float(np.median(chosen[:, 2] / chosen[:, 1]))

    medians = np.median(lin_luma.reshape(count, -1), axis=1)
    variation = float(np.std(np.log2(np.maximum(medians, 1e-4))))

    noise, sharpness = detail
    return SceneMeasurements(
        frames=count,
        luma_p1=p1,
        luma_p50=p50,
        luma_p99=p99,
        linear_p50=lin_p50,
        linear_p99=lin_p99,
        highlight_clip=float((peak >= _CLIP_LEVEL).mean()),
        shadow_crush=float((luma <= _CRUSH_LEVEL).mean()),
        mean_saturation=mean_saturation,
        cast_red=cast_red,
        cast_blue=cast_blue,
        neutral_share=float(neutral.mean()),
        noise_sigma=noise,
        sharpness=sharpness,
        exposure_variation_stops=variation,
    )


def _detail(crops: Frames) -> tuple[float, float]:
    """Luma noise sigma (Immerkaer, robust median) and sharpness (99th percentile of the
    gradient magnitude, which edges dominate) of native-resolution crops."""
    luma = _luma(crops.astype(np.float64))  # (n, h, w)
    centre = luma[:, 1:-1, 1:-1]
    response = (
        4.0 * centre
        - 2.0 * (luma[:, :-2, 1:-1] + luma[:, 2:, 1:-1] + luma[:, 1:-1, :-2] + luma[:, 1:-1, 2:])
        + (luma[:, :-2, :-2] + luma[:, :-2, 2:] + luma[:, 2:, :-2] + luma[:, 2:, 2:])
    )
    noise = float(np.median(np.abs(response)) / _NOISE_DIVISOR)
    gx = (luma[:, 1:-1, 2:] - luma[:, 1:-1, :-2]) / 2.0
    gy = (luma[:, 2:, 1:-1] - luma[:, :-2, 1:-1]) / 2.0
    spread = float(np.percentile(luma, 99) - np.percentile(luma, 1))
    # relative to the tonal spread, so flat (low-contrast) footage is not mistaken for soft
    sharpness = float(np.percentile(np.hypot(gx, gy), 99)) / max(spread, _MIN_SPREAD)
    return noise, sharpness
