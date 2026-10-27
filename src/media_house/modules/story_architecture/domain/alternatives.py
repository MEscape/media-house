"""A few materially different structures, compared on the same criteria as the recommendation.

An alternative exists only when it differs from the recommended plan in order, opening or what it
includes, and only for a reason the material supports: the recorded order, another good opening,
or the essential sections alone when the rest is a large share of the time. The recommended plan
is never replaced or edited by an alternative; they sit beside it.
"""

from dataclasses import dataclass

from media_house.modules.story_architecture.domain.model import (
    AlternativePlan,
    Criterion,
    OpeningCandidate,
    OrderingConstraint,
    PlanDuration,
    Section,
)
from media_house.modules.story_architecture.domain.profile import FormatProfile, Thresholds
from media_house.modules.story_architecture.domain.sections import excerpt
from media_house.modules.story_architecture.domain.values import (
    ConstraintKind,
    OpeningStrategy,
    Priority,
    Strength,
)

_MAX_ALTERNATIVES = 3
_MAX_OPENING_VARIANTS = 2
#: The non-essential share of the running time above which a short version is worth offering.
_CORE_WORTHWHILE = 0.2
_ALL: tuple[Priority, ...] = (
    Priority.ESSENTIAL,
    Priority.SUPPORTING,
    Priority.OPTIONAL,
    Priority.REDUNDANT_CANDIDATE,
)


@dataclass(frozen=True, slots=True)
class _Variant:
    label: str
    changes: tuple[str, ...]
    order: tuple[str, ...]
    opening: OpeningCandidate | None
    includes: tuple[Priority, ...]
    emphasizes: tuple[str, ...]
    limitations: tuple[str, ...]


def build(
    *,
    sections: tuple[Section, ...],
    source_order: tuple[str, ...],
    recommended_order: tuple[str, ...],
    constraints: tuple[OrderingConstraint, ...],
    openings: tuple[OpeningCandidate, ...],
    recommended: OpeningCandidate | None,
    duration: PlanDuration,
    profile: FormatProfile,
    thresholds: Thresholds,
) -> tuple[AlternativePlan, ...]:
    by_id = {s.section_id: s for s in sections}
    natural = next((o for o in openings if o.strategy is OpeningStrategy.NATURAL_START), None)
    primary = (recommended_order, recommended.candidate_id if recommended else None, _ALL)
    variants: list[_Variant] = []

    if source_order != recommended_order or (recommended and recommended is not natural):
        changes = []
        if source_order != recommended_order:
            changes.append("keeps every section where it was recorded")
        if recommended and recommended is not natural:
            changes.append("opens as recorded instead of with the recommended opening")
        variants.append(
            _Variant(
                "As recorded",
                tuple(changes),
                source_order,
                natural,
                _ALL,
                ("the speaker's own order",),
                ("sections that depend on later context stay where they were recorded",),
            )
        )

    others = [o for o in openings if o is not recommended and o.score is not None]
    seen_strategies: set[OpeningStrategy] = set()
    for candidate in others:
        if candidate.strategy in seen_strategies or len(seen_strategies) >= _MAX_OPENING_VARIANTS:
            continue
        seen_strategies.add(candidate.strategy)
        variants.append(
            _Variant(
                f"Open with the {candidate.strategy.value.replace('_', ' ')}",
                (f"opens with: {excerpt(candidate.excerpt, 90)}",),
                recommended_order,
                candidate,
                _ALL,
                (candidate.strategy.value,),
                tuple(f"opening risk: {r}" for r in candidate.risks),
            )
        )

    total = duration.total.seconds
    core = duration.core.seconds
    rest = [s for s in sections if s.priority is not Priority.ESSENTIAL]
    if rest and total and core is not None and (total - core) / total >= _CORE_WORTHWHILE:
        variants.append(
            _Variant(
                "Essential sections only",
                (f"leaves out {len(rest)} sections that are not essential",),
                recommended_order,
                recommended,
                (Priority.ESSENTIAL,),
                ("the main message and what it needs",),
                ("supporting and optional material is not represented",),
            )
        )

    out: list[AlternativePlan] = []
    signatures = {primary}
    for variant in variants:
        signature = (
            variant.order,
            variant.opening.candidate_id if variant.opening else None,
            variant.includes,
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        included = [s for s in variant.order if by_id[s].priority in variant.includes]
        omitted = tuple(s for s in variant.order if s not in included)
        seconds = _seconds(by_id, included)
        out.append(
            AlternativePlan(
                alternative_id=f"alt_{len(out) + 1}",
                label=variant.label,
                changes=variant.changes,
                section_order=tuple(included),
                opening_candidate=variant.opening.candidate_id if variant.opening else None,
                includes=variant.includes,
                omits=omitted,
                emphasizes=variant.emphasizes,
                estimated_seconds=seconds,
                criteria=_criteria(
                    by_id, included, constraints, variant.opening, seconds, profile, thresholds
                ),
                limitations=variant.limitations,
            )
        )
        if len(out) == _MAX_ALTERNATIVES:
            break
    return tuple(out)


def _seconds(by_id: dict[str, Section], included: list[str]) -> float | None:
    known = [d for s in included if (d := by_id[s].duration.seconds) is not None]
    return round(sum(known), 3) if known else None


def _criteria(
    by_id: dict[str, Section],
    included: list[str],
    constraints: tuple[OrderingConstraint, ...],
    opening: OpeningCandidate | None,
    seconds: float | None,
    profile: FormatProfile,
    thresholds: Thresholds,
) -> tuple[Criterion, ...]:
    position = {s: i for i, s in enumerate(included)}
    hard = [
        c
        for c in constraints
        if c.kind is ConstraintKind.MUST_PRECEDE
        and c.strength is Strength.HARD
        and c.second in position
    ]
    broken = [c for c in hard if c.first not in position or position[c.first] > position[c.second]]
    clarity = 1.0 - len(broken) / len(hard) if hard else 1.0
    essential = [s for s in by_id.values() if s.priority is Priority.ESSENTIAL]
    completeness = (
        sum(1 for s in essential if s.section_id in position) / len(essential) if essential else 1.0
    )
    risks = len(opening.risks) if opening else 0
    integrity = max(0.0, 1.0 - 0.25 * (len(broken) + risks))
    fit: float | None = None
    reason = "the format has no target length"
    if profile.target_seconds is not None and seconds is not None:
        low, high = profile.target_seconds
        low, high = (
            low * (1 - thresholds.duration_tolerance),
            high * (1 + thresholds.duration_tolerance),
        )
        gap = (
            0.0 if low <= seconds <= high else (low - seconds if seconds < low else seconds - high)
        )
        fit = max(0.0, 1.0 - gap / max(high, 1.0))
        reason = ""
    elif profile.target_seconds is not None:
        reason = "the length is unknown"
    return (
        Criterion(name="clarity", value=round(clarity, 4)),
        Criterion(name="completeness", value=round(completeness, 4)),
        Criterion(
            name="engagement",
            value=opening.score if opening else None,
            reason="" if opening and opening.score is not None else "no scored opening",
        ),
        Criterion(name="factual_integrity", value=round(integrity, 4)),
        Criterion(name="format_fit", value=None if fit is None else round(fit, 4), reason=reason),
    )
