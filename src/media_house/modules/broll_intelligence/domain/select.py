"""The evidence gate and the choice among candidates.

``support_for`` decides whether a candidate meets what the passage's need requires. This is the
guard that keeps a visually attractive or semantically close but unproven shot from standing in for
evidence: a need that requires evidence is satisfied only by footage whose role proves it (and, for
a passage naming a specific product, whose identity is confirmed). Everything else is
``ILLUSTRATIVE_ONLY`` (listed, labelled, never promoted) or ``REJECTED``.

Selection then removes repeats: a shot that duplicates one already chosen (same duplicate group,
overlapping range of one recording, near-identical picture) is dropped unless it is a distinct
angle a person confirmed; footage already used for another passage is allowed only at a lower
novelty, so a passage with no alternative can still use it, and it is marked ``reused``.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.broll_intelligence.domain.facts import RetrievalFacts, TargetFact
from media_house.modules.broll_intelligence.domain.match import Assessment
from media_house.modules.broll_intelligence.domain.need import NeedDecision
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.values import (
    ROLE_ORDER,
    Association,
    EvidenceRole,
    Identity,
    NeedKind,
    Support,
)

#: Pictures at least this alike are the same shot for the purpose of choosing among them.
NEAR_DUPLICATE = 0.95
#: Pictures at least this alike add little.
SIMILAR = 0.85

_DIRECT = frozenset({EvidenceRole.DIRECT_EVIDENCE, EvidenceRole.DIRECT_DEMONSTRATION})


def support_for(
    target: TargetFact,
    need: NeedDecision,
    a: Assessment,
    profile: BrollProfile,
    overall: float | None,
) -> tuple[Support, str]:
    """Whether the candidate satisfies the need, and the reason in words."""
    if a.refusals:
        return Support.REJECTED, a.refusals[0][1]
    if overall is not None and overall < profile.min_overall:
        return (
            Support.REJECTED,
            f"below the {profile.name} quality threshold "
            f"({overall:.2f} < {profile.min_overall:.2f})",
        )
    meets = _meets(need.kind, a, profile)
    if not meets:
        return Support.ILLUSTRATIVE_ONLY, _why_not(need, a)
    if target.negated and need.kind in (NeedKind.REQUIRED_EVIDENCE, NeedKind.DEMONSTRATION):
        return (
            Support.ILLUSTRATIVE_ONLY,
            "the passage negates or doubts what it names, so footage must not stand as proof",
        )
    return Support.SATISFIES, "meets what the passage requires"


def _meets(kind: NeedKind, a: Assessment, profile: BrollProfile) -> bool:
    role = a.role
    if role is EvidenceRole.CONTRADICTORY:
        return False
    named = a.identity in (
        Identity.CONFIRMED,
        Identity.UNVERIFIED,
        Identity.REFERENCE_UNRESOLVED,
    )
    strict_block = profile.strict_identity and named and a.identity is not Identity.CONFIRMED
    match kind:
        case NeedKind.REQUIRED_EVIDENCE:
            if role in _DIRECT:
                return not strict_block
            if role is EvidenceRole.SPECIFIC_SUBJECT and not named:
                return a.association in (Association.OBSERVED_TEXT, Association.ENTITY_LABEL)
            return (
                profile.illustrative_for_evidence
                and ROLE_ORDER[role] >= ROLE_ORDER[EvidenceRole.CONTEXTUAL_ILLUSTRATION]
            )
        case NeedKind.DEMONSTRATION:
            if role in _DIRECT:
                return not strict_block
            return (
                profile.illustrative_for_demonstration
                and ROLE_ORDER[role] >= ROLE_ORDER[EvidenceRole.CONTEXTUAL_ILLUSTRATION]
            )
        case NeedKind.ILLUSTRATIVE:
            return ROLE_ORDER[role] >= ROLE_ORDER[EvidenceRole.GENERIC_ILLUSTRATION]
        case NeedKind.VARIETY | NeedKind.JUMP_CUT_COVER:
            return ROLE_ORDER[role] >= ROLE_ORDER[EvidenceRole.GENERIC_ILLUSTRATION]
        case NeedKind.NO_VISUAL_NEEDED:
            return False


def _why_not(need: NeedDecision, a: Assessment) -> str:
    if a.identity is Identity.UNVERIFIED:
        return "nothing in the footage shows it is the specific one the passage names"
    if a.identity is Identity.REFERENCE_UNRESOLVED:
        return "the passage points at something unnamed, so the footage cannot be shown to be it"
    if need.kind is NeedKind.DEMONSTRATION:
        return "the footage relates to the passage, but the action itself is not shown"
    return f"{a.role.value.replace('_', ' ')}: related, but not the evidence the passage requires"


@dataclass(slots=True)
class Chosen:
    """What selection has taken so far for one passage."""

    keys: list[str] = field(default_factory=list)
    groups: set[str] = field(default_factory=set)
    scenes: set[tuple[str, str]] = field(default_factory=set)
    assets: set[str] = field(default_factory=set)
    ranges: list[tuple[str, Fraction, Fraction]] = field(default_factory=list)


def duplicate_of(
    key: str,
    asset_id: str,
    group: str | None,
    start: Fraction,
    end: Fraction,
    chosen: Chosen,
    retrieval: RetrievalFacts,
    angles: frozenset[str],
) -> str | None:
    """The key of a chosen shot this one repeats, else ``None``."""
    if group and group in chosen.groups:
        return next(iter(k for k in chosen.keys if k.split("#")[0] == asset_id), chosen.keys[0])
    for other_asset, a, b in chosen.ranges:
        if other_asset == asset_id and start < b and end > a:
            return next(k for k in chosen.keys if k.startswith(asset_id + "#"))
    for other in chosen.keys:
        similarity = retrieval.similar.get(frozenset((key, other)))
        if similarity is not None and similarity >= NEAR_DUPLICATE:
            if other.split("#")[0] in angles:
                continue  # a recording a person confirmed as another angle of the same scene
            return other
    return None


def novelty_of(
    key: str,
    asset_id: str,
    scene: str | None,
    chosen: Chosen,
    used_elsewhere: bool,
    retrieval: RetrievalFacts,
) -> tuple[float, str]:
    """How much this shot adds over what is already chosen (1 = something new)."""
    value, reasons = 1.0, []
    if used_elsewhere:
        value = min(value, 0.3)
        reasons.append("already used for another passage")
    if scene is not None and (asset_id, scene) in chosen.scenes:
        value = min(value, 0.6)
        reasons.append("same scene as a chosen shot")
    elif asset_id in chosen.assets:
        value = min(value, 0.8)
        reasons.append("same recording as a chosen shot")
    for other in chosen.keys:
        similarity = retrieval.similar.get(frozenset((key, other)))
        if similarity is not None and similarity >= SIMILAR:
            value = min(value, round(1.0 - similarity, 3) + 0.3)
            reasons.append(f"picture {similarity:.2f} alike a chosen shot")
    return value, "; ".join(reasons) if reasons else "adds something the chosen shots do not"
