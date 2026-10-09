"""Raw measurements ("signals") stored per analyzer: columns of numbers aligned to frames.

Signals are the cached truth. They carry no judgement: shots, camera movement and quality
descriptions are derived from them (see ``derive``) and can be re-derived with other thresholds
without decoding again.

Conventions
-----------
* A frame is addressed by its index in presentation order from the first presented frame (0).
* ``pts`` is in ticks of ``timebase``; both are exact, nothing is stored as float seconds.
* Pictures are in DISPLAY orientation (rotation applied), brightness is 0-1 full range.
* Camera-motion values describe how the PICTURE CONTENT moved between two frames, as a fraction
  of the frame size (``tx`` right, ``ty`` down). A camera that pans right moves the content left.
"""

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from media_house.core.domain import FrameTime, Rational
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.values import EntityKind
from media_house.shared.errors import InvariantViolation


def _same_length(name: str, expected: int, **columns: Sequence[object]) -> None:
    for column, values in columns.items():
        if len(values) != expected:
            raise InvariantViolation(
                f"{name}: column {column} has {len(values)} values, expected {expected}"
            )


def _finite(name: str, **columns: Sequence[float]) -> None:
    for column, values in columns.items():
        if not all(math.isfinite(v) for v in values):
            raise InvariantViolation(f"{name}: column {column} contains a non-finite value")


@dataclass(frozen=True, slots=True)
class ShotSignals:
    """One value per decoded frame (all frames, at proxy resolution)."""

    timebase: Rational
    #: Presentation timestamp of every frame, non-decreasing.
    pts: tuple[int, ...]
    #: End of the last frame (``pts`` of the last frame plus its duration).
    end_pts: int
    #: Frames whose timestamp the decoder did not supply and was interpolated.
    estimated_timestamps: int
    #: Distance (in frames) used for ``diff_long``.
    long_gap: int
    luma_mean: tuple[float, ...]
    luma_std: tuple[float, ...]
    #: Mean absolute luma difference to the frame before (0 for the first frame).
    diff1: tuple[float, ...]
    #: ... to the frame two before (0 for the first two frames).
    diff2: tuple[float, ...]
    #: ... to the frame ``long_gap`` before (0 where there is none).
    diff_long: tuple[float, ...]
    #: Half the L1 distance of the luma histograms to the frame before (0-1).
    hist1: tuple[float, ...]

    def __post_init__(self) -> None:
        count = len(self.pts)
        if count < 1:
            raise InvariantViolation("Shot signals need at least one frame")
        _same_length(
            "shot signals",
            count,
            luma_mean=self.luma_mean,
            luma_std=self.luma_std,
            diff1=self.diff1,
            diff2=self.diff2,
            diff_long=self.diff_long,
            hist1=self.hist1,
        )
        _finite(
            "shot signals",
            luma_mean=self.luma_mean,
            luma_std=self.luma_std,
            diff1=self.diff1,
            diff2=self.diff2,
            diff_long=self.diff_long,
            hist1=self.hist1,
        )
        if any(b < a for a, b in zip(self.pts, self.pts[1:], strict=False)):
            raise InvariantViolation("Frame timestamps must not decrease")
        if self.end_pts < self.pts[-1]:
            raise InvariantViolation("The end of the video lies before its last frame")

    @property
    def frame_count(self) -> int:
        return len(self.pts)

    def time_of(self, frame: int) -> FrameTime:
        return FrameTime(frame, self.pts[frame], self.timebase)

    def end_time(self) -> FrameTime:
        return FrameTime(self.frame_count, self.end_pts, self.timebase)

    @property
    def frames_per_second(self) -> float:
        """Average rate over the whole video, from the timestamps."""
        span = (self.end_pts - self.pts[0]) * self.timebase.value
        return self.frame_count / span if span > 0 else 0.0

    def frame_at_or_before(self, pts: int) -> int:
        """Index of the last frame presented at or before ``pts`` (0 if before the first)."""
        return max(0, bisect_left(self.pts, pts + 1) - 1)


@dataclass(frozen=True, slots=True)
class MotionSignals:
    """One estimate per pair of consecutive SAMPLED frames."""

    #: Distance in frames between candidate sample positions (the decode stride).
    stride: int
    #: The later and the earlier frame of each pair.
    frames: tuple[int, ...]
    prev_frames: tuple[int, ...]
    tx: tuple[float, ...]
    ty: tuple[float, ...]
    #: Natural logarithm of the picture scale change (positive: content grows).
    log_scale: tuple[float, ...]
    #: Rotation of the content in radians (counter-clockwise positive).
    rotation: tuple[float, ...]
    #: Mean luma difference left after compensating the global motion (0-1).
    residual: tuple[float, ...]
    #: Peak sharpness of the correlation (0-1); low when the picture has no usable texture.
    confidence: tuple[float, ...]

    def __post_init__(self) -> None:
        count = len(self.frames)
        _same_length(
            "motion signals",
            count,
            prev_frames=self.prev_frames,
            tx=self.tx,
            ty=self.ty,
            log_scale=self.log_scale,
            rotation=self.rotation,
            residual=self.residual,
            confidence=self.confidence,
        )
        _finite(
            "motion signals",
            tx=self.tx,
            ty=self.ty,
            log_scale=self.log_scale,
            rotation=self.rotation,
            residual=self.residual,
            confidence=self.confidence,
        )
        if self.stride < 1:
            raise InvariantViolation("The sampling stride must be at least 1")
        if any(p >= f for p, f in zip(self.prev_frames, self.frames, strict=True)):
            raise InvariantViolation("A motion pair must run forward in time")


@dataclass(frozen=True, slots=True)
class QualitySignals:
    """One measurement per SAMPLED frame (luma only, proxy resolution)."""

    stride: int
    frames: tuple[int, ...]
    luma_p1: tuple[float, ...]
    luma_p50: tuple[float, ...]
    luma_p99: tuple[float, ...]
    #: Share of pixels at or above the clip level / at or below the crush level.
    clipped: tuple[float, ...]
    crushed: tuple[float, ...]
    #: 99th percentile of the gradient magnitude relative to the tonal spread.
    sharpness: tuple[float, ...]
    #: Luma noise sigma by robust (median) estimation, 0-1 scale.
    noise_sigma: tuple[float, ...]

    def __post_init__(self) -> None:
        count = len(self.frames)
        columns = {
            "luma_p1": self.luma_p1,
            "luma_p50": self.luma_p50,
            "luma_p99": self.luma_p99,
            "clipped": self.clipped,
            "crushed": self.crushed,
            "sharpness": self.sharpness,
            "noise_sigma": self.noise_sigma,
        }
        _same_length("quality signals", count, **columns)
        _finite("quality signals", **columns)
        if self.stride < 1:
            raise InvariantViolation("The sampling stride must be at least 1")


# --- model-based and picture-geometry signals --------------------------------------------------
# Every one of them is measured on a subset of the decoded frames (``frames``): a frame in
# ``frames`` with no row simply had nothing in it, a frame NOT in ``frames`` was not looked at.


def _frames_ok(name: str, frames: Sequence[int], rows: Sequence[int]) -> None:
    if any(b <= a for a, b in pairwise(frames)):
        raise InvariantViolation(f"{name}: analysed frames must strictly increase")
    unknown = set(rows) - set(frames)
    if unknown:
        raise InvariantViolation(f"{name}: rows refer to frames that were not analysed")


@dataclass(frozen=True, slots=True)
class DetectionRow:
    frame: int
    kind: EntityKind
    #: The detector's own class name (for example ``dog``); ``kind`` is the coarse grouping.
    label: str
    confidence: float
    box: BBox


@dataclass(frozen=True, slots=True)
class DetectionSignals:
    """Objects, people, animals and vehicles found in the analysed frames."""

    #: Model identity and weights hash, so a different model is a different document.
    model: str
    frames: tuple[int, ...]
    rows: tuple[DetectionRow, ...]

    def __post_init__(self) -> None:
        _frames_ok("detection signals", self.frames, [r.frame for r in self.rows])
        _finite("detection signals", confidence=[r.confidence for r in self.rows])


@dataclass(frozen=True, slots=True)
class FaceRow:
    """One face in one frame. Angles in degrees; the other values are 0-1 cues."""

    frame: int
    box: BBox
    #: The detector confidence that this is a face.
    confidence: float
    #: Head pose: yaw (positive: the person turned to their left), pitch (positive: up), roll.
    yaw: float
    pitch: float
    roll: float
    #: Iris position inside the eye opening, -1..1, 0 = centred (looking straight on).
    gaze_x: float
    gaze_y: float
    #: How open each eye is (1 open, 0 closed), the smile cue and how far the jaw is open.
    eye_open_left: float
    eye_open_right: float
    smile: float
    mouth_open: float
    #: Sharpness of the face crop relative to its tonal spread (same measure as picture sharpness).
    sharpness: float


@dataclass(frozen=True, slots=True)
class FaceSignals:
    model: str
    frames: tuple[int, ...]
    rows: tuple[FaceRow, ...]

    def __post_init__(self) -> None:
        _frames_ok("face signals", self.frames, [r.frame for r in self.rows])
        _finite(
            "face signals",
            yaw=[r.yaw for r in self.rows],
            pitch=[r.pitch for r in self.rows],
            gaze=[r.gaze_x + r.gaze_y for r in self.rows],
        )


@dataclass(frozen=True, slots=True)
class PoseRow:
    """One body: 33 joints as (x, y) pairs in the picture, and how visible the body is."""

    frame: int
    box: BBox
    visibility: float
    joints: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class HandRow:
    """One hand: 21 landmarks as (x, y) pairs and which hand it is."""

    frame: int
    side: str
    box: BBox
    landmarks: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BodySignals:
    model: str
    frames: tuple[int, ...]
    poses: tuple[PoseRow, ...]
    hands: tuple[HandRow, ...]

    def __post_init__(self) -> None:
        rows = [r.frame for r in self.poses] + [r.frame for r in self.hands]
        _frames_ok("body signals", self.frames, rows)
        if any(len(r.joints) != 66 for r in self.poses) or any(
            len(r.landmarks) != 42 for r in self.hands
        ):
            raise InvariantViolation("body signals: a pose has 33 joints and a hand 21 landmarks")


@dataclass(frozen=True, slots=True)
class TextRow:
    frame: int
    text: str
    confidence: float
    box: BBox


@dataclass(frozen=True, slots=True)
class TextSignals:
    """Text found on screen by OCR. ``frames`` are the frames that were read."""

    model: str
    frames: tuple[int, ...]
    rows: tuple[TextRow, ...]

    def __post_init__(self) -> None:
        _frames_ok("text signals", self.frames, [r.frame for r in self.rows])


@dataclass(frozen=True, slots=True)
class EmbeddingSignals:
    """One L2-normalised image embedding per analysed frame, and the zero-shot vocabulary.

    ``label_vectors`` are the text embeddings of the fixed vocabulary (``label_names``), measured
    with the same model, so classifying a frame is a dot product in the pure domain.
    """

    model: str
    dim: int
    vocabulary_version: int
    frames: tuple[int, ...]
    vectors: tuple[tuple[float, ...], ...]
    label_names: tuple[str, ...]
    label_vectors: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        _same_length("embedding signals", len(self.frames), vectors=self.vectors)
        _same_length("embedding signals", len(self.label_names), label_vectors=self.label_vectors)
        if any(len(v) != self.dim for v in (*self.vectors, *self.label_vectors)):
            raise InvariantViolation("embedding signals: every vector has the model dimension")
        if any(b <= a for a, b in zip(self.frames, self.frames[1:], strict=False)):
            raise InvariantViolation("embedding signals: analysed frames must strictly increase")


@dataclass(frozen=True, slots=True)
class AppearanceRow:
    """Embedding of the picture inside one detected person box."""

    frame: int
    box: BBox
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class AppearanceSignals:
    """Per-person appearance embeddings, the evidence for anonymous identity clusters."""

    model: str
    frames: tuple[int, ...]
    rows: tuple[AppearanceRow, ...]

    def __post_init__(self) -> None:
        _frames_ok("appearance signals", self.frames, [r.frame for r in self.rows])


@dataclass(frozen=True, slots=True)
class SaliencySignals:
    """Where the eye is drawn in each analysed frame (spectral residual, no model)."""

    frames: tuple[int, ...]
    #: Centre of attention as picture fractions, how strong its peak is and how spread out.
    cx: tuple[float, ...]
    cy: tuple[float, ...]
    peak: tuple[float, ...]
    spread: tuple[float, ...]

    def __post_init__(self) -> None:
        columns = {"cx": self.cx, "cy": self.cy, "peak": self.peak, "spread": self.spread}
        _same_length("saliency signals", len(self.frames), **columns)
        _finite("saliency signals", **columns)


@dataclass(frozen=True, slots=True)
class GeometrySignals:
    """Horizon, vertical lines, colour and light of each analysed colour frame."""

    frames: tuple[int, ...]
    #: Tilt of the dominant near-horizontal / near-vertical lines in degrees (0 = level / plumb)
    #: and how well the picture supports that reading (0-1).
    horizon_tilt: tuple[float, ...]
    horizon_support: tuple[float, ...]
    vertical_tilt: tuple[float, ...]
    vertical_support: tuple[float, ...]
    #: Mean colour (0-1) and the luma contrast of the shadows against the lit parts (0-1).
    mean_red: tuple[float, ...]
    mean_green: tuple[float, ...]
    mean_blue: tuple[float, ...]
    shadow_contrast: tuple[float, ...]
    #: Mean luma of the main subject box and of the rest of the picture; 0 when no subject box.
    subject_luma: tuple[float, ...]
    surround_luma: tuple[float, ...]
    #: Strength (0-1) of a straight seam across the picture: a split screen is one.
    seam_vertical: tuple[float, ...]
    seam_horizontal: tuple[float, ...]
    #: Share of edge pixels outside the subject box (the whole picture when there is none) and
    #: inside it (0 when there is none): how busy the background is and how crisp the subject.
    edge_outside: tuple[float, ...]
    edge_inside: tuple[float, ...]

    def __post_init__(self) -> None:
        columns = {
            "horizon_tilt": self.horizon_tilt,
            "horizon_support": self.horizon_support,
            "vertical_tilt": self.vertical_tilt,
            "vertical_support": self.vertical_support,
            "mean_red": self.mean_red,
            "mean_green": self.mean_green,
            "mean_blue": self.mean_blue,
            "shadow_contrast": self.shadow_contrast,
            "subject_luma": self.subject_luma,
            "surround_luma": self.surround_luma,
            "seam_vertical": self.seam_vertical,
            "seam_horizontal": self.seam_horizontal,
            "edge_outside": self.edge_outside,
            "edge_inside": self.edge_inside,
        }
        _same_length("geometry signals", len(self.frames), **columns)
        _finite("geometry signals", **columns)


@dataclass(frozen=True, slots=True)
class DescriptionSignals:
    """What a vision-language model says it sees in sparse keyframes (visible content only)."""

    model: str
    frames: tuple[int, ...]
    texts: tuple[str, ...]

    def __post_init__(self) -> None:
        _same_length("description signals", len(self.frames), texts=self.texts)
