"""Framing recommendations at the clips the edit places.

The reframing analysis recommends a crop per SHOT of a recording for one target format. The edit
places RANGES of recordings, so each picture clip takes a reference to every recommendation its
source range overlaps (with the part it covers), and the editor judges what that means for the
edit:

* a clip whose range is (partly) UNREFRAMEABLE for the target, or whose crop is low quality, is an
  issue to review, with the best alternative the analysis named (never applied here);
* a clip whose recommendation is uncertain (missing evidence) is flagged as unresolved;
* every other clip simply carries the references, so a timeline stage can fetch the crop and the
  keyframes (they stay in the reframing analysis, the one place they are stored).

Nothing is cropped, moved or decided here: the editor keeps the clip it chose and says what the
framing recommendations make of it.
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from fractions import Fraction

from media_house.modules.editing_intelligence.domain.facts import FramingFacts, FramingSpan
from media_house.modules.editing_intelligence.domain.model import FramingRef, Issue, Placement
from media_house.modules.editing_intelligence.domain.timing import to_fraction, to_rational
from media_house.modules.editing_intelligence.domain.values import (
    IssueKind,
    Severity,
    TrackKind,
)

_UNFRAMEABLE = frozenset({"unreframeable", "fill_treatment"})


def attach_framing(
    placements: Sequence[Placement],
    framing: FramingFacts | None,
    issue_id: Callable[[], str],
) -> tuple[list[Placement], list[Issue]]:
    """Give every picture clip the framing recommendations of its source range, and say which
    clips the recommendations cannot serve."""
    if framing is None:
        return list(placements), []
    out: list[Placement] = []
    issues: list[Issue] = []
    for p in placements:
        if p.track is not TrackKind.VIDEO:
            out.append(p)
            continue
        lo, hi = to_fraction(p.source.start), to_fraction(p.source.end)
        spans = framing.covering(p.source.asset_id, lo, hi)
        if not spans:
            out.append(p)
            continue
        refs = tuple(_ref(framing, s, lo, hi) for s in spans)
        out.append(replace(p, framing=refs))
        issues += _judge(p, spans, framing.target, issue_id)
    return out, issues


def _ref(framing: FramingFacts, span: FramingSpan, lo: Fraction, hi: Fraction) -> FramingRef:
    return FramingRef(
        recommendation_id=span.recommendation_id,
        analysis_id=framing.analysis_id,
        target=framing.target,
        strategy=span.strategy,
        feasibility=span.feasibility,
        executable=span.executable,
        start=to_rational(max(span.start, lo)),
        end=to_rational(min(span.end, hi)),
    )


def _judge(
    placement: Placement, spans: list[FramingSpan], target: str, issue_id: Callable[[], str]
) -> list[Issue]:
    out: list[Issue] = []
    for s in spans:
        if s.strategy in _UNFRAMEABLE or s.feasibility == "impossible":
            severity, what = Severity.WARNING, f"no crop of the {target} shape serves it"
        elif s.feasibility == "uncertain":
            severity, what = (
                Severity.UNRESOLVED,
                "its framing could not be judged (missing evidence)",
            )
        elif s.feasibility == "low_quality":
            severity, what = Severity.WARNING, f"the {target} crop would be too soft"
        else:
            continue
        extra = f"; {s.detail}" if s.detail else ""
        out.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.FRAMING,
                severity=severity,
                detail=f"{placement.placement_id}: {what} ({s.recommendation_id}){extra}",
                subject=placement.placement_id,
            )
        )
    return out
