"""The unified event timeline: moments where the picture changes in a way that is measurable.

Boundaries, flashes, motion and attention peaks, entities entering and leaving, text appearing
and disappearing, the screen content of a tutorial changing, scrolling and gestures, all on one
timeline of ``FrameTime``. Events are observations of change; which of them matters is decided
by the editing stage, not here.
"""

from bisect import bisect_left
from collections.abc import Sequence
from itertools import pairwise
from statistics import median

from media_house.modules.video_intelligence.domain.observations import Event, Evidence, Track
from media_house.modules.video_intelligence.domain.profiles import MeaningSettings, TextSettings
from media_house.modules.video_intelligence.domain.result import Shot
from media_house.modules.video_intelligence.domain.signals import (
    MotionSignals,
    SaliencySignals,
    ShotSignals,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.text_cues import TrackedText, normalise
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerState,
    CameraMovement,
    EventKind,
)

_PEAKS_PER_SHOT = 3
#: Motion peaks must exceed this residual energy outright, whatever the shot's median.
_MOTION_FLOOR = 0.02


class _Builder:
    def __init__(self, timeline: ShotSignals) -> None:
        self.timeline = timeline
        self.events: list[Event] = []
        self._count: dict[tuple[str, int], int] = {}

    def add(
        self,
        kind: EventKind,
        shot: Shot,
        frame: int,
        *,
        end: int | None = None,
        strength: float | None = None,
        detail: str = "",
        confidence: float = 0.8,
    ) -> None:
        key = (kind.value, frame)
        ordinal = self._count[key] = self._count.get(key, 0) + 1
        time = self.timeline.time_of(frame)
        self.events.append(
            Event(
                state=AnalyzerState.OK,
                confidence=min(1.0, max(0.0, confidence)),
                evidence=(Evidence(frames=(time,), metric=kind.value),),
                event_id=f"evt_{kind.value}_{frame:07d}_{ordinal}",
                kind=kind,
                shot_id=shot.shot_id,
                time=time,
                end=None
                if end is None
                else self.timeline.time_of(min(end, self.timeline.frame_count - 1)),
                strength=strength,
                detail=detail,
            )
        )


def _shot_of(shots: Sequence[Shot], frame: int) -> Shot | None:
    return next((s for s in shots if s.range.contains_frame(frame)), None)


def _bounds(frames: Sequence[int], first: int, end: int) -> tuple[int, int] | None:
    """First and last analysed frame inside the shot, if any."""
    low = bisect_left(frames, first)
    high = bisect_left(frames, end)
    return (frames[low], frames[high - 1]) if high > low else None


def build_events(
    *,
    timeline: ShotSignals,
    shots: Sequence[Shot],
    tracks: Sequence[Track],
    entity_frames: Sequence[int],
    flashes: Sequence[int],
    motion: MotionSignals | None,
    saliency: SaliencySignals | None,
    text_items: Sequence[TrackedText],
    text_signals: TextSignals | None,
    meaning: MeaningSettings,
    text: TextSettings,
) -> tuple[Event, ...]:
    out = _Builder(timeline)
    for shot in shots[1:]:
        out.add(
            EventKind.SHOT_BOUNDARY,
            shot,
            shot.range.start.frame,
            strength=shot.boundary_in.confidence,
            detail=shot.boundary_in.kind.value if shot.boundary_in.kind else "",
            confidence=shot.boundary_in.confidence or 0.5,
        )
    for frame in flashes:
        owner = _shot_of(shots, frame)
        if owner is not None:
            out.add(EventKind.FLASH, owner, frame, confidence=0.7)

    for shot in shots:
        first, end = shot.range.start.frame, shot.range.end.frame
        if motion is not None:
            rows = [
                i
                for i, f in enumerate(motion.frames)
                if first <= f < end and motion.prev_frames[i] >= first
            ]
            if rows:
                base = median(motion.residual[i] for i in rows)
                peaks = sorted(
                    (
                        i
                        for i in rows
                        if motion.residual[i]
                        >= max(_MOTION_FLOOR, meaning.motion_peak_ratio * base)
                    ),
                    key=lambda i: (-motion.residual[i], i),
                )[:_PEAKS_PER_SHOT]
                for i in sorted(peaks):
                    out.add(
                        EventKind.MOTION_PEAK,
                        shot,
                        motion.frames[i],
                        strength=motion.residual[i],
                        confidence=motion.confidence[i],
                    )
        if saliency is not None:
            rows = [i for i, f in enumerate(saliency.frames) if first <= f < end]
            if len(rows) >= 3:
                base = median(saliency.peak[i] for i in rows)
                peaks = sorted(
                    (i for i in rows if saliency.peak[i] - base >= meaning.attention_peak_rise),
                    key=lambda i: (-saliency.peak[i], i),
                )[:_PEAKS_PER_SHOT]
                for i in sorted(peaks):
                    out.add(
                        EventKind.ATTENTION_PEAK,
                        shot,
                        saliency.frames[i],
                        strength=saliency.peak[i],
                    )
        for gesture in shot.body.gestures:
            out.add(
                EventKind.GESTURE,
                shot,
                gesture.first.frame,
                end=gesture.last.frame,
                detail=gesture.kind.value,
                confidence=gesture.confidence,
            )
        if (
            shot.content.content_profile == "tutorial_screen"
            and shot.camera.movement is CameraMovement.TILT
        ):
            out.add(
                EventKind.SCROLLING,
                shot,
                first,
                end=end - 1,
                confidence=shot.camera.confidence or 0.5,
            )

    for track in tracks:
        owner = next((s for s in shots if s.shot_id == track.shot_id), None)
        if owner is None:
            continue
        shot = owner
        span = _bounds(entity_frames, shot.range.start.frame, shot.range.end.frame)
        if span is None:
            continue
        if track.first.frame > span[0]:
            out.add(
                EventKind.TRACK_ENTERED,
                shot,
                track.first.frame,
                detail=track.track_id,
                confidence=track.confidence or 0.5,
            )
        if track.last.frame < span[1]:
            out.add(
                EventKind.TRACK_EXITED,
                shot,
                track.last.frame,
                detail=track.track_id,
                confidence=track.confidence or 0.5,
            )

    if text_signals is not None:
        for item in text_items:
            found = _shot_of(shots, item.first)
            if found is None:
                continue
            shot = found
            span = _bounds(text_signals.frames, shot.range.start.frame, shot.range.end.frame)
            if span is None:
                continue
            if item.first > span[0]:
                out.add(
                    EventKind.TEXT_APPEARED,
                    shot,
                    item.first,
                    detail=item.text[:60],
                    confidence=item.confidence,
                )
            if item.last < span[1]:
                out.add(
                    EventKind.TEXT_DISAPPEARED,
                    shot,
                    item.last,
                    detail=item.text[:60],
                    confidence=item.confidence,
                )
        _screen_changes(out, shots, text_signals, timeline, text)

    return tuple(sorted(out.events, key=lambda e: (e.time.frame, e.kind.value, e.event_id)))


def _screen_changes(
    out: _Builder,
    shots: Sequence[Shot],
    text_signals: TextSignals,
    timeline: ShotSignals,
    settings: TextSettings,
) -> None:
    """The set of texts on screen changes together with a clear change of the whole picture."""
    keys: dict[int, set[str]] = {f: set() for f in text_signals.frames}
    for row in text_signals.rows:
        if row.confidence >= settings.min_confidence:
            keys[row.frame].add(normalise(row.text))
    frames = list(text_signals.frames)
    for a, b in pairwise(frames):
        shot = _shot_of(shots, b)
        if shot is None or not shot.range.contains_frame(a) or (not keys[a] and not keys[b]):
            continue
        if keys[a] != keys[b] and max(timeline.diff1[a + 1 : b + 1]) >= settings.screen_change_diff:
            out.add(EventKind.SCREEN_CONTENT_CHANGE, shot, b, confidence=0.6)
