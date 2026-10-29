"""Coverage gaps: places that need footage the library does not have.

A gap is reported only after the search was real (the pool, the shortlist and the assessments are
counted in ``Searched``), and says WHY nothing qualified: nothing related, related footage that
does not prove what the passage claims, or good footage refused for quality. It never turns a
missing keyword into a gap: an optional illustration with nothing suitable is simply left alone
(unless the profile asks for it), and a passage that can live without footage keeps its picture.
"""

from collections.abc import Sequence

from media_house.core.domain import Rational
from media_house.modules.broll_intelligence.domain.facts import PoolFact, TargetFact
from media_house.modules.broll_intelligence.domain.model import (
    Candidate,
    Considered,
    CoverageGap,
    EvidenceRef,
    Searched,
)
from media_house.modules.broll_intelligence.domain.need import NeedDecision
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.values import (
    GapKind,
    NeedKind,
    Priority,
    Purpose,
    Support,
)

_KIND: dict[Purpose, GapKind] = {
    Purpose.PRODUCT_CLOSEUP: GapKind.PRODUCT_CLOSEUP,
    Purpose.DEMONSTRATION: GapKind.ACTION_DEMONSTRATION,
    Purpose.ESTABLISHING: GapKind.ESTABLISHING_SHOT,
    Purpose.SCREEN_CONTENT: GapKind.SCREEN_RECORDING,
    Purpose.COMPARISON: GapKind.COMPARISON_VISUAL,
    Purpose.EVIDENCE: GapKind.SUPPORTING_EVIDENCE,
    Purpose.ILLUSTRATION: GapKind.CONTEXT_SCENE,
    Purpose.VARIETY: GapKind.CONTEXT_SCENE,
    Purpose.JUMP_CUT_COVER: GapKind.CONTEXT_SCENE,
}
_MAX_CONSIDERED = 5


def wants_gap(target: TargetFact, need: NeedDecision, profile: BrollProfile) -> bool:
    if need.kind in (NeedKind.REQUIRED_EVIDENCE, NeedKind.DEMONSTRATION):
        return True
    return target.necessity == "essential" or profile.report_optional_gaps


def build_gap(
    target: TargetFact,
    need: NeedDecision,
    recommendation_id: str,
    start: Rational,
    end: Rational,
    pool: PoolFact,
    assessed: int,
    kept: Sequence[Candidate],
    refused: Sequence[Candidate],
    visual_used: bool,
) -> CoverageGap:
    illustrative = [c for c in kept if c.support is Support.ILLUSTRATIVE_ONLY]
    considered = tuple(
        Considered(asset_id=c.asset_id, shot_id=c.shot_id, role=c.role, reason=_reason(c))
        for c in [*illustrative, *refused][:_MAX_CONSIDERED]
    )
    concept = target.concept or target.text[:60]
    if illustrative:
        reason = (
            f"{len(illustrative)} shot(s) relate to '{concept}', but none is {need.requires}; "
            "they are illustrative only"
        )
    elif refused:
        reason = f"footage related to '{concept}' exists but was refused: {_reason(refused[0])}"
    elif assessed == 0:
        reason = (
            f"no footage in the {pool.assets} searched recordings ({pool.shots} shots) relates "
            f"to '{concept}'"
        )
    else:
        reason = f"nothing among {assessed} assessed shots qualified for '{concept}'"
    return CoverageGap(
        gap_id=f"gap:{target.target_id}",
        target_id=target.target_id,
        recommendation_id=recommendation_id,
        asset_id=target.asset_id,
        kind=_KIND[need.purpose],
        purpose=need.purpose,
        need=need.kind,
        concept=concept,
        start=start,
        end=end,
        reason=reason,
        searched=Searched(
            assets=pool.assets,
            shots=pool.shots,
            assessed=assessed,
            refused=len(refused),
            illustrative=len(illustrative),
        ),
        considered=considered,
        importance=need.priority,
        urgency=(
            Priority.HIGH
            if target.necessity == "essential"
            else Priority.MEDIUM
            if need.kind in (NeedKind.REQUIRED_EVIDENCE, NeedKind.DEMONSTRATION)
            else Priority.LOW
        ),
        repurposable=bool(illustrative),
        repurpose_candidates=tuple(c.candidate_id for c in illustrative[:3]),
        resolution=_resolution(target, need),
        fallback=_fallback(need, bool(illustrative)),
        evidence=(
            EvidenceRef(kind="story_visual", ref=target.story_visual_id)
            if target.story_visual_id
            else EvidenceRef(kind="content_opportunity", ref=target.target_id),
        ),
        confidence=_confidence(pool, visual_used, assessed),
        section_id=target.section_id,
        beat_id=target.beat_id,
        story_visual_id=target.story_visual_id,
    )


def _reason(candidate: Candidate) -> str:
    notes = list(candidate.warnings) or list(candidate.reason_codes[:2])
    return "; ".join(notes[:2]) or candidate.role.value.replace("_", " ")


def _resolution(target: TargetFact, need: NeedDecision) -> str:
    what = target.concept or "the passage"
    if target.names:
        names = ", ".join(n.text for n in target.names)
        return f"film or import footage that clearly shows {names} (label, screen or packaging)"
    match need.purpose:
        case Purpose.DEMONSTRATION:
            return f"film '{what}' being done, from start to result"
        case Purpose.ESTABLISHING:
            return f"film a wide shot of the place '{what}' refers to"
        case Purpose.PRODUCT_CLOSEUP:
            return f"film a close shot of '{what}'"
        case Purpose.COMPARISON:
            return f"film or capture both sides of the comparison '{what}'"
        case Purpose.SCREEN_CONTENT:
            return f"record the screen that shows '{what}'"
        case _:
            return f"add footage or a screen capture that shows '{what}'"


def _fallback(need: NeedDecision, repurposable: bool) -> str:
    base = "keep the main picture"
    if need.kind is NeedKind.REQUIRED_EVIDENCE:
        base += (
            "; do not cover the claim with unrelated footage "
            "(a graphic or on-screen text can state it)"
        )
    if repurposable:
        base += (
            "; a person may use an illustrative alternative, "
            "knowing it does not prove the statement"
        )
    return base


def _confidence(pool: PoolFact, visual_used: bool, assessed: int) -> float:
    """How sure "nothing suitable" is: higher when the pictures themselves were searched, the
    pool had analysed footage and the shortlist was not empty for lack of observations."""
    coverage = pool.with_shots / pool.assets if pool.assets else 0.0
    value = 0.4 + (0.3 if visual_used else 0.0) + 0.2 * coverage + (0.1 if assessed else 0.0)
    return round(min(1.0, value), 3)
