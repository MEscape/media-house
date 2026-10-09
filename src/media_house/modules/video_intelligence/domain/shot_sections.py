"""The per-shot sections every profile produces from the classical signals.

Boundaries, handles, keyframes, camera movement, picture motion, picture quality and the curves
over time. Pure functions of the cached signals and the profile; ``derive`` assembles them with
the model-based sections.
"""

from bisect import bisect_left

from media_house.core.domain import FrameTime, TimeRange
from media_house.modules.video_intelligence.domain.camera import describe_camera, describe_motion
from media_house.modules.video_intelligence.domain.observations import (
    BoundaryObservation,
    CameraObservation,
    Evidence,
    HandlesObservation,
    Keyframes,
    MotionObservation,
    QualityAssessment,
    QualityMetrics,
)
from media_house.modules.video_intelligence.domain.profiles import ProcessingProfile
from media_house.modules.video_intelligence.domain.quality import describe_quality
from media_house.modules.video_intelligence.domain.result import Curve
from media_house.modules.video_intelligence.domain.shot_detection import Transition
from media_house.modules.video_intelligence.domain.signals import (
    MotionSignals,
    QualitySignals,
    ShotSignals,
)
from media_house.modules.video_intelligence.domain.source import ProcessingHistory, SourceInfo
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    AnalyzerState,
    BoundaryKind,
)

#: Shots shorter than this many frames cannot say how steady their ends are.
_MIN_HANDLE_FRAMES = 4
#: Shot length (frames) at which the confidence of its handle measurement is full.
_FULL_HANDLE_CONFIDENCE_FRAMES = 24
_FULL_KEYFRAME_CONFIDENCE_SAMPLES = 4


def time_at(timeline: ShotSignals, frame: int) -> FrameTime:
    return timeline.end_time() if frame >= timeline.frame_count else timeline.time_of(frame)


# --- boundaries -----------------------------------------------------------------------------------
def boundaries(
    transitions: tuple[Transition, ...], timeline: ShotSignals, black: tuple[bool, ...]
) -> list[BoundaryObservation]:
    last = timeline.frame_count - 1
    result = [
        BoundaryObservation(
            state=AnalyzerState.OK,
            confidence=1.0,
            evidence=(Evidence(frames=(timeline.time_of(0),), metric="first_frame"),),
            kind=BoundaryKind.START,
            black_adjacent=black[0],
        )
    ]
    for transition in transitions:
        result.append(
            BoundaryObservation(
                state=AnalyzerState.OK,
                confidence=transition.confidence,
                evidence=(
                    Evidence(
                        frames=(
                            timeline.time_of(transition.frame - 1),
                            timeline.time_of(transition.frame),
                        ),
                        metric="frame_difference",
                    ),
                ),
                kind=transition.kind,
                transition=(
                    TimeRange(
                        timeline.time_of(transition.first), time_at(timeline, transition.last + 1)
                    )
                    if transition.gradual
                    else None
                ),
                black_adjacent=transition.black_adjacent,
            )
        )
    result.append(
        BoundaryObservation(
            state=AnalyzerState.OK,
            confidence=1.0,
            evidence=(Evidence(frames=(timeline.time_of(last),), metric="last_frame"),),
            kind=BoundaryKind.END,
            black_adjacent=black[last],
        )
    )
    return result


# --- handles --------------------------------------------------------------------------------------
def handles(
    timeline: ShotSignals,
    profile: ProcessingProfile,
    first: int,
    end: int,
    transitions: tuple[Transition, ...],
) -> HandlesObservation:
    length = end - first
    if length < _MIN_HANDLE_FRAMES:
        return HandlesObservation(
            state=AnalyzerState.UNKNOWN, reasons=("shot_too_short_for_handles",)
        )
    limit = profile.shots.stable_diff
    reach = length // 2
    head = 0
    while head < reach and timeline.diff1[first + 1 + head] <= limit:
        head += 1
    tail = 0
    while tail < reach and timeline.diff1[end - 1 - tail] <= limit:
        tail += 1
    # a gradual transition at an end of the shot is not a stable margin
    reasons: list[str] = []
    for transition in transitions:
        if not transition.gradual:
            continue
        if transition.frame == first:
            head = 0
            reasons.append("starts_inside_transition")
        elif transition.frame == end:
            tail = 0
            reasons.append("ends_inside_transition")
    seconds_head = span_seconds(timeline, first, head)
    seconds_tail = span_seconds(timeline, end - tail, tail)
    return HandlesObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, length / _FULL_HANDLE_CONFIDENCE_FRAMES),
        evidence=(
            Evidence(
                frames=(timeline.time_of(first), timeline.time_of(end - 1)),
                metric="frame_difference",
            ),
        ),
        reasons=tuple(reasons),
        head_frames=head,
        tail_frames=tail,
        head_seconds=seconds_head,
        tail_seconds=seconds_tail,
    )


def span_seconds(timeline: ShotSignals, first: int, frames: int) -> float:
    if frames <= 0:
        return 0.0
    return (
        time_at(timeline, first + frames).pts - timeline.time_of(first).pts
    ) * timeline.timebase.value


# --- keyframes ------------------------------------------------------------------------------------
def keyframes(
    timeline: ShotSignals, quality: QualitySignals | None, first: int, end: int
) -> Keyframes:
    if quality is None:
        return Keyframes(state=AnalyzerState.NOT_ANALYZED, reasons=("quality_analyzer_not_run",))
    rows = range(bisect_left(quality.frames, first), bisect_left(quality.frames, end))
    if not rows:
        return Keyframes(state=AnalyzerState.NOT_ANALYZED, reasons=("no_sampled_frame_in_shot",))
    values = sorted(quality.sharpness[i] for i in rows)
    middle_value = values[len(values) // 2]
    centre = (first + end - 1) / 2
    steady = [i for i in rows if quality.sharpness[i] >= middle_value]
    representative = min(steady, key=lambda i: (abs(quality.frames[i] - centre), i))
    sharpest = max(rows, key=lambda i: (quality.sharpness[i], -i))
    chosen = tuple(
        timeline.time_of(quality.frames[i]) for i in dict.fromkeys((representative, sharpest))
    )
    return Keyframes(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(rows) / _FULL_KEYFRAME_CONFIDENCE_SAMPLES),
        evidence=(Evidence(frames=chosen, metric="sharpness"),),
        representative=timeline.time_of(quality.frames[representative]),
        sharpest=timeline.time_of(quality.frames[sharpest]),
    )


# --- sections that need an analyzer that may not have run ----------------------------------------
def camera_section(
    motion: MotionSignals | None,
    timeline: ShotSignals,
    first: int,
    end: int,
    aspect: float,
    profile: ProcessingProfile,
    history: ProcessingHistory,
) -> CameraObservation:
    if motion is None:
        return CameraObservation(
            state=AnalyzerState.NOT_ANALYZED,
            reasons=("motion_analyzer_not_run",),
            measured_on=history.measured_on,
            stabilized=history.stabilized,
        )
    return describe_camera(motion, timeline, first, end, aspect, profile.motion, history)


def motion_section(
    motion: MotionSignals | None,
    timeline: ShotSignals,
    first: int,
    end: int,
    aspect: float,
    profile: ProcessingProfile,
) -> MotionObservation:
    if motion is None:
        return MotionObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("motion_analyzer_not_run",)
        )
    return describe_motion(motion, timeline, first, end, aspect, profile.motion)


def quality_section(
    quality: QualitySignals | None,
    timeline: ShotSignals,
    first: int,
    end: int,
    profile: ProcessingProfile,
    source: SourceInfo,
    history: ProcessingHistory,
) -> QualityAssessment:
    if quality is None:
        return QualityAssessment(
            state=AnalyzerState.NOT_ANALYZED,
            reasons=("quality_analyzer_not_run",),
            metrics=QualityMetrics(),
            measured_on=history.measured_on,
        )
    return describe_quality(quality, timeline, first, end, profile.quality, source, history)


# --- curves ---------------------------------------------------------------------------------------
def curves(
    timeline: ShotSignals,
    motion: MotionSignals | None,
    quality: QualitySignals | None,
    history: ProcessingHistory,
) -> tuple[Curve, ...]:
    curves: list[Curve] = []
    if motion is not None:
        curves.append(
            Curve(
                name="motion_energy",
                unit="luma_fraction",
                source=AnalyzerId.MOTION,
                measured_on=history.measured_on,
                timebase=timeline.timebase,
                frames=motion.frames,
                pts=tuple(timeline.pts[f] for f in motion.frames),
                values=motion.residual,
            )
        )
    if quality is not None:
        pts = tuple(timeline.pts[f] for f in quality.frames)
        for name, unit, values in (
            ("sharpness", "gradient_over_tonal_spread", quality.sharpness),
            ("luma_median", "luma_fraction", quality.luma_p50),
        ):
            curves.append(
                Curve(
                    name=name,
                    unit=unit,
                    source=AnalyzerId.QUALITY,
                    measured_on=history.measured_on,
                    timebase=timeline.timebase,
                    frames=quality.frames,
                    pts=pts,
                    values=values,
                )
            )
    return tuple(curves)
