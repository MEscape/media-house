"""Visible face cues per shot, and the mouth-activity curve behind the visual speaking cue.

Everything here is a cue of what is VISIBLE (head turned, eyes open, mouth moving). It is not
emotion, not intent and not identity. Speaking is only "the mouth is moving": who is speaking, and
whether the sound matches, is decided downstream where audio is known.
"""

import math
from collections.abc import Callable, Sequence
from statistics import median

from media_house.modules.video_intelligence.domain.observations import (
    Evidence,
    FaceObservation,
)
from media_house.modules.video_intelligence.domain.profiles import FaceSettings
from media_house.modules.video_intelligence.domain.signals import FaceRow, FaceSignals, ShotSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerState

#: Faces needed in a shot before its cues are stated with full confidence.
_FULL_CONFIDENCE_FACES = 4
_MIN_WINDOW_ROWS = 3


def main_faces(rows: Sequence[FaceRow], settings: FaceSettings) -> dict[int, FaceRow]:
    """The largest usable face of every frame that has one."""
    chosen: dict[int, FaceRow] = {}
    for row in rows:
        if row.box.height < settings.min_face_height:
            continue
        current = chosen.get(row.frame)
        if current is None or row.box.area > current.box.area:
            chosen[row.frame] = row
    return chosen


def mouth_activity(
    faces: dict[int, FaceRow], timeline: ShotSignals, settings: FaceSettings
) -> dict[int, float]:
    """Per frame, how much the jaw opening varies around that moment (0-1 speaking cue)."""
    frames = sorted(faces)
    window = settings.speaking_window_seconds
    cue: dict[int, float] = {}
    for frame in frames:
        centre = timeline.pts[frame] * timeline.timebase.value
        values = [
            faces[f].mouth_open
            for f in frames
            if abs(timeline.pts[f] * timeline.timebase.value - centre) <= window / 2
        ]
        if len(values) < _MIN_WINDOW_ROWS:
            continue
        mean = sum(values) / len(values)
        spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        cue[frame] = min(1.0, spread / (2 * settings.speaking_activity))
    return cue


def describe_faces(
    signals: FaceSignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    settings: FaceSettings,
    speaking: dict[int, float],
) -> FaceObservation:
    analysed = [f for f in signals.frames if first <= f < end]
    if not analysed:
        return FaceObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_analysed_frame_in_shot",)
        )
    rows = [r for r in signals.rows if first <= r.frame < end]
    faces = main_faces(rows, settings)
    if not faces:
        return FaceObservation(
            state=AnalyzerState.OK,
            confidence=min(1.0, len(analysed) / _FULL_CONFIDENCE_FACES),
            evidence=(
                Evidence(
                    frames=tuple(timeline.time_of(f) for f in analysed[:3]), metric="face_detection"
                ),
            ),
            reasons=("no_usable_face_found",),
        )

    usable = [faces[f] for f in sorted(faces)]
    count = len(usable)

    def share(test: Callable[[FaceRow], bool]) -> float:
        return sum(1 for r in usable if test(r)) / count

    eye_contact = share(
        lambda r: (
            abs(r.yaw) <= settings.eye_contact_yaw
            and abs(r.pitch) <= settings.eye_contact_pitch
            and abs(r.gaze_x) <= settings.gaze_offset
            and abs(r.gaze_y) <= settings.gaze_offset
        )
    )
    speaking_values = [speaking[f] for f in faces if f in speaking]
    marks = [timeline.time_of(usable[i].frame) for i in (0, count // 2, count - 1)]
    reasons: list[str] = []
    median_sharpness = float(median(r.sharpness for r in usable))
    if median_sharpness < settings.face_soft_sharpness:
        reasons.append("soft_face")
    return FaceObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, count / _FULL_CONFIDENCE_FACES)
        * float(median(r.confidence for r in usable)),
        evidence=(Evidence(frames=tuple(dict.fromkeys(marks)), metric="face_landmarks"),),
        reasons=tuple(reasons),
        face_frames=count,
        mean_face_height=sum(r.box.height for r in usable) / count,
        head_yaw=float(median(r.yaw for r in usable)),
        head_pitch=float(median(r.pitch for r in usable)),
        eye_contact=eye_contact,
        eyes_closed=share(lambda r: max(r.eye_open_left, r.eye_open_right) < settings.eyes_closed),
        smile_cue=share(lambda r: r.smile >= settings.smile),
        possibly_occluded=share(lambda r: r.confidence < settings.occlusion_confidence),
        face_sharpness=median_sharpness,
        visual_speaking=(sum(speaking_values) / len(speaking_values)) if speaking_values else None,
    )
