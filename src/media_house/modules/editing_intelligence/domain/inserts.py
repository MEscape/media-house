"""Cover inserts of a short: each item gets a glimpse of its own footage, the hook its gag.

The long video's cover is tied to sentences: a wish names the spoken range it was asked over, a
B-roll place the passage it was found for. A short keeps other sentences of the same section, so
that cover would find no speech and vanish. Here the footage belongs to its SECTION instead: a
wish over any sentence of the section, or a B-roll candidate for a place of the section, may play
over what the short keeps of that section (muted, the voice carries on underneath).

* the hook: its footage opens the short (the gag before the first line);
* an item: one insert of 1.5-2.5 s, starting 1.5 s into the item (the speaker is seen naming it),
  covering at most 40 % of the item (graphics windows are not known here), at least 4 s after
  the previous insert, never over cover already placed;
* candidates: the brief's wishes first, then B-roll shots that pass the coverage checks
  (specific associations before weak ones, which stay suggestions); no footage is used twice.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import Piece, Timeline
from media_house.modules.editing_intelligence.domain.coverage import judge_shot
from media_house.modules.editing_intelligence.domain.facts import (
    AssetEvidence,
    BrollFacts,
    StoryFacts,
)
from media_house.modules.editing_intelligence.domain.model import (
    Decision,
    EvidenceRef,
    Placement,
)
from media_house.modules.editing_intelligence.domain.profile import CoverWish, EditingProfile
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.timing import (
    exact,
    frame_round,
    to_fraction,
    to_rational,
)
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    AudioBehavior,
    EvidenceKind,
    Executability,
    Origin,
    PlacementRole,
    Precision,
    TrackKind,
)

_DELAY = Fraction(3, 2)
_SHORTEST = Fraction(3, 2)
_LONGEST = Fraction(5, 2)
_SHARE = Fraction(2, 5)
_SPACING = Fraction(4)


@dataclass(frozen=True, slots=True)
class _Footage:
    asset_id: str
    start: Fraction
    #: How much of it may play.
    room: Fraction
    executability: Executability
    why: str
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True)
class Inserts:
    placements: tuple[Placement, ...]
    decisions: tuple[Decision, ...]


def plan_inserts(
    hook: str | None,
    items: Sequence[str],
    units: Sequence[EditUnit],
    pieces: Sequence[Piece],
    timeline: Timeline,
    story: StoryFacts,
    wishes: Sequence[CoverWish],
    broll: BrollFacts | None,
    evidence: Mapping[str, AssetEvidence],
    taken: Sequence[Placement],
    profile: EditingProfile,
    rate: Rational,
) -> Inserts:
    """Inserts for the ``hook`` section and every section of ``items`` (story section ids)."""
    spans = _spans(pieces, timeline)
    busy = [
        (
            to_fraction(p.timeline_start),
            to_fraction(p.timeline_start) + to_fraction(p.timeline_duration),
        )
        for p in taken
        if p.track is TrackKind.VIDEO and p.layer > 0
    ]
    used = {(p.source.asset_id, to_fraction(p.source.start)) for p in taken if p.layer > 0}
    placements: list[Placement] = []
    decisions: list[Decision] = []
    for section in ([hook] if hook else []) + [s for s in items if s != hook]:
        if section not in spans:
            continue
        lo, hi = spans[section]
        is_hook = section == hook
        start = lo if is_hook else lo + _DELAY
        limit = hi - lo if is_hook else min(_LONGEST, _SHARE * (hi - lo))
        covered = sum((min(e, hi) - max(s, lo) for s, e in busy if s < hi and e > lo), Fraction(0))
        if is_hook and covered > 0:
            continue  # the hook already opens on its footage
        start = max([start, *(s + _SPACING for s, _ in busy if s <= start)])
        if not is_hook and any(0 < s - start < _SPACING for s, _ in busy):
            continue  # a cover follows too soon: one insert per 4 s
        for footage in _footage(section, units, story, wishes, broll, evidence, profile):
            if (footage.asset_id, footage.start) in used:
                continue
            length = min(footage.room, limit - (0 if is_hook else covered), hi - start)
            if not is_hook:
                length = min(length, _LONGEST)
            if length < _SHORTEST or any(s < start + length and start < e for s, e in busy):
                continue
            placements.append(_placement(section, footage, start, length, evidence, rate))
            decisions.append(_decision(section, footage, placements[-1]))
            busy.append((start, start + length))
            used.add((footage.asset_id, footage.start))
            break
    return Inserts(tuple(placements), tuple(decisions))


def _spans(pieces: Sequence[Piece], timeline: Timeline) -> dict[str, tuple[Fraction, Fraction]]:
    """Where each section of the short's own (main) pieces starts and ends on the timeline."""
    out: dict[str, tuple[Fraction, Fraction]] = {}
    for piece, at in zip(pieces, timeline.starts, strict=True):
        if piece.role is not PlacementRole.MAIN or not piece.units:
            continue
        section = piece.units[0].section_id
        lo, hi = out.get(section, (at, at))
        out[section] = (min(lo, at), max(hi, at + piece.duration))
    return out


def _footage(
    section: str,
    units: Sequence[EditUnit],
    story: StoryFacts,
    wishes: Sequence[CoverWish],
    broll: BrollFacts | None,
    evidence: Mapping[str, AssetEvidence],
    profile: EditingProfile,
) -> list[_Footage]:
    """The section's own footage, best first: wishes over any of its sentences, then B-roll."""
    spoken = [
        (u.asset_id, u.extent.start, u.extent.end)
        for u in units
        if u.section_id == section and u.extent is not None
    ]
    out: list[_Footage] = []
    for n, wish in enumerate(wishes):
        target = evidence.get(wish.footage_asset_id)
        lo, hi = exact(wish.over_start), exact(wish.over_end)
        if target is None or not any(
            a == wish.over_asset_id and s < hi and lo < e for a, s, e in spoken
        ):
            continue
        start = exact(wish.footage_start)
        end = exact(target.source.duration) if target.source.duration is not None else None
        room = exact(wish.max_seconds) if end is None else min(exact(wish.max_seconds), end - start)
        out.append(
            _Footage(
                wish.footage_asset_id,
                start,
                room,
                Executability.EXECUTABLE,
                f"asked for over this part: {wish.note or wish.footage_asset_id}",
                EvidenceRef(kind=EvidenceKind.PROFILE_RULE, ref=f"cover_wish:{n:03d}"),
            )
        )
    visuals = {v.visual_id: v for v in story.visuals if v.section_id == section}
    found: list[tuple[bool, int, _Footage]] = []
    for place in broll.places if broll is not None else ():
        visual = visuals.get(place.visual_id)
        if visual is None:
            continue
        passage = " ".join(u.unit.text for u in units if u.unit.unit_id in visual.source.unit_ids)
        for choice in place.choices:
            verdict = judge_shot(
                choice,
                evidence,
                exact(place.start),
                exact(place.end),
                profile,
                passage,
                visual.source.asset_id,
            )
            if isinstance(verdict, str):
                continue
            begin = to_fraction(verdict.range.start)
            found.append(
                (
                    verdict.executability is not Executability.EXECUTABLE,
                    choice.rank,
                    _Footage(
                        choice.asset_id,
                        begin,
                        to_fraction(verdict.range.end) - begin,
                        verdict.executability,
                        f"B-roll for this part ({choice.grade})",
                        EvidenceRef(kind=EvidenceKind.STORY_VISUAL, ref=place.visual_id),
                    ),
                )
            )
    return out + [f for _, _, f in sorted(found, key=lambda x: (x[0], x[1]))]


def _placement(
    section: str,
    footage: _Footage,
    start: Fraction,
    length: Fraction,
    evidence: Mapping[str, AssetEvidence],
    rate: Rational,
) -> Placement:
    first = frame_round(start, rate)
    return Placement(
        placement_id=f"cover:ins:{section}",
        track=TrackKind.VIDEO,
        layer=1,
        role=PlacementRole.COVER,
        source=make_range(
            evidence[footage.asset_id].source,
            footage.start,
            footage.start + length,
            Precision.SENTENCE_SPAN,
            Precision.SENTENCE_SPAN,
        ),
        timeline_start=to_rational(start),
        timeline_duration=to_rational(length),
        start_frame=first,
        duration_frames=max(1, frame_round(start + length, rate) - first),
        section_id=section,
        beat_id=None,
        decision_ids=(f"ins:{section}",),
        audio=AudioBehavior.MUTED,
        fade_in=Rational(0, 1),
        fade_out=Rational(0, 1),
    )


def _decision(section: str, footage: _Footage, placement: Placement) -> Decision:
    return Decision(
        decision_id=f"ins:{section}",
        action=Action.COVER,
        origin=Origin.AUTOMATIC,
        asset_id=footage.asset_id,
        section_id=section,
        beat_id=None,
        subject=placement.source,
        unit_ids=(),
        signals=(),
        executability=footage.executability,
        rationale=(f"a short shows the part's own footage: {footage.why}",),
        evidence=(footage.evidence,),
    )
