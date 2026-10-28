"""Requests for later stages: transitions, cues, reframes. The editor states intent and timing.

Nothing here designs or implements an effect. A request says what should happen, where on the
timeline and why; the stage that owns the effect decides how. The number of generated requests is
bounded per minute of edit (the strongest are kept), so a plan never asks for an effect on every
cut. Requests the story itself makes (a graphic for an explanation) are not capped.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import Piece, Timeline
from media_house.modules.editing_intelligence.domain.model import (
    Decision,
    EffectRequest,
    EvidenceRef,
)
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.timing import to_rational
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    EffectIntent,
    EffectKind,
    EvidenceKind,
    Executability,
    Origin,
    SignalName,
)

_REVEAL = frozenset({"payoff", "turning_point"})
_EMPHASIS_FLOOR = 0.8


def unit_starts(pieces: Sequence[Piece], timeline: Timeline) -> dict[str, Fraction]:
    """Where each placed sentence starts on the timeline, by unit key."""
    out: dict[str, Fraction] = {}
    for piece, start in zip(pieces, timeline.starts, strict=True):
        for unit in piece.units:
            if unit.extent is not None:
                out[unit.key] = start + max(Fraction(0), unit.extent.start - piece.start)
    return out


def plan_effects(
    units: Sequence[EditUnit],
    decisions: Mapping[str, Decision],
    starts: Mapping[str, Fraction],
    total: Fraction,
    profile: EditingProfile,
    effect_id: Callable[[], str],
) -> tuple[list[EffectRequest], list[Decision]]:
    """The strongest requests within the profile's budget, plus a decision for each emphasis."""
    candidates: list[EffectRequest] = []
    seen_sections: set[str] = set()
    for unit in units:
        decision = decisions[unit.decision_id]
        if decision.action is Action.CUT or unit.key not in starts:
            continue
        at = to_rational(starts[unit.key])
        first_of_section = unit.section_id not in seen_sections
        seen_sections.add(unit.section_id)
        if profile.section_transitions and first_of_section and len(seen_sections) > 1:
            candidates.append(
                _request(
                    effect_id(),
                    EffectKind.TRANSITION,
                    EffectIntent.SECTION_CHANGE,
                    at,
                    unit,
                    0.5,
                    "the story moves to a new section",
                )
            )
        if profile.reveal_cue and unit.function in _REVEAL:
            candidates.append(
                _request(
                    effect_id(),
                    EffectKind.MUSIC_CUE,
                    EffectIntent.REVEAL,
                    at,
                    unit,
                    0.8,
                    f"a {unit.function} passage begins",
                )
            )
        engagement = decision.value(SignalName.ENGAGEMENT)
        if profile.emphasis_reframe and engagement is not None and engagement >= _EMPHASIS_FLOOR:
            candidates.append(
                _request(
                    effect_id(),
                    EffectKind.REFRAME,
                    EffectIntent.EMPHASIS,
                    at,
                    unit,
                    engagement,
                    f"strongly emphasised delivery (engagement {engagement:.2f})",
                )
            )
    budget = max(1, math.ceil(profile.effects_per_minute * float(total) / 60)) if candidates else 0
    chosen = sorted(candidates, key=lambda r: (-r.priority, r.at.value))[:budget]
    requests = sorted(chosen, key=lambda r: (r.at.value, r.request_id))
    emphasised = [
        _emphasis(decisions[r.decision_id], r)
        for r in requests
        if r.intent is EffectIntent.EMPHASIS and r.decision_id is not None
    ]
    return requests, emphasised


def _request(
    request_id: str,
    kind: EffectKind,
    intent: EffectIntent,
    at: Rational,
    unit: EditUnit,
    priority: float,
    reason: str,
) -> EffectRequest:
    return EffectRequest(
        request_id=request_id,
        kind=kind,
        intent=intent,
        at=at,
        duration=None,
        placement_id=None,
        section_id=unit.section_id,
        priority=round(priority, 3),
        reason=reason,
        decision_id=unit.decision_id,
    )


def _emphasis(source: Decision, request: EffectRequest) -> Decision:
    return Decision(
        decision_id=f"emp:{source.decision_id}",
        action=Action.EMPHASIZE,
        origin=Origin.AUTOMATIC,
        asset_id=source.asset_id,
        section_id=source.section_id,
        beat_id=source.beat_id,
        subject=source.subject,
        unit_ids=source.unit_ids,
        signals=(),
        executability=Executability.NEEDS_REVIEW,
        rationale=(request.reason,),
        evidence=(EvidenceRef(kind=EvidenceKind.CONTENT_UNIT, ref=source.unit_ids[0]),),
    )
