"""Which moments of the preview to look at.

A person watching a render looks at the opening, the closing, both sides of every cut and the
middle of each shot. Looking at every frame is not possible, so the plan is deterministic (the same
timeline gives the same moments) and bounded (``max_frames``); the most informative moments are kept
first when it has to be thinned.
"""

from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise

from media_house.modules.quality_control.domain.facts import ClipFacts, TimelineFacts
from media_house.modules.quality_control.domain.profile import QualityProfile

#: How far from a cut the two pictures are taken (seconds), kept inside their clips.
_CUT_OFFSET = Fraction(15, 100)
_EDGE = Fraction(1, 10)


@dataclass(frozen=True, slots=True)
class SamplePoint:
    at: Fraction
    #: ``edge:start``, ``edge:end``, ``cut:<frame>:before|after`` or ``clip:<id>``.
    tag: str
    clip_id: str | None


def _inside(clip: ClipFacts, at: Fraction, fps: Fraction) -> Fraction:
    low = Fraction(clip.start_frame) / fps + Fraction(1, 50)
    high = Fraction(clip.end_frame) / fps - Fraction(1, 50)
    return min(max(at, low), max(low, high))


def plan_samples(timeline: TimelineFacts, profile: QualityProfile) -> tuple[SamplePoint, ...]:
    fps = timeline.settings.fps
    end = timeline.seconds(timeline.settings.duration_frames)
    primary = timeline.primary()
    first: list[SamplePoint] = [
        SamplePoint(_EDGE, "edge:start", primary[0].clip_id if primary else None),
        SamplePoint(
            max(Fraction(0), end - 2 * _EDGE), "edge:end", primary[-1].clip_id if primary else None
        ),
    ]
    cuts: list[SamplePoint] = []
    for a, b in pairwise(primary):
        if b.start_frame - a.end_frame > 1:
            continue
        cut = Fraction(a.end_frame) / fps
        cuts.append(
            SamplePoint(_inside(a, cut - _CUT_OFFSET, fps), f"cut:{a.end_frame}:before", a.clip_id)
        )
        cuts.append(
            SamplePoint(_inside(b, cut + _CUT_OFFSET, fps), f"cut:{a.end_frame}:after", b.clip_id)
        )
    mids = [
        SamplePoint(Fraction(c.start_frame + c.end_frame, 2) / fps, f"clip:{c.clip_id}", c.clip_id)
        for c in primary
    ]
    spacing = Fraction(profile.frame_spacing_seconds).limit_denominator(100)
    regular: list[SamplePoint] = []
    moment = spacing
    while moment < end - spacing / 2:
        clip = next((c for c in primary if c.start_frame <= moment * fps < c.end_frame), None)
        regular.append(
            SamplePoint(
                moment,
                f"clip:{clip.clip_id if clip else 'free'}@{int(moment)}",
                clip.clip_id if clip else None,
            )
        )
        moment += spacing
    ordered = [*first, *_thin(cuts, 2 * 20), *_thin(mids, profile.max_frames), *regular]
    seen: dict[Fraction, SamplePoint] = {}
    for point in ordered:
        seen.setdefault(point.at, point)
    kept = list(seen.values())[: profile.max_frames]
    return tuple(sorted(kept, key=lambda p: p.at))


def _thin(points: list[SamplePoint], limit: int) -> list[SamplePoint]:
    """Every n-th point so that at most ``limit`` remain (pairs of a cut stay together)."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    chosen = {int(i * step) for i in range(limit)}
    paired = {i & ~1 for i in chosen} | {(i & ~1) + 1 for i in chosen}
    return [
        p
        for i, p in enumerate(points)
        if i in (paired if points[0].tag.startswith("cut:") else chosen)
    ][:limit]
