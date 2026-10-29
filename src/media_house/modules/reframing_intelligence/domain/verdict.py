"""From measurements to a verdict: strategy, feasibility, scores, issues and the words that explain.

Nothing is measured here and nothing is decided by taste alone: ``Measured`` holds the numbers the
planner computed from the observed boxes; this module only classifies them against the profile.
Feasibility has a fixed precedence (impossible > uncertain > low quality > trade-offs >
feasible), and every non-feasible answer names the number that caused it.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.reframing_intelligence.domain.elements import Chosen, Selection
from media_house.modules.reframing_intelligence.domain.facts import BeatHint, ShotFacts
from media_house.modules.reframing_intelligence.domain.geometry import (
    Crop,
    SourceGeometry,
    TargetFormat,
)
from media_house.modules.reframing_intelligence.domain.model import (
    Confidence,
    CropKeyframe,
    Element,
    Evidence,
    Issue,
    Resolution,
    Score,
    TrackingInterval,
)
from media_house.modules.reframing_intelligence.domain.profile import ReframingProfile
from media_house.modules.reframing_intelligence.domain.values import (
    CameraMotion,
    Feasibility,
    IntervalState,
    IssueKind,
    ScoreBasis,
    ScoreName,
    Severity,
    Strategy,
)

#: A shot whose required elements do not fit the window for more than this share of its moments
#: is unreframeable; a smaller share is a clipped trade-off.
UNFIT_SHARE = 0.1
#: A main subject tracked for less of the shot than this is too little to place a window on.
MIN_COVERAGE = 0.25
#: A crop whose subject sits more than this (crop widths) off the centre was placed on purpose.
REPOSITION = 0.05
#: A zooming window whose centre moves less than this (crop widths) is not panning.
PAN_WITH_ZOOM = 0.1
#: Peak speeds this much over the limit are reported (a hair over is rounding).
_SPEED_SLACK = 1.05


@dataclass(frozen=True, slots=True)
class Measured:
    """What the chosen crop does to the observed elements (all derived from observed boxes)."""

    visibility: float | None
    multi_visibility: float | None
    edge_risk: float | None
    source_edge: bool
    composition: float | None
    safe_area: float | None
    tracking_confidence: float | None
    scale_min: float | None
    scale_max: float | None
    smoothness: float
    peak_speed: float
    peak_zoom_rate: float
    changes: int
    unfit_share: float


@dataclass(frozen=True, slots=True)
class ShotPlan:
    """The planner's answer for one shot; the engine adds identity, alternatives and decisions."""

    strategy: Strategy
    camera_motion: CameraMotion
    feasibility: Feasibility
    executable: bool
    keyframes: tuple[CropKeyframe, ...]
    elements: tuple[Element, ...]
    intervals: tuple[TrackingInterval, ...]
    scores: tuple[Score, ...]
    resolution: Resolution
    confidence: Confidence
    reasons: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    preferred_over: tuple[str, ...]
    issues: tuple[Issue, ...]
    evidence: tuple[Evidence, ...]
    section_ids: tuple[str, ...]
    beat_ids: tuple[str, ...]
    #: The main subject's height, centre x and top across the output at the start and the end.
    subject_start: tuple[float, float, float] | None = None
    subject_end: tuple[float, float, float] | None = None
    subject_cluster: str | None = None
    #: Labels of the required elements, and of those no window can hold together.
    required: tuple[str, ...] = ()
    omitted: tuple[str, ...] = ()


def rational(value: Fraction) -> Rational:
    return Rational(max(value.numerator, 0), value.denominator)


# --- classification ----------------------------------------------------------------------------
def classify(
    keys: tuple[CropKeyframe, ...],
    selection: Selection,
    w0: float,
    equal_aspect: bool,
    pushed: bool,
    min_change: float,
) -> tuple[Strategy, CameraMotion]:
    if len(keys) == 1:
        crop = keys[0].crop
        if equal_aspect and crop.w >= 0.999 and crop.h >= 0.999:
            return Strategy.ORIGINAL, CameraMotion.NONE
        primary = selection.primary
        if primary is not None and primary.series.boxes:
            cx = sorted(b.center[0] for b in primary.series.boxes)[len(primary.series.boxes) // 2]
            if abs((cx - crop.x) / crop.w - 0.5) > REPOSITION:
                return Strategy.REPOSITIONED_CROP, CameraMotion.NONE
        return Strategy.STATIC_CROP, CameraMotion.NONE
    zoom = [k.crop.w / w0 for k in keys]
    zoomed = max(zoom) - min(zoom) >= min_change
    first = keys[0].crop.center
    # While the window zooms, re-anchoring the subject moves its centre a little (headroom, a
    # clamp at the picture edge): that is still a push-in, not a pan.
    limit = PAN_WITH_ZOOM if zoomed else min_change
    panned = any(
        max(abs(k.crop.center[0] - first[0]), abs(k.crop.center[1] - first[1])) > limit * k.crop.w
        for k in keys
    )
    if zoomed and not panned:
        if zoom[-1] < zoom[0]:
            return Strategy.PUSH_IN, CameraMotion.PUSH_IN
        return Strategy.PULL_OUT, CameraMotion.PULL_OUT
    if zoomed:
        return Strategy.DYNAMIC_CROP, CameraMotion.MIXED
    return Strategy.DYNAMIC_CROP, CameraMotion.FOLLOW


def feasibility_of(
    m: Measured,
    profile: ReframingProfile,
    shot: ShotFacts,
    strategy: Strategy,
    coverage: float,
    intervals: tuple[TrackingInterval, ...],
) -> tuple[Feasibility, bool, list[str]]:
    q = profile.quality
    if m.unfit_share > UNFIT_SHARE:
        return Feasibility.IMPOSSIBLE, False, []
    scale, usable = m.scale_max, shot.usability
    trade: list[str] = []
    if scale is not None and scale > q.upscale_max:
        kind = Feasibility.LOW_QUALITY
        trade.append(f"the picture is enlarged {scale:.2f}x (policy allows {q.upscale_max:.2f}x)")
    elif scale is not None and scale > 1.0 and usable is not None and usable < q.min_usability:
        kind = Feasibility.LOW_QUALITY
        trade.append(
            f"enlarged {scale:.2f}x from footage of low usability "
            f"({usable:.2f} < {q.min_usability})"
        )
    elif scale is not None and scale > q.upscale_ok and usable is None:
        kind = Feasibility.UNCERTAIN
        trade.append(f"enlarged {scale:.2f}x and no quality measurement says how soft it will look")
    elif coverage < MIN_COVERAGE:
        kind = Feasibility.UNCERTAIN
        trade.append(f"the main subject is tracked for only {coverage:.0%} of the shot")
    else:
        kind = Feasibility.FEASIBLE
    if m.edge_risk:
        trade.append("a required element comes closer to the window edge than the margin")
    if m.unfit_share > 0:
        trade.append(
            f"required elements do not fit the window at {m.unfit_share:.0%} of the moments"
        )
    if m.composition is not None and m.composition < 0.5 and strategy is not Strategy.ORIGINAL:
        trade.append("the subject could not be placed where the profile prefers")
    if m.safe_area is not None and m.safe_area < 0.999:
        trade.append(f"{1 - m.safe_area:.0%} of a required element lies in a reserved area")
    if m.peak_speed > profile.motion.max_speed * _SPEED_SLACK:
        trade.append("keeping the subject inside needs the window to move faster than the limit")
    if scale is not None and q.upscale_ok < scale <= q.upscale_max and usable is not None:
        trade.append(f"enlarged {scale:.2f}x")
    if any(i.state is IntervalState.GAP for i in intervals):
        trade.append("the subject was not observed for part of the shot; the window holds there")
    if kind is Feasibility.FEASIBLE and trade:
        kind = Feasibility.TRADEOFFS
    executable = kind in (Feasibility.FEASIBLE, Feasibility.TRADEOFFS, Feasibility.LOW_QUALITY)
    return kind, executable, trade


def issues_of(
    m: Measured,
    profile: ReframingProfile,
    shot: ShotFacts,
    intervals: tuple[TrackingInterval, ...],
    coverage: float,
) -> list[Issue]:
    q, motion = profile.quality, profile.motion
    out: list[Issue] = []
    if m.visibility is not None and m.visibility < 0.999:
        out.append(
            Issue(
                kind=IssueKind.ELEMENT_CLIPPED,
                severity=Severity.WARNING,
                detail=f"a required element is cut by the window (at worst {m.visibility:.0%} of "
                "it lies inside)",
            )
        )
    elif m.edge_risk:
        out.append(
            Issue(
                kind=IssueKind.EDGE_PROXIMITY,
                severity=Severity.INFO,
                detail="a required element comes closer to the window edge than the "
                f"{profile.composition.margin:.0%} margin",
            )
        )
    if m.source_edge:
        out.append(
            Issue(
                kind=IssueKind.SOURCE_EDGE,
                severity=Severity.INFO,
                detail="a required element touches the edge of the source picture: the footage "
                "itself is tight there, no crop can add room",
            )
        )
    if (
        m.peak_speed > motion.max_speed * _SPEED_SLACK
        or m.peak_zoom_rate > motion.max_zoom_rate * _SPEED_SLACK
    ):
        out.append(
            Issue(
                kind=IssueKind.SPEED_LIMIT,
                severity=Severity.WARNING,
                detail=f"the window moves {m.peak_speed:.2f} crop widths/s "
                f"(limit {motion.max_speed}) "
                f"and zooms {m.peak_zoom_rate:.2f}/s (limit {motion.max_zoom_rate}): holding the "
                "subject inside took priority over the limit",
            )
        )
    if m.scale_max is not None and m.scale_max > q.upscale_ok:
        out.append(
            Issue(
                kind=IssueKind.LOW_QUALITY if m.scale_max > q.upscale_max else IssueKind.UPSCALE,
                severity=Severity.WARNING if m.scale_max > q.upscale_max else Severity.INFO,
                detail=f"the crop enlarges the picture up to {m.scale_max:.2f}x",
            )
        )
        if shot.usability is None:
            out.append(
                Issue(
                    kind=IssueKind.MISSING_EVIDENCE,
                    severity=Severity.UNRESOLVED,
                    detail="no usability measurement of the shot: how soft the enlarged crop "
                    "looks is unknown",
                )
            )
    if m.safe_area is not None and m.safe_area < 0.999:
        out.append(
            Issue(
                kind=IssueKind.SAFE_AREA,
                severity=Severity.WARNING,
                detail=f"{1 - m.safe_area:.0%} of a required element lies in a reserved area",
            )
        )
    gaps = [i for i in intervals if i.state is IntervalState.GAP]
    if gaps:
        out.append(
            Issue(
                kind=IssueKind.TRACKING_GAP,
                severity=Severity.WARNING,
                detail=f"the main subject was not observed for {len(gaps)} stretch(es) longer than "
                f"{motion.max_gap}s; the window holds its last position there (nothing is "
                "interpolated across a gap)",
            )
        )
    if any(i.state is IntervalState.LOW_CONFIDENCE for i in intervals):
        out.append(
            Issue(
                kind=IssueKind.LOW_TRACKING_CONFIDENCE,
                severity=Severity.INFO,
                detail=f"observations below confidence {motion.min_confidence} were not used",
            )
        )
    if coverage < MIN_COVERAGE:
        out.append(
            Issue(
                kind=IssueKind.LOW_TRACKING_CONFIDENCE,
                severity=Severity.WARNING,
                detail=f"the main subject is tracked for only {coverage:.0%} of the shot",
            )
        )
    return out


# --- what the shot contains --------------------------------------------------------------------
def elements_of(selection: Selection) -> tuple[Element, ...]:
    rows = sorted(
        selection.chosen, key=lambda c: (c.importance.value != "primary", c.series.track.track_id)
    )
    return tuple(
        Element(
            track_id=c.series.track.track_id,
            kind=c.series.track.kind,
            label=c.series.track.label,
            importance=c.importance,
            required=c.required,
            reason=c.reason,
            coverage=round(c.coverage, 4),
            confidence=None
            if c.series.mean_confidence is None
            else round(c.series.mean_confidence, 4),
        )
        for c in rows
    )


def intervals_of(
    shot: ShotFacts, primary: Chosen, max_gap: float, min_confidence: float
) -> tuple[TrackingInterval, ...]:
    """Where the main subject was observed, where it was not (a gap), and where only weak
    observations existed."""
    series = primary.series
    track_id = series.track.track_id
    start, end = shot.start, shot.end
    out: list[TrackingInterval] = []
    times, stamps = series.times, series.stamps
    if not times:
        return (
            TrackingInterval(
                start=rational(start), end=rational(end), state=IntervalState.GAP, track_id=track_id
            ),
        )
    lead = max(times[0] - float(start), 0.0)
    if lead > max_gap / 2:
        out.append(
            _span(start, stamps[0], IntervalState.GAP, track_id, "before the first observation")
        )
    run_start = stamps[0]
    for i in range(1, len(times)):
        if times[i] - times[i - 1] > max_gap:
            out.append(_span(run_start, stamps[i - 1], IntervalState.TRACKED, track_id, ""))
            out.append(
                _span(stamps[i - 1], stamps[i], IntervalState.GAP, track_id, "no observation")
            )
            run_start = stamps[i]
    out.append(_span(run_start, stamps[-1], IntervalState.TRACKED, track_id, ""))
    if float(end) - times[-1] > max_gap / 2:
        out.append(
            _span(stamps[-1], end, IntervalState.GAP, track_id, "after the last observation")
        )
    if series.low_confidence:
        weak = series.low_confidence
        out.append(
            TrackingInterval(
                start=rational(Fraction(weak[0]).limit_denominator(1000)),
                end=rational(Fraction(weak[-1]).limit_denominator(1000)),
                state=IntervalState.LOW_CONFIDENCE,
                track_id=track_id,
                detail=f"{len(weak)} observation(s) below confidence {min_confidence} not used",
            )
        )
    return tuple(i for i in out if float(rational_value(i.end)) >= float(rational_value(i.start)))


def rational_value(r: Rational) -> float:
    return r.numerator / r.denominator


def _span(
    a: Fraction, b: Fraction, state: IntervalState, track_id: str, detail: str
) -> TrackingInterval:
    return TrackingInterval(
        start=rational(a), end=rational(b), state=state, track_id=track_id, detail=detail
    )


def confidence_of(selection: Selection, m: Measured, shot: ShotFacts) -> Confidence:
    primary = selection.primary
    if primary is None:
        return Confidence(value=None)
    parts = [
        ("coverage", round(primary.coverage, 4)),
        ("tracking", m.tracking_confidence if m.tracking_confidence is not None else 0.0),
        (
            "quality_known",
            1.0 if shot.usability is not None or (m.scale_max or 1.0) <= 1.0 else 0.85,
        ),
    ]
    value = 1.0
    for _, v in parts:
        value *= v
    return Confidence(value=round(value, 4), components=tuple(parts))


# --- scores ------------------------------------------------------------------------------------
def scores_of(
    m: Measured, profile: ReframingProfile, reframing_need: float | None, need_reason: str
) -> tuple[Score, ...]:
    q = profile.quality

    def s(name: ScoreName, value: float | None, basis: ScoreBasis, why: str = "") -> Score:
        return Score(name=name, value=value, basis=basis, reason=why if value is None else "")

    geometric = 1.0
    risk = m.edge_risk if m.edge_risk is not None else 0.0
    visibility = m.visibility
    feasibility = (
        None if visibility is None else round(geometric * min(visibility, 1.0 - risk / 2), 4)
    )
    if m.scale_max is None:
        resolution = None
    else:
        resolution = round(
            max(0.0, min(1.0, (q.upscale_max - m.scale_max) / (q.upscale_max - 1.0))), 4
        )
    return (
        s(
            ScoreName.SUBJECT_VISIBILITY,
            visibility,
            ScoreBasis.MEASURED,
            "no required element was observed",
        ),
        s(
            ScoreName.COMPOSITION,
            m.composition,
            ScoreBasis.HEURISTIC,
            "the main subject was not observed",
        ),
        s(
            ScoreName.CROP_FEASIBILITY,
            feasibility,
            ScoreBasis.MEASURED,
            "no required element was observed",
        ),
        s(ScoreName.EFFECTIVE_RESOLUTION, resolution, ScoreBasis.ESTIMATED, "no source resolution"),
        s(
            ScoreName.TRACKING_CONFIDENCE,
            m.tracking_confidence,
            ScoreBasis.OBSERVED,
            "no tracked element",
        ),
        s(
            ScoreName.EDGE_CLIPPING_RISK,
            m.edge_risk,
            ScoreBasis.MEASURED,
            "no required element was observed",
        ),
        s(
            ScoreName.MULTI_SUBJECT_VISIBILITY,
            m.multi_visibility,
            ScoreBasis.MEASURED,
            "fewer than two required elements",
        ),
        s(
            ScoreName.CAPTION_SAFE_AREA,
            m.safe_area,
            ScoreBasis.MEASURED,
            "no safe area is configured",
        ),
        s(
            ScoreName.CONTINUITY_COMPATIBILITY,
            None,
            ScoreBasis.HEURISTIC,
            "no neighbouring shot was judged",
        ),
        s(ScoreName.MOTION_SMOOTHNESS, m.smoothness, ScoreBasis.MEASURED),
        s(ScoreName.REFRAMING_NEED, reframing_need, ScoreBasis.HEURISTIC, need_reason),
    )


def resolution_of(m: Measured, geometry: SourceGeometry) -> Resolution:
    return Resolution(scale_min=m.scale_min, scale_max=m.scale_max, exact=geometry.exact)


def reframing_need(geometry: SourceGeometry, target: TargetFormat, m: Measured | None) -> float:
    """How much the original picture fails the target: the aspect mismatch (how much a crop must
    discard, 0 for the same shape, 1 from a factor of two) or, if larger, a clipped subject."""
    import math

    mismatch = abs(math.log(float(geometry.aspect / target.ratio))) / math.log(2)
    clipped = 0.0 if m is None or m.edge_risk is None else m.edge_risk
    return round(min(1.0, max(mismatch, clipped)), 4)


# --- words -------------------------------------------------------------------------------------
_STRATEGY_TEXT = {
    Strategy.ORIGINAL: "keep the original framing",
    Strategy.STATIC_CROP: "use one still crop",
    Strategy.REPOSITIONED_CROP: "use one still crop with the subject placed off-centre",
    Strategy.DYNAMIC_CROP: "let the crop follow the subject",
    Strategy.PUSH_IN: "push in slowly",
    Strategy.PULL_OUT: "pull out slowly",
}


def reasons_of(
    strategy: Strategy,
    selection: Selection,
    required: tuple[Chosen, ...],
    beats: tuple[BeatHint, ...],
    pushed: bool,
    lead: str,
    equal_aspect: bool,
    crops: tuple[Crop, ...],
) -> list[str]:
    names = ", ".join(sorted(name_of(c) for c in required))
    out = [f"{_STRATEGY_TEXT[strategy]}; keeps {names} inside the window"]
    if strategy is Strategy.ORIGINAL:
        out.append("the source already has the target's shape and nothing in it needs a change")
    if pushed:
        serving = ", ".join(sorted({f"{b.function} ({b.beat_id})" for b in beats}))
        out.append(f"the push-in serves the story beat(s): {serving}")
    elif beats:
        out.append("serves " + ", ".join(sorted({f"{b.function} ({b.beat_id})" for b in beats})))
    if lead:
        out.append(lead)
    return out


def preferred_of(
    strategy: Strategy,
    still: bool,
    pans_needed: bool,
    follow: bool,
    pushed: bool,
    equal_aspect: bool,
) -> list[str]:
    out: list[str] = []
    if strategy is Strategy.ORIGINAL:
        out.append("any crop would discard picture without a reason: the shape already matches")
    elif strategy in (Strategy.STATIC_CROP, Strategy.REPOSITIONED_CROP):
        out.append(
            "a moving window: the subject stays inside one position, movement would only distract"
        )
    elif strategy is Strategy.DYNAMIC_CROP and pans_needed:
        out.append(
            "one still crop: no single position keeps every required element, or the subject "
            "leaves the dead zone"
        )
    if pushed:
        out.append(
            "a still crop: the story beat earns a slow push-in and the quality policy allows it"
        )
    if not follow and strategy.has_crop and not still:
        out.append("(the profile forbids a moving window)")
    return out


def unfit_labels(required: tuple[Chosen, ...], fits_alone: dict[str, bool]) -> tuple[str, ...]:
    """The required elements that cannot be held: those that do not fit even alone, else every
    required element beyond the main subject (they do not fit TOGETHER with it)."""
    alone = [c for c in required if not fits_alone.get(c.series.track.track_id, True)]
    chosen = alone or [c for c in required if c.importance.value != "primary"]
    return tuple(sorted(name_of(c) for c in chosen))


def name_of(c: Chosen) -> str:
    """A name a person can tell apart: the label and the track it came from."""
    return f"{c.series.track.label or c.series.track.kind} ({c.series.track.track_id})"
