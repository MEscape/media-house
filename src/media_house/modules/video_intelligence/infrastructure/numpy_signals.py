"""NumPy engines for the per-frame signals (shots) and the picture-quality signals.

They read the frames FFmpeg decoded once (``DecodedFrames``) in bounded chunks from a memory map,
never the whole video, and measure only raw values. What a value means is decided later by the
derivations. CPU only: these are cheap array operations, and a GPU would not change the values.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.signals import QualitySignals, ShotSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerId, DeviceKind
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import DecodedFrames
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, UnexpectedError

#: Revision of how these signals are computed. Part of the cache identity.
REVISION = "1"
_DECIMALS = 6
#: Rough budget of float32 values held at once while measuring a chunk of frames.
_CHUNK_VALUES = 8_000_000
_NOISE_DIVISOR = (
    0.6745 * 6.0
)  # median(|Immerkaer response|) = 0.6745 * 6 * sigma for Gaussian noise
_MIN_SPREAD = 0.1

type Plane = NDArray[np.float32]


def own(video: DecodedVideo) -> DecodedFrames:
    if not isinstance(video, DecodedFrames):
        raise TypeError("the frames were not produced by this module's frame source")
    return video


def read_frames(
    path: Path, height: int, width: int, first: int, count: int, channels: int = 1
) -> NDArray[np.uint8]:
    """``count`` raw frames starting at frame ``first`` (fewer at the end of the file).

    Grey frames come back as (n, h, w), colour frames (``channels=3``) as (n, h, w, 3). Read, not
    memory-mapped: nothing keeps the scratch file open, so it can be deleted at once (Windows
    refuses to delete a mapped file).
    """
    per_frame = height * width * channels
    data = np.fromfile(path, dtype=np.uint8, count=count * per_frame, offset=first * per_frame)
    shape = (-1, height, width) if channels == 1 else (-1, height, width, channels)
    shaped: NDArray[np.uint8] = data.reshape(shape)
    return shaped


def engine_identity() -> dict[str, str]:
    return {"numpy": np.__version__, "signal_engine": REVISION}


def rounded(values: NDArray[Any]) -> tuple[float, ...]:
    return tuple(np.round(values.astype(np.float64), _DECIMALS).tolist())


def guarded[T](what: str, work: Callable[[], T]) -> T:
    """Run a measurement, turning a numerical or memory failure into a typed external error."""
    try:
        return work()
    except (ValueError, ArithmeticError, MemoryError, OSError) as exc:
        raise ExternalSystemError(f"{what} failed: {exc}") from exc


class NumpyShotAnalyzer:
    """Brightness, contrast and frame-to-frame change of every frame (``AnalyzerId.SHOTS``)."""

    analyzer = AnalyzerId.SHOTS

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
    ) -> ShotSignals:
        frames = own(video)
        if frames.dense_path is None or frames.dense_count == 0:
            raise UnexpectedError("the shot analyzer needs the dense decode")
        path = frames.dense_path
        return guarded(
            "Measuring frame changes", lambda: self._measure(path, frames, request, cancellation)
        )

    def _measure(
        self,
        path: Path,
        frames: DecodedFrames,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> ShotSignals:
        count = frames.dense_count
        height, width = frames.dense_size
        gap = request.settings.long_gap_frames
        bins = request.settings.histogram_bins
        chunk = max(16, min(512, _CHUNK_VALUES // (height * width)))

        series = {
            name: np.zeros(count) for name in ("mean", "std", "diff1", "diff2", "long", "hist")
        }
        for start in range(0, count, chunk):
            cancellation.raise_if_cancelled()
            low = max(0, start - gap)  # the extra frames before the chunk serve the comparisons
            raw = read_frames(path, height, width, low, min(count, start + chunk) - low)
            block = raw.astype(np.float32) / 255.0
            offset = start - low
            flat = block.reshape(block.shape[0], -1)
            series["mean"][start:][: len(flat) - offset] = flat.mean(axis=1)[offset:]
            series["std"][start:][: len(flat) - offset] = flat.std(axis=1)[offset:]
            for name, distance in (("diff1", 1), ("diff2", 2), ("long", gap)):
                if block.shape[0] <= distance:
                    continue  # fewer frames than the distance: there is nothing to compare
                change = (
                    np.abs(block[distance:] - block[:-distance])
                    .reshape(block.shape[0] - distance, -1)
                    .mean(axis=1)
                )
                _store(series[name], low, distance, offset, change)
            binned = (raw.reshape(raw.shape[0], -1).astype(np.int32) * bins) // 256
            binned += (np.arange(raw.shape[0], dtype=np.int32) * bins)[:, None]
            counts = np.bincount(binned.ravel(), minlength=raw.shape[0] * bins).reshape(-1, bins)
            share = counts / float(height * width)
            if block.shape[0] > 1:
                moved = 0.5 * np.abs(share[1:] - share[:-1]).sum(axis=1)
                _store(series["hist"], low, 1, offset, moved)
        timeline = frames.timeline
        return ShotSignals(
            timebase=timeline.timebase,
            pts=timeline.pts[:count],
            end_pts=timeline.end_pts,
            estimated_timestamps=timeline.estimated,
            long_gap=gap,
            luma_mean=rounded(series["mean"]),
            luma_std=rounded(series["std"]),
            diff1=rounded(series["diff1"]),
            diff2=rounded(series["diff2"]),
            diff_long=rounded(series["long"]),
            hist1=rounded(series["hist"]),
        )


def _store(
    target: NDArray[np.float64], low: int, distance: int, offset: int, values: NDArray[Any]
) -> None:
    """Write comparison ``values`` (row ``k`` compares block frame ``k + distance`` with ``k``)
    for the frames of the current chunk only; frames before ``distance`` keep 0."""
    first_row = max(offset, distance)  # block rows of the chunk that have a frame to compare to
    chosen = values[first_row - distance :]
    target[low + first_row : low + first_row + len(chosen)] = chosen


def sample_frame(frames: DecodedFrames, frame: int) -> Plane:
    """The sampled picture of decoded frame ``frame`` (a multiple of the stride), luma 0-1."""
    if frames.sample_path is None:
        raise UnexpectedError("the analyzer needs the sampled decode")
    height, width = frames.sample_size
    position = frame // frames.stride
    raw = read_frames(frames.sample_path, height, width, position, 1)
    picture: Plane = (raw[0].astype(np.float32) / 255.0).astype(np.float32)
    return picture


class NumpyQualityAnalyzer:
    """Exposure, sharpness and noise of the planned sample frames (``AnalyzerId.QUALITY``)."""

    analyzer = AnalyzerId.QUALITY

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
    ) -> QualitySignals:
        frames = own(video)
        return guarded(
            "Measuring picture quality", lambda: self._measure(frames, request, cancellation)
        )

    def _measure(
        self, frames: DecodedFrames, request: MeasureRequest, cancellation: CancellationToken
    ) -> QualitySignals:
        settings = request.settings
        usable = [f for f in request.plan if f // frames.stride < frames.sample_count]
        columns: dict[str, list[float]] = {
            name: [] for name in ("p1", "p50", "p99", "clipped", "crushed", "sharp", "noise")
        }
        for frame in usable:
            cancellation.raise_if_cancelled()
            picture = sample_frame(frames, frame)
            p1, p50, p99 = (float(v) for v in np.percentile(picture, [1, 50, 99]))
            columns["p1"].append(p1)
            columns["p50"].append(p50)
            columns["p99"].append(p99)
            columns["clipped"].append(float((picture >= settings.clip_level).mean()))
            columns["crushed"].append(float((picture <= settings.crush_level).mean()))
            columns["sharp"].append(_sharpness(picture, p99 - p1))
            columns["noise"].append(_noise(picture))
        return QualitySignals(
            stride=frames.stride,
            frames=tuple(usable),
            luma_p1=rounded(np.array(columns["p1"])),
            luma_p50=rounded(np.array(columns["p50"])),
            luma_p99=rounded(np.array(columns["p99"])),
            clipped=rounded(np.array(columns["clipped"])),
            crushed=rounded(np.array(columns["crushed"])),
            sharpness=rounded(np.array(columns["sharp"])),
            noise_sigma=rounded(np.array(columns["noise"])),
        )


def _sharpness(picture: Plane, spread: float) -> float:
    """99th percentile of the gradient magnitude relative to the tonal spread (edges dominate)."""
    gx = (picture[1:-1, 2:] - picture[1:-1, :-2]) / 2.0
    gy = (picture[2:, 1:-1] - picture[:-2, 1:-1]) / 2.0
    return float(np.percentile(np.hypot(gx, gy), 99)) / max(spread, _MIN_SPREAD)


def _noise(picture: Plane) -> float:
    """Luma noise sigma by Immerkaer's robust estimator (median of the response)."""
    centre = picture[1:-1, 1:-1]
    response = (
        4.0 * centre
        - 2.0 * (picture[:-2, 1:-1] + picture[2:, 1:-1] + picture[1:-1, :-2] + picture[1:-1, 2:])
        + (picture[:-2, :-2] + picture[:-2, 2:] + picture[2:, :-2] + picture[2:, 2:])
    )
    return float(np.median(np.abs(response)) / _NOISE_DIVISOR)
