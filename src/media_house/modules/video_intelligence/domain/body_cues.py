"""Body presence and simple hand gestures from pose and hand landmarks (pure geometry).

Gestures are descriptions of a visible hand shape (an open hand, a fist, a pointing finger, a hand
above the shoulder). They carry no meaning: what a gesture signifies is not decided here.
"""

import math
from collections.abc import Sequence

from media_house.modules.video_intelligence.domain.observations import (
    BodyObservation,
    Evidence,
    Gesture,
)
from media_house.modules.video_intelligence.domain.signals import BodySignals, ShotSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerState, GestureKind

#: MediaPipe pose joints used: shoulders and wrists.
_L_SHOULDER, _R_SHOULDER, _L_WRIST, _R_WRIST = 11, 12, 15, 16
#: Hand landmark (tip, pip) pairs of the four fingers; the thumb is ignored for the shapes.
_FINGERS = ((8, 6), (12, 10), (16, 14), (20, 18))
_EXTENDED_RATIO = 1.15
#: A hand counts as raised when the wrist is this far above the shoulder (picture fraction).
_RAISE_MARGIN = 0.03
#: A gesture needs this many analysed frames in a row, and may skip this many in between.
_MIN_RUN = 2
_FULL_CONFIDENCE_FRAMES = 4


def _distance(points: Sequence[float], a: int, b: int) -> float:
    return math.hypot(points[2 * a] - points[2 * b], points[2 * a + 1] - points[2 * b + 1])


def hand_shape(landmarks: Sequence[float]) -> GestureKind | None:
    """The shape of one hand from its 21 landmarks, or ``None`` when it is none of the shapes."""
    extended = [
        _distance(landmarks, tip, 0) > _EXTENDED_RATIO * _distance(landmarks, pip, 0)
        for tip, pip in _FINGERS
    ]
    count = sum(extended)
    if extended[0] and not any(extended[1:]):
        return GestureKind.POINTING
    if count >= 4:
        return GestureKind.OPEN_HAND
    if count == 0:
        return GestureKind.FIST
    return None


def _runs(frames: list[int], analysed: Sequence[int]) -> list[tuple[int, int]]:
    """Maximal runs of ``frames`` that are consecutive among the analysed frames."""
    position = {f: i for i, f in enumerate(analysed)}
    runs: list[tuple[int, int]] = []
    start = previous = None
    for frame in sorted(frames):
        if previous is not None and position[frame] - position[previous] == 1:
            previous = frame
            continue
        if start is not None and previous is not None:
            runs.append((start, previous))
        start = previous = frame
    if start is not None and previous is not None:
        runs.append((start, previous))
    return runs


def describe_body(
    signals: BodySignals, timeline: ShotSignals, first: int, end: int
) -> BodyObservation:
    analysed = [f for f in signals.frames if first <= f < end]
    if not analysed:
        return BodyObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_analysed_frame_in_shot",)
        )
    poses = [p for p in signals.poses if first <= p.frame < end]
    hands = [h for h in signals.hands if first <= h.frame < end]

    found: dict[GestureKind, list[int]] = {}
    for hand in hands:
        shape = hand_shape(hand.landmarks)
        if shape is not None:
            found.setdefault(shape, []).append(hand.frame)
    for pose in poses:
        shoulder = min(pose.joints[2 * _L_SHOULDER + 1], pose.joints[2 * _R_SHOULDER + 1])
        wrist = min(pose.joints[2 * _L_WRIST + 1], pose.joints[2 * _R_WRIST + 1])
        if wrist < shoulder - _RAISE_MARGIN:
            found.setdefault(GestureKind.HAND_RAISED, []).append(pose.frame)

    gestures: list[Gesture] = []
    for kind in GestureKind:
        for start, stop in _runs(sorted(set(found.get(kind, []))), analysed):
            length = analysed.index(stop) - analysed.index(start) + 1
            if length >= _MIN_RUN:
                gestures.append(
                    Gesture(
                        kind=kind,
                        first=timeline.time_of(start),
                        last=timeline.time_of(stop),
                        confidence=min(1.0, length / _FULL_CONFIDENCE_FRAMES),
                    )
                )
    gestures.sort(key=lambda g: (g.first.frame, g.kind.value))
    with_pose = {p.frame for p in poses}
    marks = tuple(timeline.time_of(f) for f in analysed[:3])
    return BodyObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(analysed) / _FULL_CONFIDENCE_FRAMES),
        evidence=(Evidence(frames=marks, metric="pose_and_hand_landmarks"),),
        reasons=() if poses or hands else ("no_body_found",),
        pose_fraction=len(with_pose) / len(analysed),
        gestures=tuple(gestures),
    )
