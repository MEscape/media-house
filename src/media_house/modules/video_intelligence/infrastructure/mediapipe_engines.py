"""Faces, body and hands with MediaPipe Tasks (Apache-2.0), CPU, local model files.

* ``FACES``: FaceDetector (box and confidence) plus FaceLandmarker (478 landmarks and blendshapes).
  Head pose, gaze, eye openness, smile and mouth opening are computed from the landmarks and
  blendshapes. They are CUES of what is visible; no expression is called an emotion and nothing
  identifies a person.
* ``BODY``: PoseLandmarker (33 joints) and HandLandmarker (21 landmarks per hand).

Conventions: ``yaw`` is positive when the face is turned toward the RIGHT edge of the picture,
``pitch`` positive when looking up, ``roll`` positive when the head tilts clockwise; ``gaze_x`` is
positive toward the right edge of the picture and ``gaze_y`` positive upward. They come from
landmark geometry (2D), so they are approximations good to a few degrees, not a calibrated pose.

Tiered execution: when person detections exist, only frames where a person was found are read.
"""

import math
import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.geometry import BBox, iou
from media_house.modules.video_intelligence.domain.signals import (
    BodySignals,
    DetectionSignals,
    FaceRow,
    FaceSignals,
    HandRow,
    PoseRow,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    DeviceKind,
    EntityKind,
)
from media_house.modules.video_intelligence.infrastructure.model_files import (
    ModelSpec,
    ModelStore,
    library_missing,
)
from media_house.modules.video_intelligence.infrastructure.numpy_picture import (
    rgb_frame,
    usable_rgb_frames,
)
from media_house.modules.video_intelligence.infrastructure.numpy_signals import (
    guarded,
    own,
)
from media_house.modules.video_intelligence.infrastructure.torch_support import (
    OnceLoaded,
    package_version,
)
from media_house.shared.concurrency import CancellationToken

REVISION = "1"
_BASE = "https://storage.googleapis.com/mediapipe-models"
FACE_LANDMARKER = ModelSpec(
    "face_landmarker.task",
    f"{_BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "Apache-2.0",
    "64184e229b263107bc2b804c6625db1341ff2bb7",
)
FACE_DETECTOR = ModelSpec(
    "blaze_face_short_range.tflite",
    f"{_BASE}/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
    "Apache-2.0",
    "b4578f35940bf5a1a655214a1cce5cab13eba73c",
)
POSE_LANDMARKER = ModelSpec(
    "pose_landmarker_lite.task",
    f"{_BASE}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "Apache-2.0",
    "59929e1d1ee95287735ddd833b19cf4ac46d29bc",
)
HAND_LANDMARKER = ModelSpec(
    "hand_landmarker.task",
    f"{_BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    "Apache-2.0",
    "fbc2a30080c3c557093b5ddfc334698132eb3410",
)

# MediaPipe face-mesh landmark indices
_NOSE, _FOREHEAD, _CHIN = 1, 10, 152
_CHEEK_L, _CHEEK_R = 234, 454
_EYE_L = (33, 133, 159, 145, 468)  # outer, inner, upper lid, lower lid, iris centre
_EYE_R = (263, 362, 386, 374, 473)
_MAX_FACES = 4
_MAX_HANDS = 4
_MIN_LANDMARK_VISIBILITY = 0.3
_MIN_CROP = 8


def _frames_with_people(
    usable: Sequence[int], entities: DetectionSignals | None, every: int
) -> list[int]:
    """The planned frames to read; frames the detector looked at and found nobody in are skipped."""
    chosen = list(usable)[::every]
    if entities is None:
        return chosen
    looked = set(entities.frames)
    people = {r.frame for r in entities.rows if r.kind is EntityKind.PERSON}
    return [f for f in chosen if f not in looked or f in people]


def _mp_image(picture: Any) -> Any:
    import mediapipe as mp

    return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(picture))


def _clip(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _crop_sharpness(picture: Any, box: BBox) -> float:
    """Gradient p99 over tonal spread of the face crop (same measure as picture sharpness)."""
    height, width = picture.shape[:2]
    crop = picture[
        int(box.y0 * height) : int(box.y1 * height) + 1,
        int(box.x0 * width) : int(box.x1 * width) + 1,
    ]
    if crop.shape[0] < _MIN_CROP or crop.shape[1] < _MIN_CROP:
        return 0.0
    luma = (crop.astype(np.float32) / 255.0) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    gx = (luma[1:-1, 2:] - luma[1:-1, :-2]) / 2.0
    gy = (luma[2:, 1:-1] - luma[:-2, 1:-1]) / 2.0
    spread = float(np.percentile(luma, 99) - np.percentile(luma, 1))
    return float(np.percentile(np.hypot(gx, gy), 99)) / max(spread, 0.1)


def face_cues(landmarks: Sequence[Any], blend: dict[str, float]) -> dict[str, float]:
    """Head pose, gaze, eyes, smile and mouth from 478 landmarks and the blendshape scores."""
    point = lambda i: (landmarks[i].x, landmarks[i].y)  # noqa: E731
    left, right = point(_CHEEK_L), point(_CHEEK_R)
    nose, forehead, chin = point(_NOSE), point(_FOREHEAD), point(_CHIN)
    half_width = max((right[0] - left[0]) / 2.0, 1e-6)
    half_height = max((chin[1] - forehead[1]) / 2.0, 1e-6)
    yaw = math.degrees(
        math.asin(max(-1.0, min(1.0, (nose[0] - (left[0] + right[0]) / 2) / half_width)))
    )
    pitch = -math.degrees(
        math.asin(max(-1.0, min(1.0, (nose[1] - (forehead[1] + chin[1]) / 2) / half_height)))
    )
    eye_l, eye_r = point(_EYE_L[0]), point(_EYE_R[0])
    roll = math.degrees(math.atan2(eye_r[1] - eye_l[1], eye_r[0] - eye_l[0]))

    def gaze(eye: tuple[int, int, int, int, int]) -> tuple[float, float]:
        outer, inner, upper, lower, iris = (point(i) for i in eye)
        centre = ((outer[0] + inner[0]) / 2, (upper[1] + lower[1]) / 2)
        wide = max(abs(outer[0] - inner[0]) / 2.0, 1e-6)
        high = max(abs(lower[1] - upper[1]) / 2.0, 1e-6)
        return (iris[0] - centre[0]) / wide, -(iris[1] - centre[1]) / high

    (gx_l, gy_l), (gx_r, gy_r) = gaze(_EYE_L), gaze(_EYE_R)
    return {
        "yaw": yaw,
        "pitch": pitch,
        "roll": roll,
        "gaze_x": max(-1.0, min(1.0, (gx_l + gx_r) / 2)),
        "gaze_y": max(-1.0, min(1.0, (gy_l + gy_r) / 2)),
        "eye_open_left": _clip(1.0 - blend.get("eyeBlinkLeft", 0.0)),
        "eye_open_right": _clip(1.0 - blend.get("eyeBlinkRight", 0.0)),
        "smile": _clip((blend.get("mouthSmileLeft", 0.0) + blend.get("mouthSmileRight", 0.0)) / 2),
        "mouth_open": _clip(blend.get("jawOpen", 0.0)),
    }


class MediaPipeFaceAnalyzer:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``FACES``."""

    analyzer = AnalyzerId.FACES

    def __init__(self, models: ModelStore) -> None:
        self._models = models
        self._tasks: OnceLoaded[tuple[Any, Any]] = OnceLoaded()
        self._lock = threading.Lock()

    def identity(self) -> dict[str, str]:
        return {
            "mediapipe": package_version("mediapipe"),
            "landmarker": FACE_LANDMARKER.identity,
            "detector": FACE_DETECTOR.identity,
            "faces_revision": REVISION,
        }

    def unavailable(self, allow_downloads: bool) -> str | None:
        return library_missing("mediapipe") or next(
            (
                reason
                for spec in (FACE_LANDMARKER, FACE_DETECTOR)
                if (reason := self._models.missing_reason(spec, allow_downloads))
            ),
            None,
        )

    def device(self, requested: DeviceKind) -> str:
        return "cpu"

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> FaceSignals:
        frames = own(video)
        entities = request.dependencies.get(AnalyzerId.ENTITIES)
        chosen = _frames_with_people(
            usable_rgb_frames(frames, request.rgb_plan),
            entities if isinstance(entities, DetectionSignals) else None,
            request.settings.face_every,
        )
        minimum = request.settings.face_min_score

        def work() -> FaceSignals:
            landmarker, detector = self._load(request.allow_downloads)
            rows: list[FaceRow] = []
            for frame in chosen:
                cancellation.raise_if_cancelled()
                picture = rgb_frame(frames, frame)
                image = _mp_image(picture)
                with self._lock:
                    found = landmarker.detect(image)
                    detections = detector.detect(image).detections
                boxes = _detector_boxes(detections, picture.shape[1], picture.shape[0])
                for index, marks in enumerate(found.face_landmarks):
                    row = _face_row(frame, picture, marks, found, index, boxes, minimum)
                    if row is not None:
                        rows.append(row)
            return FaceSignals(
                model=f"{FACE_LANDMARKER.identity}+{FACE_DETECTOR.identity}",
                frames=tuple(chosen),
                rows=tuple(rows),
            )

        return guarded("Reading faces", work)

    def _load(self, allow_downloads: bool) -> tuple[Any, Any]:
        def load() -> tuple[Any, Any]:
            from mediapipe.tasks.python import BaseOptions, vision

            mode = vision.RunningMode.IMAGE
            landmarker = vision.FaceLandmarker.create_from_options(
                vision.FaceLandmarkerOptions(
                    base_options=BaseOptions(
                        model_asset_path=str(self._models.ensure(FACE_LANDMARKER, allow_downloads))
                    ),
                    running_mode=mode,
                    num_faces=_MAX_FACES,
                    output_face_blendshapes=True,
                )
            )
            detector = vision.FaceDetector.create_from_options(
                vision.FaceDetectorOptions(
                    base_options=BaseOptions(
                        model_asset_path=str(self._models.ensure(FACE_DETECTOR, allow_downloads))
                    ),
                    running_mode=mode,
                )
            )
            return landmarker, detector

        return self._tasks.get("faces", load)


def _detector_boxes(detections: Sequence[Any], width: int, height: int) -> list[tuple[BBox, float]]:
    boxes: list[tuple[BBox, float]] = []
    for item in detections:
        b = item.bounding_box
        box = BBox.clamped(
            b.origin_x / width,
            b.origin_y / height,
            (b.origin_x + b.width) / width,
            (b.origin_y + b.height) / height,
        )
        if box is not None:
            boxes.append((box, float(item.categories[0].score)))
    return boxes


def _face_row(
    frame: int,
    picture: Any,
    marks: Sequence[Any],
    found: Any,
    index: int,
    detected: list[tuple[BBox, float]],
    minimum: float,
) -> FaceRow | None:
    box = BBox.clamped(
        min(m.x for m in marks),
        min(m.y for m in marks),
        max(m.x for m in marks),
        max(m.y for m in marks),
    )
    if box is None:
        return None
    best = max(((iou(box, d), score) for d, score in detected), default=(0.0, 0.0))
    confidence = best[1] if best[0] >= 0.3 else 0.5
    if confidence < minimum:
        return None
    blend = (
        {c.category_name: float(c.score) for c in found.face_blendshapes[index]}
        if found.face_blendshapes and index < len(found.face_blendshapes)
        else {}
    )
    cues = face_cues(marks, blend)
    return FaceRow(
        frame=frame,
        box=box,
        confidence=round(confidence, 4),
        sharpness=round(_crop_sharpness(picture, box), 5),
        **{k: round(v, 4) for k, v in cues.items()},
    )


class MediaPipeBodyAnalyzer:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``BODY``."""

    analyzer = AnalyzerId.BODY

    def __init__(self, models: ModelStore) -> None:
        self._models = models
        self._tasks: OnceLoaded[tuple[Any, Any]] = OnceLoaded()
        self._lock = threading.Lock()

    def identity(self) -> dict[str, str]:
        return {
            "mediapipe": package_version("mediapipe"),
            "pose": POSE_LANDMARKER.identity,
            "hands": HAND_LANDMARKER.identity,
            "body_revision": REVISION,
        }

    def unavailable(self, allow_downloads: bool) -> str | None:
        return library_missing("mediapipe") or next(
            (
                reason
                for spec in (POSE_LANDMARKER, HAND_LANDMARKER)
                if (reason := self._models.missing_reason(spec, allow_downloads))
            ),
            None,
        )

    def device(self, requested: DeviceKind) -> str:
        return "cpu"

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> BodySignals:
        frames = own(video)
        entities = request.dependencies.get(AnalyzerId.ENTITIES)
        chosen = _frames_with_people(
            usable_rgb_frames(frames, request.rgb_plan),
            entities if isinstance(entities, DetectionSignals) else None,
            request.settings.body_every,
        )

        def work() -> BodySignals:
            pose, hands = self._load(request.allow_downloads)
            poses: list[PoseRow] = []
            hand_rows: list[HandRow] = []
            for frame in chosen:
                cancellation.raise_if_cancelled()
                image = _mp_image(rgb_frame(frames, frame))
                with self._lock:
                    body = pose.detect(image)
                    found = hands.detect(image)
                poses.extend(r for r in (_pose_row(frame, m) for m in body.pose_landmarks) if r)
                for marks, side in zip(found.hand_landmarks, found.handedness, strict=False):
                    row = _hand_row(frame, marks, side[0].category_name.lower())
                    if row is not None:
                        hand_rows.append(row)
            return BodySignals(
                model=f"{POSE_LANDMARKER.identity}+{HAND_LANDMARKER.identity}",
                frames=tuple(chosen),
                poses=tuple(poses),
                hands=tuple(hand_rows),
            )

        return guarded("Reading body pose", work)

    def _load(self, allow_downloads: bool) -> tuple[Any, Any]:
        def load() -> tuple[Any, Any]:
            from mediapipe.tasks.python import BaseOptions, vision

            mode = vision.RunningMode.IMAGE
            pose = vision.PoseLandmarker.create_from_options(
                vision.PoseLandmarkerOptions(
                    base_options=BaseOptions(
                        model_asset_path=str(self._models.ensure(POSE_LANDMARKER, allow_downloads))
                    ),
                    running_mode=mode,
                )
            )
            hands = vision.HandLandmarker.create_from_options(
                vision.HandLandmarkerOptions(
                    base_options=BaseOptions(
                        model_asset_path=str(self._models.ensure(HAND_LANDMARKER, allow_downloads))
                    ),
                    running_mode=mode,
                    num_hands=_MAX_HANDS,
                )
            )
            return pose, hands

        return self._tasks.get("body", load)


def _flat(marks: Sequence[Any]) -> tuple[float, ...]:
    return tuple(round(_clip(v), 4) for m in marks for v in (m.x, m.y))


def _pose_row(frame: int, marks: Sequence[Any]) -> PoseRow | None:
    seen = [m for m in marks if getattr(m, "visibility", 1.0) >= _MIN_LANDMARK_VISIBILITY]
    if not seen:
        return None
    box = BBox.clamped(
        min(m.x for m in seen),
        min(m.y for m in seen),
        max(m.x for m in seen),
        max(m.y for m in seen),
    )
    if box is None:
        return None
    visibility = sum(getattr(m, "visibility", 1.0) for m in marks) / len(marks)
    return PoseRow(frame, box, round(_clip(visibility), 4), _flat(marks))


def _hand_row(frame: int, marks: Sequence[Any], side: str) -> HandRow | None:
    box = BBox.clamped(
        min(m.x for m in marks),
        min(m.y for m in marks),
        max(m.x for m in marks),
        max(m.y for m in marks),
    )
    return None if box is None else HandRow(frame, side, box, _flat(marks))
