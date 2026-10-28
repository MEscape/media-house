"""Keep, cut or shorten: one decision per sentence, from separate signals.

Every sentence gets the same named signals (``keep``, ``cut``, ``engagement``, ``narrative_risk``)
computed from different evidence, never merged into one number:

* ``keep``: the story's priority of the section (and the segment's importance); 1.0 when a hard
  constraint protects the passage; very low for a sentence that is only filler.
* ``cut``: the strongest single reason to remove it (filler, greeting, a repeat that adds nothing,
  a recorded copy, content analysis' removal score, a false start). The reason is named.
* ``engagement``: delivery and hook evidence (emphasis, expressiveness, hook, highlight).
* ``narrative_risk``: how much could be lost if a CUT is wrong.

The rule is explicit: a passage is proposed for removal only when no hard constraint protects it,
``cut`` reaches the profile's threshold and beats ``keep`` by the profile's margin. A removal is
EXECUTABLE only when meaning is not at stake (a filler sentence, or a recorded copy of material
that stays elsewhere); every other removal is a suggestion that needs review. Dependencies are
then closed: a sentence a kept passage needs, a qualification of kept information, a segment a
kept segment depends on, is restored.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from media_house.modules.editing_intelligence.domain.facts import AssetEvidence, StoryFacts
from media_house.modules.editing_intelligence.domain.model import (
    Decision,
    EvidenceRef,
    ManualOverride,
    Signal,
    SourceRange,
)
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.speech import is_greeting, tokens
from media_house.modules.editing_intelligence.domain.units import EditUnit, Protection
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    EvidenceKind,
    Executability,
    Origin,
    SignalBasis,
    SignalName,
)

PRIORITY_VALUE: Mapping[str, float] = {
    "essential": 1.0,
    "supporting": 0.6,
    "optional": 0.3,
    "redundant_candidate": 0.15,
}
_FILLER_KEEP = 0.1


@dataclass(frozen=True, slots=True)
class JudgeContext:
    story: StoryFacts
    evidence: Mapping[str, AssetEvidence]
    profile: EditingProfile
    protected: Mapping[str, Sequence[Protection]]
    #: Sections that only repeat a recording whose copy is recorded lineage (a hard fact).
    recorded_copies: frozenset[str]
    overrides: Mapping[str, ManualOverride]
    #: Sections the pacing analysis wants shortened first (repetition, slow empty delivery).
    pacing_tighten: frozenset[str] = frozenset()


def judge(units: Sequence[EditUnit], jc: JudgeContext) -> dict[str, Decision]:
    """One decision per sentence, keyed by decision id, in story order."""
    decisions: dict[str, Decision] = {}
    for unit in units:
        decisions[unit.decision_id] = _decide(unit, jc)
    return decisions


def _decide(u: EditUnit, jc: JudgeContext) -> Decision:
    profile = jc.profile
    protected = jc.protected.get(u.key, ())
    keep = _keep(u, protected)
    cut, _ = _cut(u, jc)
    engagement = _engagement(u, jc)
    evidence = _evidence(u, protected)
    rationale: list[str] = []
    constraints = [p.reason for p in protected]
    action = Action.KEEP
    executability = Executability.EXECUTABLE
    warnings: list[str] = []

    wants_cut = (
        not protected
        and cut.value is not None
        and keep.value is not None
        and cut.value >= profile.cut_threshold
        and cut.value - keep.value >= profile.cut_margin
    )
    if protected:
        rationale.append("kept: " + "; ".join(constraints))
    elif wants_cut:
        action = Action.CUT
        rationale.append(cut.reason)
        executability = _cut_executability(u, jc)
        if executability is Executability.NEEDS_REVIEW:
            warnings.append("removal changes the content; meaning preservation is not established")
    else:
        rationale.append(f"kept: {keep.reason}")

    if u.extent is None:
        executability = Executability.UNRESOLVED
        warnings.append("the sentence has no time in the analysis, so it cannot be placed")
    elif u.coarse:
        executability = _weakest(executability, Executability.NEEDS_REVIEW)
        warnings.append("only the story beat is known, not its sentences; boundaries are coarse")

    signals = [keep, cut]
    if engagement is not None:
        signals.append(engagement)
    if action is Action.CUT:
        signals.append(narrative_risk(u, keep))
    decision = Decision(
        decision_id=u.decision_id,
        action=action,
        origin=Origin.AUTOMATIC,
        asset_id=u.asset_id,
        section_id=u.section_id,
        beat_id=u.beat_id,
        subject=_subject(u, jc),
        unit_ids=(u.unit.unit_id,),
        signals=tuple(signals),
        executability=executability,
        rationale=tuple(rationale),
        constraints=tuple(constraints),
        evidence=evidence,
        warnings=tuple(warnings),
    )
    return _apply_override(decision, jc.overrides.get(u.decision_id))


def _apply_override(decision: Decision, override: ManualOverride | None) -> Decision:
    """A person's decision replaces the automatic one and is never changed by a replan."""
    if override is None:
        return decision
    return replace(
        decision,
        action=override.action,
        origin=Origin.MANUAL,
        executability=Executability.EXECUTABLE,
        rationale=(f"set by a person: {override.note or override.action.value}",),
        note=override.note or None,
        warnings=(),
    )


def _keep(u: EditUnit, protected: Sequence[Protection]) -> Signal:
    if protected:
        return Signal(
            name=SignalName.KEEP,
            value=1.0,
            basis=SignalBasis.MEASURED,
            reason="protected by " + "; ".join(p.reason for p in protected),
        )
    if u.pure_filler:
        return Signal(
            name=SignalName.KEEP,
            value=_FILLER_KEEP,
            basis=SignalBasis.HEURISTIC,
            reason="the sentence is only filler words",
        )
    base = PRIORITY_VALUE.get(u.priority, 0.5)
    importance = u.segment.importance if u.segment is not None else None
    if importance is not None and u.priority != "redundant_candidate":
        return Signal(
            name=SignalName.KEEP,
            value=max(base, importance),
            basis=SignalBasis.HEURISTIC,
            reason=f"section priority {u.priority}, passage importance {importance:.2f}",
        )
    return Signal(
        name=SignalName.KEEP,
        value=base,
        basis=SignalBasis.HEURISTIC,
        reason=f"section priority {u.priority}",
    )


def _cut(u: EditUnit, jc: JudgeContext) -> tuple[Signal, dict[str, float]]:
    profile = jc.profile
    parts: dict[str, float] = {}
    if u.pure_filler:
        parts["filler sentence"] = 0.9
    if profile.trim_greeting and u.order < 3 and is_greeting(u.unit.text):
        parts["opening greeting"] = 0.75
    if profile.cut_redundant:
        if u.priority == "redundant_candidate":
            parts["story marks the section as a repeat"] = 0.85
        if u.segment is not None and u.segment.repeats == "nothing_new":
            parts["repeats earlier material and adds nothing"] = 0.8
    if u.segment is not None and u.segment.removal is not None:
        parts["content analysis removal score"] = round(0.9 * u.segment.removal, 3)
    if _false_start(u, jc):
        parts["false start restated by the next sentence"] = 0.8
    if not parts:
        return (
            Signal(
                name=SignalName.CUT,
                value=None if u.coarse or u.segment is None else 0.0,
                basis=SignalBasis.HEURISTIC,
                reason="no sentence-level evidence"
                if u.coarse or u.segment is None
                else "no reason to remove it was found",
            ),
            parts,
        )
    name, value = max(parts.items(), key=lambda item: item[1])
    others = [f"{n} {v:.2f}" for n, v in parts.items() if n != name]
    reason = f"{name} ({value:.2f})" + (f"; also {', '.join(others)}" if others else "")
    return (
        Signal(name=SignalName.CUT, value=value, basis=SignalBasis.HEURISTIC, reason=reason),
        parts,
    )


def _false_start(u: EditUnit, jc: JudgeContext) -> bool:
    """A sentence that stops mid-way and is restated right after: the restatement is the "
    "sentence."""
    if u.unit.ends_clean or u.coarse:
        return False
    content = jc.evidence[u.asset_id].content if u.asset_id in jc.evidence else None
    if content is None or u.source_pos + 1 >= len(content.units):
        return False
    here, after = tokens(u.unit.text), tokens(content.units[u.source_pos + 1].text)
    return len(here) >= 2 and len(after) >= len(here) and after[:2] == here[:2]


def _cut_executability(u: EditUnit, jc: JudgeContext) -> Executability:
    if u.pure_filler and u.extent is not None and u.extent.words:
        return Executability.EXECUTABLE
    if u.section_id in jc.recorded_copies:
        return Executability.EXECUTABLE
    return Executability.NEEDS_REVIEW


def _weakest(a: Executability, b: Executability) -> Executability:
    order = (Executability.EXECUTABLE, Executability.NEEDS_REVIEW, Executability.UNRESOLVED)
    return max(a, b, key=order.index)


def _engagement(u: EditUnit, jc: JudgeContext) -> Signal | None:
    values: list[float] = []
    names: list[str] = []
    if u.extent is not None and u.extent.words:
        emphasis = [w.emphasis for w in u.extent.words if w.emphasis is not None]
        expressive = [w.expressiveness for w in u.extent.words if w.expressiveness is not None]
        if emphasis:
            values.append(max(emphasis))
            names.append(f"emphasis {max(emphasis):.2f}")
        if expressive:
            values.append(max(expressive))
            names.append(f"expressiveness {max(expressive):.2f}")
    if u.segment is not None:
        for label, value in (("hook", u.segment.hook), ("highlight", u.segment.highlight)):
            if value is not None:
                values.append(value)
                names.append(f"{label} {value:.2f}")
    if not values:
        return None
    return Signal(
        name=SignalName.ENGAGEMENT,
        value=round(sum(values) / len(values), 3),
        basis=SignalBasis.MEASURED,
        reason="mean of " + ", ".join(names),
    )


def narrative_risk(u: EditUnit, keep: Signal) -> Signal:
    if u.pure_filler:
        return Signal(
            name=SignalName.NARRATIVE_RISK,
            value=0.05,
            basis=SignalBasis.HEURISTIC,
            reason="only filler words are removed",
        )
    importance = max(PRIORITY_VALUE.get(u.priority, 0.5), keep.value or 0.0)
    return Signal(
        name=SignalName.NARRATIVE_RISK,
        value=round(min(1.0, 0.25 + 0.5 * importance), 3),
        basis=SignalBasis.HEURISTIC,
        reason="content is removed and its effect on meaning is not verified",
    )


def _evidence(u: EditUnit, protected: Sequence[Protection]) -> tuple[EvidenceRef, ...]:
    refs = [
        EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=u.section_id),
        EvidenceRef(kind=EvidenceKind.CONTENT_UNIT, ref=u.unit.unit_id, asset_id=u.asset_id),
    ]
    if u.beat_id is not None:
        refs.append(EvidenceRef(kind=EvidenceKind.STORY_BEAT, ref=u.beat_id))
    if u.extent is not None and u.extent.words:
        refs.append(
            EvidenceRef(
                kind=EvidenceKind.AUDIO_WORD,
                ref=f"{u.extent.words[0].index}-{u.extent.words[-1].index}",
                asset_id=u.asset_id,
                detail=u.extent.precision.value,
            )
        )
    refs.extend(EvidenceRef(kind=p.kind, ref=p.ref, detail=p.reason) for p in protected)
    return tuple(dict.fromkeys(refs))


def _subject(u: EditUnit, jc: JudgeContext) -> SourceRange | None:
    if u.extent is None:
        return None
    return make_range(
        jc.evidence[u.asset_id].source,
        u.extent.start,
        u.extent.end,
        u.extent.precision,
        u.extent.precision,
    )


# --- dependencies --------------------------------------------------------------------------
def close_dependencies(
    units: Sequence[EditUnit], decisions: dict[str, Decision], jc: JudgeContext
) -> list[str]:
    """Restore what kept material needs. Returns what was restored (decision ids), for the report.

    A person's decisions are never touched; if one removes something a kept passage needs, the
    validation reports it.
    """
    by_unit = {(u.asset_id, u.unit.unit_id): u for u in units}
    by_segment: dict[tuple[str, str], list[EditUnit]] = {}
    for u in units:
        by_segment.setdefault((u.asset_id, u.unit.segment_id), []).append(u)
    restored: list[str] = []

    def restore(target: EditUnit, why: str) -> bool:
        current = decisions[target.decision_id]
        if current.action is not Action.CUT or current.origin is Origin.MANUAL:
            return False
        decisions[target.decision_id] = replace(
            current,
            action=Action.KEEP,
            executability=Executability.EXECUTABLE
            if target.extent is not None
            else Executability.UNRESOLVED,
            rationale=(*current.rationale, f"restored: {why}"),
            constraints=(*current.constraints, why),
            signals=tuple(s for s in current.signals if s.name is not SignalName.NARRATIVE_RISK),
            warnings=(),
        )
        restored.append(target.decision_id)
        return True

    for _ in range(10):
        changed = False
        kept = [u for u in units if decisions[u.decision_id].action is not Action.CUT]
        for u in kept:
            seg = u.segment
            if seg is None:
                continue
            for need in seg.requires_context:
                target = by_unit.get((u.asset_id, need))
                if target is not None:
                    changed |= restore(
                        target, f"sentence {need} is context that {u.unit.unit_id} needs"
                    )
            for dep in seg.depends_on:
                members = by_segment.get((u.asset_id, dep), [])
                if members and all(decisions[m.decision_id].action is Action.CUT for m in members):
                    for member in members:
                        changed |= restore(
                            member, f"segment {dep} is depended on by {seg.segment_id}"
                        )
        for info in jc.story.key_info:
            holders = [by_unit.get((info.source.asset_id, uid)) for uid in info.source.unit_ids]
            if any(
                h is not None and decisions[h.decision_id].action is not Action.CUT for h in holders
            ):
                for qualifier in info.qualified_by:
                    target = by_unit.get((info.source.asset_id, qualifier))
                    if target is not None:
                        changed |= restore(target, f"qualifies {info.info_id}, which is kept")
        if not changed:
            break
    return restored


def keep_every_essential_section(
    units: Sequence[EditUnit], decisions: dict[str, Decision]
) -> list[str]:
    """No essential section may end up empty: the highest-``keep`` timed sentence returns."""
    restored: list[str] = []
    for section_id in dict.fromkeys(u.section_id for u in units):
        members = [u for u in units if u.section_id == section_id]
        if members[0].priority != "essential":
            continue
        if any(decisions[m.decision_id].action is not Action.CUT for m in members):
            continue
        if any(decisions[m.decision_id].origin is Origin.MANUAL for m in members):
            continue
        timed = [m for m in members if m.extent is not None] or members
        best = max(timed, key=lambda m: decisions[m.decision_id].value(SignalName.KEEP) or 0.0)
        current = decisions[best.decision_id]
        decisions[best.decision_id] = replace(
            current,
            action=Action.KEEP,
            executability=Executability.EXECUTABLE,
            rationale=(
                *current.rationale,
                "restored: an essential section keeps at least one sentence",
            ),
            constraints=(*current.constraints, "essential section must stay represented"),
            warnings=(),
        )
        restored.append(best.decision_id)
    return restored
