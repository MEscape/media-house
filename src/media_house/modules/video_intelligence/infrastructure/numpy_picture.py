"""Classical picture measurements (NumPy/SciPy, no model): saliency and picture geometry.

* Saliency: spectral residual (Hou and Zhang): where the picture's spectrum departs from the
  smooth average spectrum is where the eye is drawn. Deterministic and cheap.
* Geometry: dominant near-horizontal and near-vertical line orientation from the gradient field,
  mean colour, shadow contrast, brightness of the subject against its surroundings, edge density
  and straight seams across the picture.

Tilt angles are in degrees and positive when the line is rotated CLOCKWISE as seen in the picture
(a horizon that falls toward the right edge, a vertical whose top leans to the right).
"""

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter, sobel, uniform_filter, zoom

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.signals import (
    DetectionRow,
    DetectionSignals,
    GeometrySignals,
    SaliencySignals,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    DeviceKind,
    EntityKind,
)
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import DecodedFrames
from media_house.modules.video_intelligence.infrastructure.numpy_signals import (
    Plane,
    engine_identity,
    guarded,
    own,
    read_frames,
    rounded,
    sample_frame,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import UnexpectedError

_SALIENCY_SIZE = 64
_EPSILON = 1e-9
_EDGE_LEVEL = 0.1  # gradient magnitude (luma fraction per pixel) that counts as an edge
_LINE_WINDOW = 20.0  # degrees around level / plumb that a line may be and still be read as one
_MIN_LINE_PIXELS = 50
_SEAM_BAND = (0.3, 0.7)
_SEAM_JUMP = 0.12
_SEAM_FLOOR = 0.6
_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

type Rgb = NDArray[np.uint8]


def rgb_frame(frames: DecodedFrames, frame: int) -> Rgb:
    """The colour picture of decoded frame ``frame`` (a multiple of the colour stride)."""
    if frames.rgb_path is None:
        raise UnexpectedError("the analyzer needs the colour decode")
    height, width = frames.rgb_size
    picture: Rgb = read_frames(frames.rgb_path, height, width, frame // frames.rgb_stride, 1, 3)[0]
    return picture


def usable_rgb_frames(frames: DecodedFrames, plan: tuple[int, ...]) -> list[int]:
    return [f for f in plan if f // frames.rgb_stride < frames.rgb_count]


# --- saliency ----------------------------------------------------------------------------------
def saliency_of(gray: Plane) -> tuple[float, float, float, float]:
    """(centre x, centre y, peak strength, spread) of a grey picture, all as 0-1 fractions."""
    small = zoom(gray, (_SALIENCY_SIZE / gray.shape[0], _SALIENCY_SIZE / gray.shape[1]), order=1)
    spectrum = np.fft.fft2(small)
    log_amplitude = np.log(np.abs(spectrum) + _EPSILON)
    residual = log_amplitude - uniform_filter(log_amplitude, size=3)
    saliency = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))) ** 2
    saliency = gaussian_filter(saliency, 2.5)
    span = float(saliency.max() - saliency.min())
    if span < _EPSILON:
        return 0.5, 0.5, 0.0, 1.0
    normal = (saliency - saliency.min()) / span
    weight = normal**4
    total = float(weight.sum())
    rows, columns = np.indices(normal.shape)
    cx = float((weight * (columns + 0.5)).sum() / total / normal.shape[1])
    cy = float((weight * (rows + 0.5)).sum() / total / normal.shape[0])
    spread = float(
        np.sqrt(
            (weight * ((columns + 0.5) / normal.shape[1] - cx) ** 2).sum() / total
            + (weight * ((rows + 0.5) / normal.shape[0] - cy) ** 2).sum() / total
        )
    )
    return cx, cy, float(1.0 - normal.mean()), spread


class NumpySaliencyAnalyzer:
    """Where the eye is drawn in each planned grey sample (``AnalyzerId.SALIENCY``)."""

    analyzer = AnalyzerId.SALIENCY

    def identity(self) -> dict[str, str]:
        return {**engine_identity(), "saliency": "spectral-residual-1"}

    def unavailable(self, allow_downloads: bool) -> str | None:
        return None

    def device(self, requested: DeviceKind) -> str:
        return "cpu"

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> SaliencySignals:
        frames = own(video)
        usable = [f for f in request.plan if f // frames.stride < frames.sample_count]

        def work() -> SaliencySignals:
            columns: list[tuple[float, float, float, float]] = []
            for frame in usable:
                cancellation.raise_if_cancelled()
                columns.append(saliency_of(sample_frame(frames, frame)))
            data = np.array(columns).reshape(-1, 4)
            return SaliencySignals(
                frames=tuple(usable),
                cx=rounded(data[:, 0]),
                cy=rounded(data[:, 1]),
                peak=rounded(data[:, 2]),
                spread=rounded(data[:, 3]),
            )

        return guarded("Measuring saliency", work)


# --- geometry ----------------------------------------------------------------------------------
def _line_tilt(mag: Plane, u: Plane, centre: float, window: float) -> tuple[float, float]:
    """Weighted mean clockwise tilt of the edges whose line orientation lies within ``window``
    degrees of ``centre`` (an orientation 0-180), and how much of the edge energy they carry."""
    strong = mag >= _EDGE_LEVEL
    offset = ((u - centre + 90.0) % 180.0) - 90.0
    chosen = strong & (np.abs(offset) <= window)
    if int(chosen.sum()) < _MIN_LINE_PIXELS:
        return 0.0, 0.0
    weights = mag[chosen]
    tilt = float((offset[chosen] * weights).sum() / weights.sum())
    share = float(weights.sum() / max(float(mag[strong].sum()), _EPSILON))
    return tilt, min(1.0, 2.0 * share)


def _seam(luma: Plane) -> tuple[float, float]:
    """Strength (0-1) of a straight vertical / horizontal seam through the middle band."""

    def strength(difference: Plane, band: tuple[int, int]) -> float:
        share = (np.abs(difference) >= _SEAM_JUMP).mean(axis=0)[band[0] : band[1]]
        best = float(share.max()) if share.size else 0.0
        return max(0.0, (best - _SEAM_FLOOR) / (1.0 - _SEAM_FLOOR))

    height, width = luma.shape
    vertical = strength(
        np.diff(luma, axis=1), (int(_SEAM_BAND[0] * (width - 1)), int(_SEAM_BAND[1] * (width - 1)))
    )
    horizontal = strength(
        np.diff(luma, axis=0).T,
        (int(_SEAM_BAND[0] * (height - 1)), int(_SEAM_BAND[1] * (height - 1))),
    )
    return vertical, horizontal


def main_subject_box(rows: list[DetectionRow]) -> BBox | None:
    """The largest person box of a frame, else the largest box of any kind."""
    persons = [r for r in rows if r.kind is EntityKind.PERSON]
    pool = persons or rows
    return max(pool, key=lambda r: r.box.area).box if pool else None


def geometry_of(picture: Rgb, subject: BBox | None) -> tuple[float, ...]:
    """The ten geometry columns of one colour picture (see ``GeometrySignals``)."""
    colour = picture.astype(np.float32) / 255.0
    luma: Plane = (colour @ _LUMA).astype(np.float32)
    gx = sobel(luma, axis=1) / 4.0
    gy = sobel(luma, axis=0) / 4.0
    mag = np.hypot(gx, gy).astype(np.float32)
    u = (np.degrees(np.arctan2(gy, gx)) % 180.0).astype(np.float32)
    horizon, horizon_support = _line_tilt(mag, u, 90.0, _LINE_WINDOW)
    vertical, vertical_support = _line_tilt(mag, u, 0.0, _LINE_WINDOW)
    low, high = (float(v) for v in np.percentile(luma, [5, 95]))
    height, width = luma.shape
    edges = mag >= _EDGE_LEVEL
    subject_luma = surround_luma = edge_inside = 0.0
    edge_outside = float(edges.mean())
    if subject is not None:
        mask = np.zeros(luma.shape, dtype=bool)
        mask[
            int(subject.y0 * height) : max(int(subject.y1 * height), int(subject.y0 * height) + 1),
            int(subject.x0 * width) : max(int(subject.x1 * width), int(subject.x0 * width) + 1),
        ] = True
        if mask.any() and (~mask).any():
            subject_luma, surround_luma = float(luma[mask].mean()), float(luma[~mask].mean())
            edge_inside, edge_outside = float(edges[mask].mean()), float(edges[~mask].mean())
    seam_vertical, seam_horizontal = _seam(luma)
    return (
        horizon,
        horizon_support,
        vertical,
        vertical_support,
        float(colour[..., 0].mean()),
        float(colour[..., 1].mean()),
        float(colour[..., 2].mean()),
        (high - low) / (high + low + _EPSILON),
        subject_luma,
        surround_luma,
        seam_vertical,
        seam_horizontal,
        edge_outside,
        edge_inside,
    )


class NumpyGeometryAnalyzer:
    """Horizon, verticals, colour, light and seams of planned colour frames (``GEOMETRY``)."""

    analyzer = AnalyzerId.GEOMETRY

    def identity(self) -> dict[str, str]:
        return {**engine_identity(), "geometry": "gradient-orientation-1"}

    def unavailable(self, allow_downloads: bool) -> str | None:
        return None

    def device(self, requested: DeviceKind) -> str:
        return "cpu"

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> GeometrySignals:
        frames = own(video)
        usable = usable_rgb_frames(frames, request.rgb_plan)
        entities = request.dependencies.get(AnalyzerId.ENTITIES)
        by_frame: dict[int, list[DetectionRow]] = {}
        if isinstance(entities, DetectionSignals):
            for row in entities.rows:
                by_frame.setdefault(row.frame, []).append(row)

        def work() -> GeometrySignals:
            rows: list[tuple[float, ...]] = []
            for frame in usable:
                cancellation.raise_if_cancelled()
                subject = main_subject_box(by_frame.get(frame, []))
                rows.append(geometry_of(rgb_frame(frames, frame), subject))
            data = np.array(rows).reshape(-1, 14)
            c = [rounded(data[:, i]) for i in range(14)]
            return GeometrySignals(
                frames=tuple(usable),
                horizon_tilt=c[0],
                horizon_support=c[1],
                vertical_tilt=c[2],
                vertical_support=c[3],
                mean_red=c[4],
                mean_green=c[5],
                mean_blue=c[6],
                shadow_contrast=c[7],
                subject_luma=c[8],
                surround_luma=c[9],
                seam_vertical=c[10],
                seam_horizontal=c[11],
                edge_outside=c[12],
                edge_inside=c[13],
            )

        return guarded("Measuring picture geometry", work)
