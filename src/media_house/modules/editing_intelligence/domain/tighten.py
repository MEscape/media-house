"""Fitting the edit to a target length without giving up the story.

The ladder, in order, stopping as soon as the estimate is within the target:

1. Passages the story marks as repeats, then optional ones, that no hard constraint protects:
   the lowest ``keep`` (then the least engaging) go first. Each is a SUGGESTION: it removes
   content, so it needs review.
2. A supporting section that nothing depends on, as a STRUCTURAL PROPOSAL. It is only applied when
   the configuration allows structural alternatives.

Essential material is never removed to meet a target. When the ladder runs out and the edit is
still too long, the plan says by how much; whether that is an error depends on the target being a
hard constraint or a preference (see ``validation``).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction

from media_house.modules.editing_intelligence.domain.judge import (
    JudgeContext,
    close_dependencies,
    keep_every_essential_section,
    narrative_risk,
)
from media_house.modules.editing_intelligence.domain.model import Decision, StructuralProposal
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    Executability,
    Origin,
    SignalName,
    StructuralKind,
)

_ROUNDS = 6
_RANK = {"redundant_candidate": 0, "optional": 1}


@dataclass(slots=True)
class Tightening:
    cut_ids: list[str] = field(default_factory=list)
    proposals: list[StructuralProposal] = field(default_factory=list)


def tighten(
    units: Sequence[EditUnit],
    decisions: dict[str, Decision],
    jc: JudgeContext,
    measure: Callable[[], Fraction],
    high: Fraction,
    allow_structural: bool,
    proposal_id: Callable[[], str],
) -> Tightening:
    result = Tightening()
    target_text = f"{float(high):.0f} s"
    for _ in range(_ROUNDS):
        over = measure() - high
        if over <= 0:
            return result
        picked = _next_batch(units, decisions, jc, over)
        if not picked:
            break
        for unit in picked:
            _cut(unit, decisions, jc, f"cut to approach the target length of {target_text}")
            result.cut_ids.append(unit.decision_id)
        close_dependencies(units, decisions, jc)
        keep_every_essential_section(units, decisions)
    over = measure() - high
    if over > 0:
        _structural(units, decisions, jc, measure, high, allow_structural, proposal_id, result)
    return result


def _next_batch(
    units: Sequence[EditUnit],
    decisions: Mapping[str, Decision],
    jc: JudgeContext,
    over: Fraction,
) -> list[EditUnit]:
    candidates = [
        u
        for u in units
        if u.priority in _RANK
        and u.extent is not None
        and decisions[u.decision_id].action is not Action.CUT
        and decisions[u.decision_id].origin is Origin.AUTOMATIC
        and u.key not in jc.protected
    ]
    candidates.sort(
        key=lambda u: (
            0 if u.section_id in jc.pacing_tighten else 1,
            _RANK[u.priority],
            decisions[u.decision_id].value(SignalName.KEEP) or 0.0,
            decisions[u.decision_id].value(SignalName.ENGAGEMENT) or 0.5,
            -u.order,
        )
    )
    picked: list[EditUnit] = []
    saved = Fraction(0)
    for unit in candidates:
        if saved >= over:
            break
        if unit.extent is None:
            continue
        picked.append(unit)
        saved += unit.extent.seconds
    return picked


def _cut(unit: EditUnit, decisions: dict[str, Decision], jc: JudgeContext, why: str) -> None:
    current = decisions[unit.decision_id]
    keep = next((s for s in current.signals if s.name is SignalName.KEEP), None)
    signals = [s for s in current.signals if s.name is not SignalName.NARRATIVE_RISK]
    if keep is not None:
        signals.append(narrative_risk(unit, keep))
    decisions[unit.decision_id] = replace(
        current,
        action=Action.CUT,
        executability=Executability.NEEDS_REVIEW,
        signals=tuple(signals),
        rationale=(*current.rationale, why),
        warnings=(
            *current.warnings,
            "removal changes the content; meaning preservation is not established",
        ),
    )


def _structural(
    units: Sequence[EditUnit],
    decisions: dict[str, Decision],
    jc: JudgeContext,
    measure: Callable[[], Fraction],
    high: Fraction,
    allow_structural: bool,
    proposal_id: Callable[[], str],
    result: Tightening,
) -> None:
    required = {r for s in jc.story.sections for r in s.requires} | {
        c.requires_section_id for c in jc.story.contexts
    }
    sections = list(dict.fromkeys(u.section_id for u in units))
    options: list[tuple[float, str, list[EditUnit]]] = []
    for section_id in sections:
        members = [u for u in units if u.section_id == section_id]
        if members[0].priority != "supporting" or section_id in required:
            continue
        if any(m.key in jc.protected for m in members):
            continue
        if any(decisions[m.decision_id].origin is Origin.MANUAL for m in members):
            continue
        weight = sum(decisions[m.decision_id].value(SignalName.KEEP) or 0.0 for m in members)
        options.append((weight / len(members), section_id, members))
    for _, section_id, members in sorted(options, key=lambda o: o[0]):
        seconds = float(
            sum(
                (
                    m.extent.seconds
                    for m in members
                    if m.extent is not None and decisions[m.decision_id].action is not Action.CUT
                ),
                Fraction(0),
            )
        )
        applied = False
        if allow_structural and measure() > high:
            for member in members:
                if decisions[member.decision_id].action is not Action.CUT:
                    _cut(
                        member,
                        decisions,
                        jc,
                        "the supporting section is omitted to approach the target length",
                    )
            applied = True
        result.proposals.append(
            StructuralProposal(
                proposal_id=proposal_id(),
                kind=StructuralKind.OMIT_SECTION,
                section_ids=(section_id,),
                rationale=(
                    "a supporting section that no hard constraint or other section depends on; "
                    "omitting it is the next step toward the target length"
                ),
                effect_seconds=round(seconds, 2),
                applied=applied,
                status="applied"
                if applied
                else (
                    "proposed only: structural changes are not allowed by the configuration"
                    if not allow_structural
                    else "not needed: the target is already met"
                ),
            )
        )
