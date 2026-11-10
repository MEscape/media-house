"""Cover footage someone asked for: placed where the named speech ends up in the edit.

A wish names a spoken range of a recording and the footage to show over it. The range is found
in the assembled pieces (it may be split by trims, and parts of it may be cut), the footage plays
on continuously over each part, muted, on the cover layer: the narration carries on. Nothing is
judged and nothing is invented: a range the edit no longer contains places nothing (reported),
and footage never plays past its end or over cover that is already placed.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import Piece, Timeline
from media_house.modules.editing_intelligence.domain.facts import AssetEvidence
from media_house.modules.editing_intelligence.domain.model import (
    Decision,
    EvidenceRef,
    Issue,
    Placement,
)
from media_house.modules.editing_intelligence.domain.profile import CoverWish
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.timing import (
    exact,
    frame_round,
    to_fraction,
    to_rational,
)
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    AudioBehavior,
    EvidenceKind,
    Executability,
    IssueKind,
    Origin,
    PlacementRole,
    Precision,
    Severity,
    TrackKind,
)

#: Shorter cover than this flickers; it is left out.
_MIN_SECONDS = Fraction(1, 2)


@dataclass(slots=True)
class WishResult:
    placements: list[Placement] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def place_wishes(
    wishes: Sequence[CoverWish],
    pieces: Sequence[Piece],
    timeline: Timeline,
    evidence: Mapping[str, AssetEvidence],
    taken: Sequence[Placement],
    rate: Rational,
    issue_id: Callable[[], str],
) -> WishResult:
    out = WishResult()
    busy = [
        (
            to_fraction(p.timeline_start),
            to_fraction(p.timeline_start) + to_fraction(p.timeline_duration),
        )
        for p in taken
        if p.track is TrackKind.VIDEO and p.layer > 0
    ]
    for n, wish in enumerate(wishes):
        footage = evidence.get(wish.footage_asset_id)
        wish_id = f"wish:{n:03d}"
        if footage is None:
            out.issues.append(_issue(issue_id(), wish_id, f"no facts for {wish.footage_asset_id}"))
            continue
        lo, hi = exact(wish.over_start), exact(wish.over_end)
        cursor = exact(wish.footage_start)
        budget = exact(wish.max_seconds)
        end_of_footage = (
            exact(footage.source.duration) if footage.source.duration is not None else None
        )
        placed: list[Placement] = []
        for i, piece in enumerate(pieces):
            if piece.asset_id != wish.over_asset_id or budget <= 0:
                continue
            a, b = max(piece.start, lo), min(piece.end, hi)
            start = timeline.starts[i] + (a - piece.start)
            # A short piece inside a running cover is covered too: showing the speaker for a
            # moment between two parts of the same footage is a flicker, not a cut.
            running = bool(placed) and _end(placed[-1]) == start
            shortest = Fraction(0) if running else _MIN_SECONDS
            if b - a <= shortest:
                continue
            length = min(b - a, budget)
            if end_of_footage is not None:
                length = min(length, end_of_footage - cursor)
            if length <= shortest or any(s < start + length and start < e for s, e in busy):
                continue
            first = frame_round(start, rate)
            frames = max(1, frame_round(start + length, rate) - first)
            placed.append(
                Placement(
                    placement_id=f"cover:{wish_id}:{len(placed)}",
                    track=TrackKind.VIDEO,
                    layer=1,
                    role=PlacementRole.COVER,
                    source=make_range(
                        footage.source,
                        cursor,
                        cursor + length,
                        Precision.SENTENCE_SPAN,
                        Precision.SENTENCE_SPAN,
                    ),
                    timeline_start=to_rational(start),
                    timeline_duration=to_rational(length),
                    start_frame=first,
                    duration_frames=frames,
                    section_id=piece.units[0].section_id if piece.units else None,
                    beat_id=None,
                    decision_ids=(wish_id,),
                    audio=AudioBehavior.MUTED,
                    fade_in=Rational(0, 1),
                    fade_out=Rational(0, 1),
                )
            )
            busy.append((start, start + length))
            cursor += length
            budget -= length
        if not placed:
            out.issues.append(
                _issue(issue_id(), wish_id, "the speech it was asked over is not in the edit")
            )
            continue
        out.placements.extend(placed)
        out.decisions.append(
            Decision(
                decision_id=wish_id,
                action=Action.COVER,
                origin=Origin.MANUAL,
                asset_id=wish.over_asset_id,
                section_id=placed[0].section_id,
                beat_id=None,
                subject=placed[0].source,
                unit_ids=(),
                signals=(),
                executability=Executability.EXECUTABLE,
                rationale=(f"asked for: {wish.note or wish.footage_asset_id}",),
                evidence=(
                    EvidenceRef(kind=EvidenceKind.PROFILE_RULE, ref="cover_wish", detail=wish.note),
                ),
            )
        )
    return out


def _end(p: Placement) -> Fraction:
    return to_fraction(p.timeline_start) + to_fraction(p.timeline_duration)


def _issue(issue_id: str, wish_id: str, detail: str) -> Issue:
    return Issue(
        issue_id=issue_id,
        kind=IssueKind.COVER_NEEDED,
        severity=Severity.WARNING,
        detail=f"cover asked for was not placed: {detail}",
        subject=wish_id,
    )
