"""Which part of the shot to show: a bounded source range with its provenance.

A recommendation never offers a whole long recording when a few seconds are useful. The range is
chosen from what the shot's own observations locate:

* the usable part of the shot (after the settling head and before the tail the analysis found);
* the moments that carry the evidence: action events for a demonstration, text appearing for
  something read from the picture; a cluster of them is shown with a lead-in and a hold on the
  result;
* otherwise the start of the usable part, for as long as the insert should last.

Honesty about precision: a range equal to the whole shot has frame-exact ends (shot boundaries);
any sub-shot range is ``ESTIMATED``. A final in/out is the editing stage's decision. Several
separate clusters of evidence in one shot become several ranges (at most ``MAX_RANGES``).
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.broll_intelligence.domain.facts import EventFact, ShotFact, TargetFact
from media_house.modules.broll_intelligence.domain.match import Assessment
from media_house.modules.broll_intelligence.domain.need import NeedDecision
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.values import (
    Association,
    NeedKind,
    Precision,
    Purpose,
)

MAX_RANGES = 2
#: Seconds of lead-in before the first evidence moment and of hold after the last one.
LEAD_IN = Fraction(1, 2)
HOLD = Fraction(1)
_ACTION_EVENTS = frozenset(
    {
        "gesture",
        "motion_peak",
        "scrolling",
        "screen_content_change",
        "track_entered",
        "track_exited",
    }
)
_TEXT_EVENTS = frozenset({"text_appeared", "text_disappeared"})
_TOLERANCE = Fraction(1, 10)


@dataclass(frozen=True, slots=True)
class Chosen:
    start: Fraction
    end: Fraction
    precision: Precision
    #: Share of the shot's evidence moments inside the range; ``None`` = none located or needed.
    temporal: float | None
    temporal_reason: str
    #: The usable length offered, and the length the use needs.
    usable: Fraction
    wanted: Fraction
    moments: int


def frac(seconds: float) -> Fraction:
    return Fraction(seconds).limit_denominator(1000)


def wanted_seconds(target: TargetFact, profile: BrollProfile) -> Fraction:
    """How long the insert should last: the passage's length within the profile's bounds."""
    length = target.length if target.length > 0 else profile.max_insert_seconds
    return frac(max(profile.min_insert_seconds, min(profile.max_insert_seconds, length)))


def choose_ranges(
    shot: ShotFact,
    target: TargetFact,
    need: NeedDecision,
    assessment: Assessment,
    profile: BrollProfile,
) -> tuple[Chosen, ...] | str:
    """Up to ``MAX_RANGES`` ranges, best first, or the reason none is long enough."""
    start, end = shot.start, shot.end
    if shot.asset_duration is not None:
        end = min(end, shot.asset_duration)
    head = frac(shot.head_seconds or 0.0)
    tail = frac(shot.tail_seconds or 0.0)
    low, high = start + head, end - tail
    minimum = frac(profile.min_insert_seconds)
    if high - low < minimum:
        low, high = start, end  # the settling parts are better than no footage at all
    if high - low + _TOLERANCE < minimum:
        return (
            f"only {float(high - low):.1f} s of the shot is usable, "
            f"less than {float(minimum):.1f} s"
        )
    wanted = wanted_seconds(target, profile)
    moments = _moments(shot, need, assessment, low, high)
    if not moments:
        return (_plain(shot, low, high, wanted, minimum, need, assessment),)
    clusters = _clusters(moments, wanted)
    out: list[Chosen] = []
    ranked = sorted(clusters, key=lambda c: (-_weight(c), c[0].start))
    for cluster in ranked[:MAX_RANGES]:
        out.append(_around(cluster, len(moments), low, high, wanted, minimum))
    return tuple(out)


def _moments(
    shot: ShotFact, need: NeedDecision, assessment: Assessment, low: Fraction, high: Fraction
) -> list[EventFact]:
    kinds: set[str] = set()
    if need.kind is NeedKind.DEMONSTRATION or need.purpose is Purpose.DEMONSTRATION:
        kinds |= _ACTION_EVENTS
    if assessment.association is Association.OBSERVED_TEXT:
        kinds |= _TEXT_EVENTS
    return sorted(
        (e for e in shot.events if e.kind in kinds and low <= e.start < high),
        key=lambda e: e.start,
    )


def _clusters(moments: list[EventFact], wanted: Fraction) -> list[list[EventFact]]:
    """Moments closer together than the insert length belong to one stretch of action."""
    clusters: list[list[EventFact]] = [[moments[0]]]
    for event in moments[1:]:
        previous = clusters[-1][-1]
        if event.start - (previous.end or previous.start) <= wanted:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    return clusters


def _weight(cluster: list[EventFact]) -> float:
    return sum(e.strength if e.strength is not None else 0.5 for e in cluster)


def _around(
    cluster: list[EventFact],
    total_moments: int,
    low: Fraction,
    high: Fraction,
    wanted: Fraction,
    minimum: Fraction,
) -> Chosen:
    first = cluster[0].start
    last = max((e.end or e.start) for e in cluster)
    begin, finish = first - LEAD_IN, last + HOLD
    if finish - begin > wanted:  # more action than the insert holds: centre on the strongest
        peak = max(cluster, key=lambda e: (e.strength or 0.5, -e.start)).start
        begin, finish = peak - wanted * Fraction(3, 10), peak + wanted * Fraction(7, 10)
    if finish - begin < minimum:
        middle = (begin + finish) / 2
        begin, finish = middle - minimum / 2, middle + minimum / 2
    if begin < low:
        begin, finish = low, min(high, low + max(finish - begin, minimum))
    if finish > high:
        begin, finish = max(low, high - max(finish - begin, minimum)), high
    covered = sum(1 for e in cluster if begin <= e.start < finish)
    return Chosen(
        start=begin,
        end=finish,
        precision=Precision.ESTIMATED,
        temporal=round(covered / total_moments, 3) if total_moments else None,
        temporal_reason=(
            f"{covered} of {total_moments} located moments (movement, text appearing) lie in "
            "the range"
        ),
        usable=high - low,
        wanted=wanted,
        moments=total_moments,
    )


def _plain(
    shot: ShotFact,
    low: Fraction,
    high: Fraction,
    wanted: Fraction,
    minimum: Fraction,
    need: NeedDecision,
    assessment: Assessment,
) -> Chosen:
    length = min(wanted, high - low)
    finish = low + max(length, minimum if high - low >= minimum else high - low)
    needs_moment = need.kind is NeedKind.DEMONSTRATION or need.purpose is Purpose.DEMONSTRATION
    whole = low == shot.start and finish >= shot.end - _TOLERANCE
    return Chosen(
        start=low,
        end=min(finish, high),
        precision=Precision.SHOT_BOUNDARY if whole else Precision.ESTIMATED,
        temporal=None if needs_moment else 0.9,
        temporal_reason=(
            "the action was not located in time inside the shot, so the start of the usable part "
            "is offered"
            if needs_moment
            else "no particular moment is needed; the start of the usable part is offered"
        ),
        usable=high - low,
        wanted=wanted,
        moments=0,
    )
