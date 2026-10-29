"""Judge a proposed sequence: every cut between two picture clips, one transition at a time. Pure.

For each transition the detectors report what the evidence shows (``detect``). This module turns a
finding into an issue:

* **style**: how likely the difference is a deliberate choice, from the profile's prior for that
  kind of break, the story context (a section boundary, a cold open, a cross-recording cut, speech
  trimmed) and the detector's own hints. An ESTIMATE with its reasons, never a fact;
* **level**: the finding's strength x the profile's weight x ``(1 - 0.8 x style)`` against the
  profile's thresholds scaled by strictness. A difference that is real but probably deliberate is
  kept as ``info`` (not surfaced) rather than dropped, so nothing is hidden;
* **repairs** (``repair``): what could change, who would do it and how far it was examined.

A transition whose fingerprint is unchanged since the previous analysis is not judged again: its
issues are carried over (rebound to the new place). A person's accept/reject decision follows the
issue through every reanalysis. Cuts that cannot be decided become ``Unresolved`` items saying
what is missing.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.continuity_intelligence.domain.detect import (
    Detected,
    Finding,
    detect,
)
from media_house.modules.continuity_intelligence.domain.facts import (
    SideFact,
    TransitionFact,
)
from media_house.modules.continuity_intelligence.domain.model import (
    CompatiblePair,
    ContinuityAnalysis,
    InputRecord,
    Issue,
    IssueDecision,
    Metrics,
    Provenance,
    SourceSpan,
    StageReport,
    TransitionRecord,
    Unresolved,
)
from media_house.modules.continuity_intelligence.domain.profile import (
    ContinuityConfig,
    ContinuityProfile,
)
from media_house.modules.continuity_intelligence.domain.repair import repairs_for
from media_house.modules.continuity_intelligence.domain.validation import validate
from media_house.modules.continuity_intelligence.domain.values import (
    ANALYZER_VERSION,
    LEVEL_ORDER,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Availability,
    IssueType,
    Level,
    ResultState,
    TransitionKind,
)

_STYLE_DAMPING = 0.8
LIMITATIONS = (
    "Validated on synthetic edits and hand-built analyses; no real footage or real vision model "
    "has been evaluated, and every threshold is an uncalibrated design choice.",
    "Subject position, travel and face cues come from the video analysis' tracks and shot-level "
    "medians: a cut between moments the analysis did not track is reported as unresolved, not "
    "judged. Action continuity is a motion-extrapolation heuristic, not action-state tracking.",
    "Identity is appearance clustering and text read from the picture: similar-looking is not the "
    "same person, and nobody is recognised.",
    "Intentional style is an estimate from the profile and the story's structure; it is never "
    "inferred from appearance alone.",
    "Cutaway (cover) enter/exit points are not judged as cuts; alternates are not searched for: a "
    "repair that names another take says its range was not compared.",
)


@dataclass(frozen=True, slots=True)
class AnalysisMeta:
    continuity_analysis_id: str
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    edit_plan_id: str
    edit_plan_version: int
    configuration_fingerprint: str
    story_format: str | None
    sources: tuple[str, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    warnings: tuple[str, ...]
    decisions: tuple[IssueDecision, ...] = ()


def rational(value: Fraction) -> Rational:
    value = max(Fraction(0), value)
    return Rational(value.numerator, value.denominator)


def analyse_continuity(
    transitions: Sequence[TransitionFact],
    config: ContinuityConfig,
    meta: AnalysisMeta,
    previous: ContinuityAnalysis | None = None,
) -> ContinuityAnalysis:
    profile = config.resolve(meta.story_format)
    before = {r.fingerprint: r for r in previous.transitions} if previous else {}
    records: list[TransitionRecord] = []
    issues: list[Issue] = []
    unresolved: list[Unresolved] = []
    pairs: list[CompatiblePair] = []
    picture = sound = judged = 0
    for t in transitions:
        prior = before.get(t.fingerprint)
        if prior is not None and previous is not None:
            records.append(_record(t, reused=True, present=(), missing=()))
            issues += [_rebind(i, t) for i in previous.issues_at(prior.transition_id)]
            unresolved += [
                replace(u, transition_id=t.transition_id)
                for u in previous.unresolved
                if u.transition_id == prior.transition_id
            ]
            pairs += [
                replace(
                    p,
                    transition_id=t.transition_id,
                    outgoing=_span(t.outgoing, t),
                    incoming=_span(t.incoming, t),
                )
                for p in previous.compatible_pairs
                if p.transition_id == prior.transition_id
            ]
            continue
        judged += 1
        found = detect(t)
        present, missing = _evidence(t)
        picture += int("picture" in present)
        sound += int("sound" in present)
        records.append(_record(t, reused=False, present=present, missing=missing))
        built = [_issue(f, t, profile, config.strictness) for f in found.findings]
        issues += [i for i in built if i is not None]
        unresolved += [
            Unresolved(transition_id=t.transition_id, question=m.question, missing=m.missing)
            for m in found.missing
        ]
        if not any(i.level is not Level.INFO for i in built if i is not None) and found.differences:
            pairs.append(_compatible(t, found))
    issues = _apply_decisions(issues, meta.decisions)
    reused = len(transitions) - judged
    metrics = Metrics(
        transitions=len(transitions),
        evaluated=judged,
        reused=reused,
        skipped=0,
        issues=len(issues),
        unresolved=len(unresolved),
        picture_coverage=round(picture / judged, 3) if judged else 1.0,
        sound_coverage=round(sound / judged, 3) if judged else 1.0,
    )
    partial = bool(judged) and picture < judged
    result = ContinuityAnalysis(
        continuity_analysis_id=meta.continuity_analysis_id,
        analysis_version=meta.analysis_version,
        supersedes=meta.supersedes,
        project_id=meta.project_id,
        created_at=meta.created_at,
        state=ResultState.PARTIAL if partial else ResultState.OK,
        edit_plan_id=meta.edit_plan_id,
        edit_plan_version=meta.edit_plan_version,
        profile=profile.name,
        strictness=config.strictness,
        sources=meta.sources,
        transitions=tuple(records),
        issues=tuple(sorted(issues, key=_order)),
        unresolved=tuple(unresolved),
        compatible_pairs=tuple(pairs),
        decisions=meta.decisions,
        inputs=meta.inputs,
        stages=meta.stages,
        metrics=metrics,
        validation=(),
        provenance=Provenance(
            analyzer_version=ANALYZER_VERSION,
            schema_version=SCHEMA_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile_name=profile.name,
            profile_version=profile.version,
            strictness=config.strictness,
            configuration_fingerprint=meta.configuration_fingerprint,
        ),
        warnings=(
            *meta.warnings,
            *(
                ("some cuts have no picture evidence on a side; they are unresolved",)
                if partial
                else ()
            ),
        ),
        limitations=LIMITATIONS,
    )
    return replace(result, validation=tuple(validate(result)))


def _order(issue: Issue) -> tuple[int, float, str]:
    return (-LEVEL_ORDER[issue.level], -(issue.peak or 0.0), issue.issue_id)


# --- one finding -> one issue -------------------------------------------------------------------
def _issue(
    f: Finding, t: TransitionFact, profile: ContinuityProfile, strictness: float
) -> Issue | None:
    style, reason = _style(f, t, profile)
    effective = f.peak * profile.weight(f.type) * (1.0 - _STYLE_DAMPING * style)
    level = _level(effective, profile, strictness)
    if level is None:
        raw = _level(f.peak * profile.weight(f.type), profile, strictness)
        if raw is None:
            return None
        level = Level.INFO  # real, but probably deliberate: kept, not surfaced
    if f.type is IssueType.SYNC_UNCERTAIN:
        level = Level.LOW if level is not Level.INFO else level
    repairs = repairs_for(f, t, style)
    usable = [r for r in repairs if r.availability is not Availability.UNAVAILABLE]
    recommended = usable[0].kind if usable else None
    surfaced = level in (Level.HIGH, Level.MEDIUM) or (level is Level.LOW and style < 0.5)
    return Issue(
        issue_id=f"is:{t.fingerprint[:12]}:{f.type.value}",
        type=f.type,
        scope=f.scope,
        level=level,
        transition_id=t.transition_id,
        spans=(_span(t.outgoing, t), _span(t.incoming, t)),
        timeline_at=rational(t.timeline_at),
        section_ids=tuple(
            dict.fromkeys(s for s in (t.outgoing.section_id, t.incoming.section_id) if s)
        ),
        scores=f.scores,
        detectability_confidence=None if f.confidence is None else round(f.confidence, 3),
        intentional_style_likelihood=round(style, 3),
        style_reason=reason,
        explanation=f.explanation,
        why_it_matters=f.why,
        evidence=f.evidence,
        repairs=repairs,
        recommended=recommended,
        related_assets=t.related_assets,
        unresolved_questions=f.questions,
        identity=f.identity,
        surfaced=surfaced,
    )


def _level(effective: float, profile: ContinuityProfile, strictness: float) -> Level | None:
    if effective >= profile.high / strictness:
        return Level.HIGH
    if effective >= profile.medium / strictness:
        return Level.MEDIUM
    if effective >= profile.low / strictness:
        return Level.LOW
    return None


def _style(f: Finding, t: TransitionFact, profile: ContinuityProfile) -> tuple[float, str]:
    parts: list[tuple[str, float]] = []
    prior = profile.style_prior(f.type)
    if prior > 0:
        parts.append((f"the {profile.name} profile accepts this kind of break", prior))
    if t.kind is TransitionKind.CROSS_RECORDING and f.type in _LOOK_TYPES:
        parts.append(("the cut joins different recordings", profile.cross_recording_prior))
    if t.section_change and f.type in _LOOK_TYPES:
        parts.append(("the cut is a story section boundary", 0.3))
    if t.cold_open:
        parts.append(("the clip is a cold open", 0.5))
    parts.extend(f.style_hints)
    if not parts:
        return 0.0, "nothing in the profile or the story suggests it is deliberate"
    miss = 1.0
    for _, p in parts:
        miss *= 1.0 - p
    return 1.0 - miss, "; ".join(name for name, _ in parts)


_LOOK_TYPES = frozenset(
    {
        IssueType.VISUAL_DISCONTINUITY,
        IssueType.LIGHTING_MISMATCH,
        IssueType.SCREEN_DIRECTION,
        IssueType.EYELINE,
        IssueType.IDENTITY_MISMATCH,
        IssueType.OBJECT_STATE,
        IssueType.NARRATIVE_CONFUSION,
    }
)


# --- plumbing ----------------------------------------------------------------------------------
def _span(side: SideFact, t: TransitionFact) -> SourceSpan:
    return SourceSpan(
        asset_id=side.asset_id,
        start=rational(side.clip_start),
        end=rational(side.clip_end),
        clip_id=side.clip_id,
        boundary=rational(side.moment),
        shot_id=side.shot_id,
    )


def _evidence(t: TransitionFact) -> tuple[tuple[str, ...], tuple[str, ...]]:
    present: list[str] = []
    missing: list[str] = []
    both = t.outgoing.edge is not None and t.incoming.edge is not None
    (present if both else missing).append("picture")
    sound = t.outgoing.audio is not None and t.incoming.audio is not None
    (present if sound else missing).append("sound")
    known = t.outgoing.sync != "unknown" and t.incoming.sync != "unknown"
    (present if known else missing).append("alignment")
    return tuple(present), tuple(missing)


def _record(
    t: TransitionFact, *, reused: bool, present: tuple[str, ...], missing: tuple[str, ...]
) -> TransitionRecord:
    return TransitionRecord(
        transition_id=t.transition_id,
        fingerprint=t.fingerprint,
        kind=t.kind,
        outgoing_clip=t.outgoing.clip_id,
        incoming_clip=t.incoming.clip_id,
        outgoing_asset=t.outgoing.asset_id,
        outgoing_at=rational(t.outgoing.moment),
        incoming_asset=t.incoming.asset_id,
        incoming_at=rational(t.incoming.moment),
        timeline_at=rational(t.timeline_at),
        evidence_present=present,
        evidence_missing=missing,
        reused=reused,
    )


def _rebind(issue: Issue, t: TransitionFact) -> Issue:
    return replace(
        issue,
        transition_id=t.transition_id,
        timeline_at=rational(t.timeline_at),
        spans=(_span(t.outgoing, t), _span(t.incoming, t)),
    )


def _compatible(t: TransitionFact, found: Detected) -> CompatiblePair:
    return CompatiblePair(
        transition_id=t.transition_id,
        outgoing=_span(t.outgoing, t),
        incoming=_span(t.incoming, t),
        compatibility=round(1.0 - max(found.differences), 3),
        checked=tuple(dict.fromkeys(found.compared)),
    )


def _apply_decisions(issues: list[Issue], decisions: Sequence[IssueDecision]) -> list[Issue]:
    by_id = {d.issue_id: d for d in decisions}
    return [
        replace(i, decision=by_id[i.issue_id].decision, decision_note=by_id[i.issue_id].note)
        if i.issue_id in by_id
        else i
        for i in issues
    ]


__all__ = ["AnalysisMeta", "analyse_continuity"]
