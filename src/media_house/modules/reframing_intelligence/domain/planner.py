"""Plan the framing of ONE shot: who must stay in the picture, where the window sits, how it moves.

Order of reasoning (each step uses only observed evidence):

1. ``select``: the main subject, the participants and the objects the story names are REQUIRED; the
   rest is context.
2. The shot's moments are the observations of the required elements; a moment where an element was
   not observed (a gap) gives no instruction, the window holds.
3. The window starts as wide as the target's shape allows (zoom 1): nothing is cropped away that
   the shape does not force. A tighter zoom exists only for a push-in the story earns and the
   quality policy allows.
4. A single still window is preferred. It is used when one position keeps every required element
   inside (with the profile's margin) and the subject never leaves the dead zone; otherwise the
   window follows, within speed, dwell and zoom-rate limits and looking ahead (``camera``).
5. The result is measured against the observations (``Measured``): visibility, margins, safe areas,
   enlargement, smoothness. The strategy and feasibility follow from those numbers.

When the target has the source's own shape and nothing forces a change, the answer is the original
framing. When the required elements cannot all fit the window of the target's shape, nothing is
dropped silently: the shot is ``unreframeable`` (or, if the profile allows, the fill treatment is
proposed) and the engine lists the alternatives.
"""

import statistics
from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.modules.reframing_intelligence.domain import camera
from media_house.modules.reframing_intelligence.domain.elements import (
    Chosen,
    Selection,
    grid,
    select,
)
from media_house.modules.reframing_intelligence.domain.facts import (
    BeatHint,
    ShotFacts,
    SourceFacts,
    VisualNeed,
)
from media_house.modules.reframing_intelligence.domain.geometry import (
    Box,
    Crop,
    SourceGeometry,
    TargetFormat,
    crop_around,
    effective_scale,
    union_box,
    widest_crop,
)
from media_house.modules.reframing_intelligence.domain.model import (
    CropKeyframe,
    Element,
    Evidence,
    Issue,
    Resolution,
    Score,
    TrackingInterval,
    crop_at,
)
from media_house.modules.reframing_intelligence.domain.profile import ReframingProfile, SafeArea
from media_house.modules.reframing_intelligence.domain.values import (
    CameraMotion,
    Feasibility,
    Interpolation,
    IssueKind,
    ScoreName,
    Severity,
    Strategy,
)
from media_house.modules.reframing_intelligence.domain.verdict import (
    Measured,
    ShotPlan,
    classify,
    confidence_of,
    elements_of,
    feasibility_of,
    intervals_of,
    issues_of,
    name_of,
    preferred_of,
    rational,
    reasons_of,
    reframing_need,
    resolution_of,
    scores_of,
    unfit_labels,
)

#: A subject that moves less than this (picture widths per second) gives no lead-room direction.
_TRAVEL_MIN = 0.03
#: A head turned less than this (degrees) gives no lead-room direction.
_YAW_MIN = 15.0
#: The tolerated composition error (crop widths) at which the horizontal score reaches 0.
_PLACEMENT_TOLERANCE = 0.25
#: Candidate vertical shifts tried to move a subject out of a reserved area.
_NUDGE_STEPS = 9
_EDGE = 1e-4
_EVIDENCE_ISSUES = (
    IssueKind.TRACKING_GAP,
    IssueKind.LOW_TRACKING_CONFIDENCE,
    IssueKind.MISSING_EVIDENCE,
)


@dataclass(slots=True)
class _Moments:
    """Per moment of the grid: what the required elements were doing."""

    t: list[float]
    stamps: list[Fraction]
    union: list[Box | None]
    primary: list[Box | None]
    top: list[float | None]


def plan_shot(
    shot: ShotFacts,
    source: SourceFacts,
    target: TargetFormat,
    profile: ReframingProfile,
    beats: tuple[BeatHint, ...],
    needs: tuple[VisualNeed, ...],
    safe_areas: tuple[SafeArea, ...],
) -> ShotPlan:
    geometry = source.geometry
    selection = select(shot, needs, profile, widest_crop(geometry, target))
    section_ids = tuple(sorted({b.section_id for b in beats}))
    beat_ids = tuple(sorted({b.beat_id for b in beats}))
    evidence = tuple(
        [
            Evidence(kind="beat", ref=b.beat_id, detail=f"{b.function} in section {b.section_id}")
            for b in beats
        ]
        + [
            Evidence(kind="visual_need", ref=n.visual_id, detail=f"{n.purpose}: {n.concept}")
            for n in needs
        ]
    )
    issues = [
        Issue(kind=IssueKind.NOT_TRACKED, severity=Severity.WARNING, detail=d, subject=s)
        for s, d in selection.unprotected
    ]
    if not geometry.exact:
        issues.append(
            Issue(
                kind=IssueKind.SOURCE_GEOMETRY,
                severity=Severity.INFO,
                detail="only the display size is known: pixel shape and stored size are assumed "
                "square and equal, so the enlargement is as good as that assumption",
            )
        )
    primary = selection.primary
    if primary is None:
        return _without_subject(
            shot, geometry, target, profile, section_ids, beat_ids, evidence, issues
        )
    return _plan(
        shot, geometry, target, profile, selection, primary, beats, safe_areas, section_ids,
        beat_ids, evidence, issues,
    )  # fmt: skip


# --- the planned shot --------------------------------------------------------------------------
def _plan(
    shot: ShotFacts,
    geometry: SourceGeometry,
    target: TargetFormat,
    profile: ReframingProfile,
    selection: Selection,
    primary: Chosen,
    beats: tuple[BeatHint, ...],
    safe_areas: tuple[SafeArea, ...],
    section_ids: tuple[str, ...],
    beat_ids: tuple[str, ...],
    evidence: tuple[Evidence, ...],
    issues: list[Issue],
) -> ShotPlan:
    motion, comp = profile.motion, profile.composition
    required = tuple(c for c in selection.chosen if c.required)
    moments = _moments(shot, selection, primary, required, motion.max_gap)
    w0, h0 = widest_crop(geometry, target)
    equal_aspect = (w0, h0) == (1.0, 1.0)
    margin = comp.margin
    need = [_need_zoom(u, w0, h0, margin) for u in moments.union]

    zoom, push = _zooms(shot, geometry, target, profile, beats, moments, need)
    w = [w0 * z for z in zoom]
    h = [h0 * z for z in zoom]
    lo_x, hi_x, lo_y, hi_y, unfit = _limits(moments, w, h, margin)
    wants_x, wants_y, lead = _wants(shot, selection, profile, moments, w, h)

    pans_needed = False
    x_path: list[float]
    y_path: list[float]
    still = not push and len(set(zoom)) == 1
    if still:
        x_path, y_path, pans_needed = _still(
            moments, lo_x, hi_x, lo_y, hi_y, wants_x, wants_y, w0, h0, profile
        )
    else:
        x_path, y_path = _moving(
            moments, lo_x, hi_x, lo_y, hi_y, wants_x, wants_y, w, profile, zooming=True
        )
    if still and pans_needed and motion.follow:
        still = False
        x_path, y_path = _moving(
            moments, lo_x, hi_x, lo_y, hi_y, wants_x, wants_y, w, profile, zooming=False
        )

    points = [
        camera.PathPoint(t, x, y, z)
        for t, x, y, z in zip(moments.t, x_path, y_path, zoom, strict=True)
    ]
    if safe_areas:
        points = _nudge(points, moments, w0, h0, lo_y, hi_y, safe_areas)
    keys = _keyframes(points, moments.stamps, w, w0, h0, profile)
    crops = tuple(k.crop for k in keys)

    m = _measure(shot, geometry, target, profile, required, keys, moments, safe_areas, unfit, w0)
    intervals = intervals_of(shot, primary, motion.max_gap, motion.min_confidence)
    plan_issues = list(issues) + issues_of(m, profile, shot, intervals, primary.coverage)
    strategy, motion_kind = classify(keys, selection, w0, equal_aspect, push, motion.min_change)
    feasibility, executable, tradeoffs = feasibility_of(
        m, profile, shot, strategy, primary.coverage, intervals
    )
    elements = elements_of(selection)
    names = tuple(sorted(name_of(c) for c in required))
    if feasibility is Feasibility.IMPOSSIBLE:
        fits = {
            c.series.track.track_id: all(
                (b := c.series.at(t, motion.max_gap)) is None or (b.width <= w0 and b.height <= h0)
                for t in moments.t
            )
            for c in required
        }
        evidence_issues = [i for i in plan_issues if i.kind in _EVIDENCE_ISSUES]
        return _unreframeable(
            profile, selection, elements, intervals, m, shot, geometry, target, section_ids,
            beat_ids, evidence, [*issues, *evidence_issues], names, unfit_labels(required, fits),
        )  # fmt: skip
    first, last = crops[0], crops[-1]
    reasons = reasons_of(strategy, selection, required, beats, push, lead, equal_aspect, crops)
    return ShotPlan(
        strategy=strategy,
        camera_motion=motion_kind,
        feasibility=feasibility,
        executable=executable,
        keyframes=keys,
        elements=elements,
        intervals=intervals,
        scores=scores_of(m, profile, reframing_need(geometry, target, m), ""),
        resolution=resolution_of(m, geometry),
        confidence=confidence_of(selection, m, shot),
        reasons=tuple(reasons),
        tradeoffs=tuple(tradeoffs),
        preferred_over=tuple(
            preferred_of(strategy, still, pans_needed, motion.follow, push, equal_aspect)
        ),
        issues=tuple(plan_issues),
        evidence=evidence,
        section_ids=section_ids,
        beat_ids=beat_ids,
        subject_start=_subject_view(selection, first, float(shot.start)),
        subject_end=_subject_view(selection, last, float(shot.end)),
        subject_cluster=primary.series.track.cluster,
        required=names,
    )


def _moments(
    shot: ShotFacts,
    selection: Selection,
    primary: Chosen,
    required: tuple[Chosen, ...],
    max_gap: float,
) -> _Moments:
    stamps = grid(shot, required)
    times = [float(s) for s in stamps]
    union: list[Box | None] = []
    prime: list[Box | None] = []
    top: list[float | None] = []
    for t in times:
        boxes = [b for c in required if (b := c.series.at(t, max_gap)) is not None]
        union.append(union_box(boxes))
        p = primary.series.at(t, max_gap)
        prime.append(p)
        face = selection.face.at(t, max_gap) if selection.face is not None else None
        top.append(face.y0 if face is not None else (p.y0 if p is not None else None))
    return _Moments(times, stamps, union, prime, top)


def _need_zoom(u: Box | None, w0: float, h0: float, margin: float) -> float:
    if u is None:
        return 0.0
    room = max(1.0 - 2 * margin, 0.1)
    return max(u.width / (room * w0), u.height / (room * h0))


def _push_in(
    shot: ShotFacts, profile: ReframingProfile, beats: tuple[BeatHint, ...]
) -> tuple[float, float] | None:
    """The span of the shot a push-in covers: where a beat the profile wants emphasised overlaps it
    for long enough."""
    e = profile.emphasis
    if not e.push_in:
        return None
    spans = [
        (float(max(b.start, shot.start)), float(min(b.end, shot.end)))
        for b in beats
        if b.function in e.functions and b.start is not None and b.end is not None
    ]
    spans = [(a, b) for a, b in spans if b - a >= e.min_seconds]
    return max(spans, key=lambda s: s[1] - s[0]) if spans else None


def _zooms(
    shot: ShotFacts,
    geometry: SourceGeometry,
    target: TargetFormat,
    profile: ReframingProfile,
    beats: tuple[BeatHint, ...],
    moments: _Moments,
    need: list[float],
) -> tuple[list[float], bool]:
    n = len(moments.t)
    span = _push_in(shot, profile, beats)
    if span is None:
        return [1.0] * n, False
    e, m = profile.emphasis, profile.motion
    peak_need = max(
        (need[i] for i, t in enumerate(moments.t) if span[0] <= t <= span[1]), default=0.0
    )
    end = max(e.zoom, min(peak_need, 1.0))
    q = profile.quality
    while end < 0.97:  # the tightest zoom the quality policy allows
        scale = effective_scale(_centered(geometry, target, end), geometry, target)
        weak = shot.usability is not None and scale > 1.0 and shot.usability < q.min_usability
        if (
            scale <= q.upscale_max
            and not weak
            and (scale <= q.upscale_ok or shot.usability is not None)
        ):
            break
        end += 0.03
    if end >= 0.97:
        return [1.0] * n, False
    rate = min(m.max_zoom_rate, (1.0 - end) / (span[1] - span[0]))
    want = [max(end, 1.0 - rate * (t - span[0])) if t >= span[0] else 1.0 for t in moments.t]
    path = camera.follow_zoom(moments.t, [min(v, 1.0) for v in need], want, m.max_zoom_rate)
    return path, True


def _centered(geometry: SourceGeometry, target: TargetFormat, zoom: float) -> Crop:
    w0, h0 = widest_crop(geometry, target)
    return Crop((1 - w0 * zoom) / 2, (1 - h0 * zoom) / 2, w0 * zoom, h0 * zoom)


def _limits(
    moments: _Moments, w: list[float], h: list[float], margin: float
) -> tuple[list[float], list[float], list[float], list[float], list[bool]]:
    lo_x, hi_x, lo_y, hi_y, unfit = [], [], [], [], []
    for u, wi, hi in zip(moments.union, w, h, strict=True):
        if u is None:
            lo_x.append(0.0)
            hi_x.append(1.0 - wi)
            lo_y.append(0.0)
            hi_y.append(1.0 - hi)
            unfit.append(False)
            continue
        lx, hx, fit_x = _axis(u.x0, u.x1, wi, margin)
        ly, hy, fit_y = _axis(u.y0, u.y1, hi, margin)
        lo_x.append(min(max(lx, 0.0), 1.0 - wi))
        hi_x.append(max(min(hx, 1.0 - wi), 0.0))
        lo_y.append(min(max(ly, 0.0), 1.0 - hi))
        hi_y.append(max(min(hy, 1.0 - hi), 0.0))
        unfit.append(not (fit_x and fit_y))
        if lo_x[-1] > hi_x[-1]:
            lo_x[-1] = hi_x[-1] = (lo_x[-1] + hi_x[-1]) / 2
        if lo_y[-1] > hi_y[-1]:
            lo_y[-1] = hi_y[-1] = (lo_y[-1] + hi_y[-1]) / 2
    return lo_x, hi_x, lo_y, hi_y, unfit


def _axis(a0: float, a1: float, size: float, margin: float) -> tuple[float, float, bool]:
    """The interval for the window's corner that keeps ``[a0, a1]`` inside with ``margin``."""
    slack = size - (a1 - a0)
    if slack < -1e-9:
        centre = (a0 + a1) / 2 - size / 2
        return centre, centre, False
    keep = min(margin * size, slack / 2)
    return a1 + keep - size, a0 - keep, True


def _lead(
    shot: ShotFacts, selection: Selection, profile: ReframingProfile, moments: _Moments
) -> tuple[int, str]:
    """-1, 0 or 1: the side the subject looks or travels toward (room is left there)."""
    if not profile.composition.lead_room or selection.primary is None:
        return 0, ""
    series = selection.primary.series
    if len(series.times) >= 2:
        span = series.times[-1] - series.times[0]
        travel = (series.boxes[-1].center[0] - series.boxes[0].center[0]) / span if span > 0 else 0
        if abs(travel) >= _TRAVEL_MIN:
            return (1 if travel > 0 else -1), "the subject travels that way"
    if shot.head_yaw is not None and abs(shot.head_yaw) >= _YAW_MIN:
        return (1 if shot.head_yaw > 0 else -1), (
            "the head is turned that way (positive yaw is ASSUMED to look toward the right edge; "
            "the sign convention is not validated upstream)"
        )
    return 0, ""


def _wants(
    shot: ShotFacts,
    selection: Selection,
    profile: ReframingProfile,
    moments: _Moments,
    w: list[float],
    h: list[float],
) -> tuple[list[float | None], list[float | None], str]:
    comp = profile.composition
    direction, why = _lead(shot, selection, profile, moments)
    anchor = comp.anchor_x - direction * comp.lead_room_shift
    xs: list[float | None] = []
    ys: list[float | None] = []
    for p, top, wi, hi in zip(moments.primary, moments.top, w, h, strict=True):
        if p is None or top is None:
            xs.append(None)
            ys.append(None)
            continue
        xs.append(p.center[0] - anchor * wi)
        ys.append(top - comp.headroom * hi)
    return (
        xs,
        ys,
        (f"leaving room on the side the subject looks or moves: {why}" if direction else ""),
    )


def _still(
    moments: _Moments,
    lo_x: list[float],
    hi_x: list[float],
    lo_y: list[float],
    hi_y: list[float],
    wants_x: list[float | None],
    wants_y: list[float | None],
    w0: float,
    h0: float,
    profile: ReframingProfile,
) -> tuple[list[float], list[float], bool]:
    """One position for the whole shot, and whether the subject moves too much for it."""
    dead = profile.motion.dead_zone
    pans = False
    out: list[list[float]] = []
    for lo, hi, wants, size in ((lo_x, hi_x, wants_x, w0), (lo_y, hi_y, wants_y, h0)):
        low, high = max(lo), min(hi)
        seen = [v for v in wants if v is not None]
        if low > high:  # no single position keeps everything: the best compromise
            pans = pans or True
            low = high = (low + high) / 2
        pick = min(max(statistics.median(seen) if seen else (low + high) / 2, low), high)
        if seen and size < 1.0 and max(abs(v - pick) for v in seen) > dead * size:
            pans = True
        out.append([pick] * len(moments.t))
    return out[0], out[1], pans


def _moving(
    moments: _Moments,
    lo_x: list[float],
    hi_x: list[float],
    lo_y: list[float],
    hi_y: list[float],
    wants_x: list[float | None],
    wants_y: list[float | None],
    w: list[float],
    profile: ReframingProfile,
    *,
    zooming: bool,
) -> tuple[list[float], list[float]]:
    """``zooming``: the window changes size, so dead zone, dwell and minimum change are dropped: a
    push-in stays anchored on the subject (within the speed limit) instead of drifting toward the
    picture's corner in steps."""
    m = profile.motion
    speed = [m.max_speed * wi for wi in w]
    dead = [0.0 if zooming else m.dead_zone * wi for wi in w]
    change = 0.0 if zooming else min(m.min_change * wi for wi in w)
    dwell = 0.0 if zooming else m.min_dwell
    paths = []
    tau = 0.0 if zooming else m.smoothing
    for lo, hi, raw in ((lo_x, hi_x, wants_x), (lo_y, hi_y, wants_y)):
        wants = camera.smooth(moments.t, raw, m.smoothing)
        if len(moments.t) < 2:
            paths.append(
                [(lo[0] + hi[0]) / 2 if wants[0] is None else min(max(wants[0], lo[0]), hi[0])]
            )
            continue
        paths.append(camera.follow(moments.t, lo, hi, wants, speed, dead, dwell, change, tau))
    return paths[0], paths[1]


def _nudge(
    points: list[camera.PathPoint],
    moments: _Moments,
    w0: float,
    h0: float,
    lo_y: list[float],
    hi_y: list[float],
    safe_areas: tuple[SafeArea, ...],
) -> list[camera.PathPoint]:
    """Shift the whole path vertically (where there is room) out of the reserved areas."""
    low = max(lo - p.y for lo, p in zip(lo_y, points, strict=True))
    high = min(hi - p.y for hi, p in zip(hi_y, points, strict=True))
    if high - low < _EDGE or (h0 >= 1.0 and points[0].z >= 1.0):
        return points
    candidates = sorted(
        {0.0, *(low + (high - low) * k / (_NUDGE_STEPS - 1) for k in range(_NUDGE_STEPS))},
        key=abs,
    )

    def overlap(dy: float) -> float:
        worst = 0.0
        for p, box in zip(points, moments.primary, strict=True):
            if box is None:
                continue
            crop = Crop(p.x, p.y + dy, w0 * p.z, h0 * p.z)
            worst = max(worst, _in_reserved(crop, box, safe_areas))
        return worst

    best = min(candidates, key=lambda dy: (round(overlap(dy), 4), abs(dy)))
    return [replace(p, y=p.y + best) for p in points] if best else points


def _in_reserved(crop: Crop, box: Box, areas: tuple[SafeArea, ...]) -> float:
    x0, y0, x1, y1 = crop.to_output(box)
    x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, 1.0), min(y1, 1.0)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (x1 - x0) * (y1 - y0)
    taken = 0.0
    for a in areas:  # reserved areas may overlap; counting each once is close enough and stable
        wx, wy = min(x1, a.x1) - max(x0, a.x0), min(y1, a.y1) - max(y0, a.y0)
        if wx > 0 and wy > 0:
            taken += wx * wy
    return min(1.0, taken / area)


def _keyframes(
    points: list[camera.PathPoint],
    stamps: list[Fraction],
    w: list[float],
    w0: float,
    h0: float,
    profile: ReframingProfile,
) -> tuple[CropKeyframe, ...]:
    m = profile.motion
    by_time = dict(zip((p.t for p in points), stamps, strict=True))
    kept = camera.simplify(points, w, m.key_tolerance, m.key_tolerance)
    if len(kept) > 1 and _barely_moves(kept, w0, m.min_change):
        kept = kept[:1]
    keys: list[CropKeyframe] = []
    for i, p in enumerate(kept):
        keys.append(
            CropKeyframe(
                time=rational(by_time[p.t]),
                crop=Crop(p.x, p.y, w0 * p.z, h0 * p.z).rounded(),
                interpolation=Interpolation.LINEAR if i < len(kept) - 1 else Interpolation.HOLD,
            )
        )
    return tuple(keys)


def _barely_moves(points: list[camera.PathPoint], w0: float, min_change: float) -> bool:
    a = points[0]
    return all(
        abs(p.x - a.x) <= min_change * w0 * a.z
        and abs(p.y - a.y) <= min_change * w0 * a.z
        and abs(p.z - a.z) <= min_change
        for p in points
    )


# --- measuring ---------------------------------------------------------------------------------
def _measure(
    shot: ShotFacts,
    geometry: SourceGeometry,
    target: TargetFormat,
    profile: ReframingProfile,
    required: tuple[Chosen, ...],
    keys: tuple[CropKeyframe, ...],
    moments: _Moments,
    safe_areas: tuple[SafeArea, ...],
    unfit: list[bool],
    w0: float,
) -> Measured:
    comp, motion = profile.composition, profile.motion
    margin = max(comp.margin, 0.01)
    worst_visible, risk, source_edge = 1.0, 0.0, False
    seen: dict[str, float] = {}
    place, headroom, reserved = [], [], 0.0
    anchor = comp.anchor_x
    for t, p, top in zip(moments.t, moments.primary, moments.top, strict=True):
        crop = crop_at(keys, t)
        for c in required:
            box = c.series.at(t, motion.max_gap)
            if box is None:
                continue
            share = crop.inside_share(box)
            name = c.series.track.track_id
            seen[name] = min(seen.get(name, 1.0), share)
            worst_visible = min(worst_visible, share)
            for side, gap in enumerate(crop.margins(box)):
                at_frame_edge = (
                    (side == 0 and crop.x <= _EDGE)
                    or (side == 1 and crop.y <= _EDGE)
                    or (side == 2 and crop.x1 >= 1 - _EDGE)
                    or (side == 3 and crop.y1 >= 1 - _EDGE)
                )
                if at_frame_edge:
                    source_edge = source_edge or gap < margin / 2
                    continue
                risk = max(risk, 1.0 if gap < 0 else min(1.0, max(0.0, (margin - gap) / margin)))
            reserved = max(reserved, _in_reserved(crop, box, safe_areas)) if safe_areas else 0.0
        if p is not None and top is not None:
            place.append(abs((p.center[0] - crop.x) / crop.w - anchor))
            headroom.append((top - crop.y) / crop.h)
    composition = None
    if place:
        horizontal = max(0.0, 1.0 - statistics.fmean(place) / _PLACEMENT_TOLERANCE)
        vertical = max(
            0.0,
            1.0 - abs(statistics.fmean(headroom) - comp.headroom) / comp.headroom_tolerance,
        )
        composition = round((horizontal + vertical) / 2, 4)
    scales = [effective_scale(k.crop, geometry, target) for k in keys]
    widths = [k.crop.w / w0 for k in keys]
    path = [
        camera.PathPoint(float(k.time.numerator) / k.time.denominator, k.crop.x, k.crop.y, z)
        for k, z in zip(keys, widths, strict=True)
    ]
    speed, zoom_rate = camera.peak_speed(path, [k.crop.w for k in keys])
    allowed = max(1.0, shot.seconds / max(motion.min_dwell, 0.5))
    changes = max(len(keys) - 1, 0)
    smooth = 1.0 - min(
        1.0, max(speed / motion.max_speed, zoom_rate / motion.max_zoom_rate, changes / allowed)
    )
    confidences = [x for c in required for x in c.series.confidences]
    people = list(seen.values())
    return Measured(
        visibility=round(worst_visible, 4),
        multi_visibility=(
            round(sum(1 for v in people if v >= 0.999) / len(people), 4)
            if len(people) >= 2
            else None
        ),
        edge_risk=round(risk, 4),
        source_edge=source_edge,
        composition=composition,
        safe_area=round(1.0 - reserved, 4) if safe_areas else None,
        tracking_confidence=round(statistics.fmean(confidences), 4) if confidences else None,
        scale_min=round(min(scales), 4),
        scale_max=round(max(scales), 4),
        smoothness=round(smooth, 4),
        peak_speed=speed,
        peak_zoom_rate=zoom_rate,
        changes=changes,
        unfit_share=round(sum(unfit) / len(unfit), 4) if unfit else 0.0,
    )


def _subject_view(selection: Selection, crop: Crop, t: float) -> tuple[float, float, float] | None:
    """Height, centre x and top (all across the output) of the main subject at ``t``."""
    primary = selection.primary
    if primary is None:
        return None
    box = primary.series.at(t, 1e9)
    if box is None:
        return None
    return (
        round(box.height / crop.h, 4),
        round((box.center[0] - crop.x) / crop.w, 4),
        round((box.y0 - crop.y) / crop.h, 4),
    )


# --- no subject, no window ---------------------------------------------------------------------
def _unmeasured(scale: float | None, tracking: float | None = None) -> Measured:
    return Measured(
        visibility=None,
        multi_visibility=None,
        edge_risk=None,
        source_edge=False,
        composition=None,
        safe_area=None,
        tracking_confidence=tracking,
        scale_min=scale,
        scale_max=scale,
        smoothness=1.0,
        peak_speed=0.0,
        peak_zoom_rate=0.0,
        changes=0,
        unfit_share=0.0,
    )


def failed_shot(
    shot: ShotFacts,
    geometry: SourceGeometry,
    target: TargetFormat,
    profile: ReframingProfile,
    beats: tuple[BeatHint, ...],
    reason: str,
) -> ShotPlan:
    """Planning this shot broke an invariant (bad evidence or a planner bug): a placeholder that
    says so and is never executable, so the rest of the analysis survives."""
    issue = Issue(
        kind=IssueKind.INVALID_GEOMETRY,
        severity=Severity.UNRESOLVED,
        detail=f"the framing of this shot could not be planned: {reason}",
    )
    return _without_subject(
        shot,
        geometry,
        target,
        profile,
        tuple(sorted({b.section_id for b in beats})),
        tuple(sorted({b.beat_id for b in beats})),
        (),
        [issue],
        failed=True,
    )


def _without_subject(
    shot: ShotFacts,
    geometry: SourceGeometry,
    target: TargetFormat,
    profile: ReframingProfile,
    section_ids: tuple[str, ...],
    beat_ids: tuple[str, ...],
    evidence: tuple[Evidence, ...],
    issues: list[Issue],
    *,
    failed: bool = False,
) -> ShotPlan:
    """Nothing was tracked in the shot. The original framing is safe when the shape already
    matches; otherwise any window is a PLACEHOLDER (the attention point if there is one, else the
    centre) that must not be applied unseen."""
    w0, h0 = widest_crop(geometry, target)
    equal = (w0, h0) == (1.0, 1.0)
    issues = [
        *issues,
        Issue(
            kind=IssueKind.MISSING_EVIDENCE,
            severity=Severity.UNRESOLVED,
            detail="no subject was tracked in this shot, so nothing says what the window must keep",
        ),
    ]
    if equal and not failed:
        crop = Crop(0.0, 0.0, 1.0, 1.0)
        strategy, feasibility, executable = Strategy.ORIGINAL, Feasibility.FEASIBLE, True
        reasons = [
            "keep the original framing: the source has the target's shape and no subject "
            "was tracked that a change could serve"
        ]
    else:
        cx, cy = shot.attention if shot.attention is not None else (0.5, 0.5)
        crop = crop_around(geometry, target, 1.0, cx, cy).rounded()
        strategy, feasibility, executable = Strategy.STATIC_CROP, Feasibility.UNCERTAIN, False
        where = "the picture's attention point" if shot.attention is not None else "the centre"
        reasons = [f"a window on {where} is a placeholder, not a finding: no subject was tracked"]
    scale = effective_scale(crop, geometry, target)
    m = _unmeasured(round(scale, 4))
    key = CropKeyframe(time=rational(shot.start), crop=crop, interpolation=Interpolation.HOLD)
    return ShotPlan(
        strategy=strategy,
        camera_motion=CameraMotion.NONE,
        feasibility=feasibility,
        executable=executable,
        keyframes=(key,),
        elements=(),
        intervals=(),
        scores=scores_of(
            m, profile, reframing_need(geometry, target, None) if not equal else 0.0, ""
        ),
        resolution=resolution_of(m, geometry),
        confidence=confidence_of(Selection((), None, None, ()), m, shot),
        reasons=tuple(reasons),
        tradeoffs=(),
        preferred_over=(),
        issues=tuple(issues),
        evidence=evidence,
        section_ids=section_ids,
        beat_ids=beat_ids,
    )


def _unreframeable(
    profile: ReframingProfile,
    selection: Selection,
    elements: tuple[Element, ...],
    intervals: tuple[TrackingInterval, ...],
    m: Measured,
    shot: ShotFacts,
    geometry: SourceGeometry,
    target: TargetFormat,
    section_ids: tuple[str, ...],
    beat_ids: tuple[str, ...],
    evidence: tuple[Evidence, ...],
    issues: list[Issue],
    required: tuple[str, ...],
    omitted: tuple[str, ...],
) -> ShotPlan:
    """No window of the target's shape holds what is required. Nothing is dropped silently."""
    fill = profile.allow_fill
    held = ", ".join(omitted) or "the required elements"
    detail = (
        f"no window of the {target.name} shape holds {held} together with the main subject "
        f"(they do not fit at {m.unfit_share:.0%} of the moments)"
    )
    issues = [
        *issues,
        Issue(kind=IssueKind.UNREFRAMEABLE, severity=Severity.WARNING, detail=detail),
        *(
            Issue(
                kind=IssueKind.ELEMENT_OMITTED,
                severity=Severity.WARNING,
                detail=f"{name} cannot be kept in the picture",
                subject=name,
            )
            for name in omitted
        ),
    ]
    blank = _unmeasured(None, m.tracking_confidence)
    scores = tuple(
        Score(
            name=s.name,
            value=0.0,
            basis=s.basis,
        )
        if s.name is ScoreName.CROP_FEASIBILITY
        else s
        for s in scores_of(blank, profile, 1.0, "")
    )
    return ShotPlan(
        strategy=Strategy.FILL_TREATMENT if fill else Strategy.UNREFRAMEABLE,
        camera_motion=CameraMotion.NONE,
        feasibility=Feasibility.IMPOSSIBLE,
        executable=False,
        keyframes=(),
        elements=elements,
        intervals=intervals,
        scores=scores,
        resolution=Resolution(scale_min=None, scale_max=None, exact=geometry.exact),
        confidence=confidence_of(selection, blank, shot),
        reasons=(
            detail,
            "propose the original frame with a fill (blurred or letterboxed background): no stage "
            "here can render it, so it stays a proposal"
            if fill
            else "no crop is recommended",
        ),
        tradeoffs=(),
        preferred_over=(f"a crop that drops {held}: required material would vanish silently",),
        issues=tuple(issues),
        evidence=evidence,
        section_ids=section_ids,
        beat_ids=beat_ids,
        required=required,
        omitted=omitted,
    )
