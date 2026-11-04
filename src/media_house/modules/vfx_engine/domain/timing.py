"""Exact time maps and the per-output-frame view of a treatment.

A retimed clip KEEPS its timeline span (no ripple: captions, music and transitions planned on the
same edit stay valid). Inside the span the picture follows a piecewise map

    normal (rate 1) -> [ramp] -> slow (rate r) -> freeze (rate 0) -> slow -> [ramp] -> normal

so it shows LESS source than the clip placed; the unused tail is recorded, never invented.
Every boundary sits on the edit's frame grid (``round(t * fps)``, half-to-even, on exact values),
and every source time is derived from it exactly (``source = start + rate * (t - t0)``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise

from media_house.core.domain import Rational
from media_house.modules.vfx_engine.domain.facts import FULL_FRAME, Box, fr, rat
from media_house.modules.vfx_engine.domain.model import (
    Effect,
    Mask,
    TimeMap,
    TimeSegment,
    Treatment,
)
from media_house.modules.vfx_engine.domain.values import (
    AudioIntent,
    EffectType,
    Interpolation,
    SegmentKind,
)


def frame_of(t: Fraction, fps: Fraction) -> int:
    """The frame index of an exact time (contracts sec. 3: computed once, at the edge)."""
    return round(t * fps)


def snap(t: Fraction, fps: Fraction) -> Fraction:
    return Fraction(frame_of(t, fps)) / fps


@dataclass(frozen=True, slots=True)
class _Piece:
    kind: SegmentKind
    t0: Fraction
    t1: Fraction
    s0: Fraction
    rate: Fraction

    @property
    def s1(self) -> Fraction:
        if self.kind is SegmentKind.FREEZE:
            return self.s0
        return self.s0 + self.rate * (self.t1 - self.t0)


def retime_map(
    *,
    timeline_start: Fraction,
    timeline_end: Fraction,
    source_start: Fraction,
    peak: Fraction,
    fps: Fraction,
    rate: Fraction,
    pre_roll: Fraction,
    post_roll: Fraction,
    freeze: Fraction,
    ramp: bool,
    interpolation: Interpolation,
    audio: AudioIntent,
) -> TimeMap:
    """Slow motion (``rate`` < 1) around a source ``peak`` and/or a freeze of the peak frame.

    ``pre_roll`` / ``post_roll`` are SOURCE seconds shown slowly before / after the peak; the
    freeze holds the peak for ``freeze`` timeline seconds. Pieces past the clip end are cut.
    """
    pieces: list[_Piece] = []
    at, src = timeline_start, source_start

    def add(kind: SegmentKind, until: Fraction, r: Fraction) -> None:
        nonlocal at, src
        # the clip end itself is kept exact: snapping it could fall short of an off-grid end
        until = timeline_end if until >= timeline_end else min(snap(until, fps), timeline_end)
        if until <= at:
            return
        piece = _Piece(kind, at, until, src, r)
        pieces.append(piece)
        at, src = until, piece.s1

    slow = rate < 1
    lead = max(peak - (pre_roll if slow else Fraction(0)), source_start)
    add(SegmentKind.NORMAL, timeline_start + (lead - source_start), Fraction(1))
    steps = (Fraction(3, 4), Fraction(1, 2)) if ramp and slow else ()
    for step in steps:  # ramp in: short constant-rate steps (rate never jumps 1 -> r at once)
        if step > rate:
            add(SegmentKind.SLOW, at + Fraction(1, 5), step)
    if slow:
        add(SegmentKind.SLOW, at + max(peak - src, Fraction(0)) / rate, rate)
    if freeze > 0:
        add(SegmentKind.FREEZE, at + freeze, Fraction(0))
    if slow:
        add(SegmentKind.SLOW, at + post_roll / rate, rate)
        for step in reversed(steps):
            if step > rate:
                add(SegmentKind.SLOW, at + Fraction(1, 5), step)
    add(SegmentKind.NORMAL, timeline_end, Fraction(1))
    segments = tuple(_segment(p, fps, audio) for p in pieces)
    return TimeMap(
        segments=segments,
        interpolation=interpolation if slow else Interpolation.DUPLICATE,
        source_used_start=rat(source_start),
        source_used_end=rat(max(p.s1 for p in pieces)),
        note="the clip keeps its timeline span; the source after the used end is not shown",
    )


def reverse_map(
    *,
    timeline_start: Fraction,
    timeline_end: Fraction,
    source_start: Fraction,
    at: Fraction,
    length: Fraction,
    fps: Fraction,
    audio: AudioIntent,
) -> TimeMap:
    """A rewind replay: forward up to source ``at``, the last ``length`` seconds backwards, then
    forward again from ``at``."""
    pieces: list[_Piece] = []
    t = timeline_start
    forward_end = min(snap(timeline_start + (at - source_start), fps), timeline_end)
    if forward_end > t:
        pieces.append(_Piece(SegmentKind.NORMAL, t, forward_end, source_start, Fraction(1)))
        t = forward_end
    shown = source_start + (t - timeline_start)
    length = min(length, shown - source_start)
    back_end = min(snap(t + length, fps), timeline_end)
    if back_end > t:
        # stored with its source span in forward order; it plays from source_end down
        pieces.append(_Piece(SegmentKind.REVERSE, t, back_end, shown - (back_end - t), Fraction(1)))
        t = back_end
    if timeline_end > t:
        pieces.append(_Piece(SegmentKind.NORMAL, t, timeline_end, shown, Fraction(1)))
    return TimeMap(
        segments=tuple(_segment(p, fps, audio) for p in pieces),
        interpolation=Interpolation.DUPLICATE,
        source_used_start=rat(source_start),
        source_used_end=rat(max(p.s1 for p in pieces)),
        note="rewind replay; the clip keeps its timeline span",
    )


def _segment(p: _Piece, fps: Fraction, audio: AudioIntent) -> TimeSegment:
    retimed = p.kind is not SegmentKind.NORMAL
    return TimeSegment(
        kind=p.kind,
        timeline_start=rat(p.t0),
        timeline_end=rat(p.t1),
        source_start=rat(p.s0),
        source_end=rat(p.s1),
        rate=rat(p.rate),
        start_frame=frame_of(p.t0, fps),
        end_frame=frame_of(p.t1, fps),
        audio=audio if retimed else AudioIntent.SOURCE_SYNC,
    )


def map_problems(time_map: TimeMap, start: Fraction, end: Fraction) -> list[str]:
    """Contiguity and exactness of a time map over the treatment span ``[start, end)``."""
    found: list[str] = []
    segments = time_map.segments
    if not segments:
        return ["time map without segments"]
    if fr(segments[0].timeline_start) != start or fr(segments[-1].timeline_end) != end:
        found.append("time map does not cover the clip span exactly")
    for a, b in pairwise(segments):
        if a.timeline_end != b.timeline_start or a.end_frame != b.start_frame:
            found.append(f"time map gap at {a.timeline_end}")
    for s in segments:
        span = fr(s.timeline_end) - fr(s.timeline_start)
        if span <= 0:
            found.append("empty time segment")
        expected = fr(s.source_start) + fr(s.rate) * span
        if s.kind is SegmentKind.FREEZE:
            if s.source_end != s.source_start or fr(s.rate) != 0:
                found.append("a freeze holds one source time at rate 0")
        elif fr(s.source_end) != expected:
            found.append(f"segment at {s.timeline_start}: source end is not start + rate x span")
    return found


# --- per output frame -------------------------------------------------------------------------
def source_time_at(treatment: Treatment, t: Fraction) -> Fraction:
    """The source time shown at timeline time ``t`` (inside the treatment span)."""
    clip_offset = t - fr(treatment.timeline_start)
    if treatment.time_map is None:
        return fr(treatment.source_start) + clip_offset
    for s in treatment.time_map.segments:
        t0, t1 = fr(s.timeline_start), fr(s.timeline_end)
        if t0 <= t < t1:
            if s.kind is SegmentKind.FREEZE:
                return fr(s.source_start)
            if s.kind is SegmentKind.REVERSE:
                return fr(s.source_end) - fr(s.rate) * (t - t0)
            return fr(s.source_start) + fr(s.rate) * (t - t0)
    return fr(treatment.time_map.segments[-1].source_end)


def box_at(mask: Mask, source_time: Fraction) -> Box | None:
    """The mask's box at a source time; ``None`` = the mask does not apply then."""
    if mask.keyframes and len(mask.keyframes) == 1 and not mask.fallback_spans:
        return mask.keyframes[0].box
    for lo, hi in mask.fallback_spans:
        if fr(lo) <= source_time < fr(hi):
            return FULL_FRAME
    keys = mask.keyframes
    if not keys or source_time < fr(keys[0].source_time) or source_time > fr(keys[-1].source_time):
        return None
    for k0, k1 in pairwise(keys):
        a, b = fr(k0.source_time), fr(k1.source_time)
        if a <= source_time <= b:
            w = float((source_time - a) / (b - a)) if b > a else 0.0
            return Box(
                x0=k0.box.x0 + (k1.box.x0 - k0.box.x0) * w,
                y0=k0.box.y0 + (k1.box.y0 - k0.box.y0) * w,
                x1=k0.box.x1 + (k1.box.x1 - k0.box.x1) * w,
                y1=k0.box.y1 + (k1.box.y1 - k0.box.y1) * w,
            )
    return keys[-1].box


def punch_scale_at(effects: Sequence[Effect], t: Fraction) -> tuple[float, float, float]:
    """``(scale, anchor_x, anchor_y)`` of the punch-ins at timeline time ``t`` (1.0 = none)."""
    for e in effects:
        if e.effect_type is not EffectType.PUNCH_IN:
            continue
        t0, t1 = fr(e.timeline_start), fr(e.timeline_end)
        if not t0 <= t < t1:
            continue
        target, ease = e.param("scale", 1.0), Fraction(e.param("ease", 0.0)).limit_denominator(1000)
        progress = 1.0 if ease <= 0 else min(1.0, float((t - t0) / ease))
        eased = progress * progress * (3 - 2 * progress)  # smoothstep: no jolt at either end
        return 1.0 + (target - 1.0) * eased, e.param("anchor_x", 0.5), e.param("anchor_y", 0.5)
    return 1.0, 0.5, 0.5


@dataclass(frozen=True, slots=True)
class FrameView:
    """What one output frame shows: its source time, the punch-in window (normalised source crop)
    and each mask's box (``None`` = mask off on that frame)."""

    index: int
    timeline_time: Fraction
    source_time: Fraction
    crop: Box
    masks: tuple[Box | None, ...]


def frame_views(treatment: Treatment, fps: Fraction) -> list[FrameView]:
    effects = treatment.active()
    masks = tuple(m for e in effects for m in e.masks)
    views: list[FrameView] = []
    for n in range(treatment.frames):
        t = Fraction(treatment.start_frame + n) / fps
        t = min(max(t, fr(treatment.timeline_start)), fr(treatment.timeline_end))
        source = source_time_at(treatment, t)
        scale, ax, ay = punch_scale_at(effects, t)
        views.append(
            FrameView(
                index=n,
                timeline_time=t,
                source_time=source,
                crop=crop_window(scale, ax, ay),
                masks=tuple(box_at(m, source) for m in masks),
            )
        )
    return views


def crop_window(scale: float, anchor_x: float, anchor_y: float) -> Box:
    """The normalised source window a zoom of ``scale`` about an anchor shows (kept inside)."""
    size = 1.0 / max(scale, 1.0)
    x0 = min(max(anchor_x - size / 2, 0.0), 1.0 - size)
    y0 = min(max(anchor_y - size / 2, 0.0), 1.0 - size)
    return Box(x0=x0, y0=y0, x1=x0 + size, y1=y0 + size)


def as_rational(value: Fraction) -> Rational:
    return rat(value)
