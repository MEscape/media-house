"""The editing engine: story plan + evidence in, one validated edit plan out.

Pure: no clock, I/O or model. The order of work is fixed and each step has one owner:

1. sentences in story order (``units``), hard constraints (``protections``);
2. a decision per sentence from separate signals (``judge``), dependencies restored;
3. fitting to a target length if there is one (``tighten``);
4. runs, trims, cut points, pieces and the timeline (``assemble``, ``cutpoints``);
5. cover footage, J/L bridges (``coverage``), continuity at the cuts (``continuity``);
6. requests for later stages (``effects``), pacing notes (``pacing``);
7. the validation boundary (``validation``).

A person's overrides are applied inside step 2 and survive every later step.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import (
    Assembly,
    Piece,
    Timeline,
    apply_assembly,
    assemble,
    build_timeline,
)
from media_house.modules.editing_intelligence.domain.continuity import assess_cuts
from media_house.modules.editing_intelligence.domain.coverage import plan_coverage
from media_house.modules.editing_intelligence.domain.cutpoints import CutContext, cut_in, cut_out
from media_house.modules.editing_intelligence.domain.effects import plan_effects, unit_starts
from media_house.modules.editing_intelligence.domain.errors import NothingToEdit
from media_house.modules.editing_intelligence.domain.facts import (
    AssetEvidence,
    BrollFacts,
    ContinuityFacts,
    FramingFacts,
    PacingFacts,
    StoryFacts,
)
from media_house.modules.editing_intelligence.domain.framing import attach_framing
from media_house.modules.editing_intelligence.domain.hooks import Hooks
from media_house.modules.editing_intelligence.domain.judge import (
    JudgeContext,
    close_dependencies,
    judge,
    keep_every_essential_section,
)
from media_house.modules.editing_intelligence.domain.model import (
    Confidence,
    Decision,
    EditDuration,
    EditPlan,
    EvidenceRef,
    InputRecord,
    Issue,
    ManualOverride,
    PacingNote,
    Placement,
    Provenance,
    Signal,
    SourceRecord,
    StageReport,
    StoryRef,
    StructuralProposal,
    TargetRecord,
    Timebase,
)
from media_house.modules.editing_intelligence.domain.pacing import plan_pacing
from media_house.modules.editing_intelligence.domain.profile import EditConfig, EditingProfile
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.speech import AudioIndex
from media_house.modules.editing_intelligence.domain.tighten import tighten
from media_house.modules.editing_intelligence.domain.timing import exact, to_rational
from media_house.modules.editing_intelligence.domain.units import (
    EditUnit,
    collect_units,
    protections,
)
from media_house.modules.editing_intelligence.domain.validation import validate
from media_house.modules.editing_intelligence.domain.values import (
    EDITOR_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Action,
    ConstraintKind,
    ConstraintState,
    DurationMode,
    EvidenceKind,
    Executability,
    IssueKind,
    Origin,
    PlacementRole,
    PlanState,
    Precision,
    Severity,
    SignalBasis,
    SignalName,
    StageState,
    StructuralKind,
    TrackKind,
)

_MEASURED = frozenset({Precision.FRAME_EXACT, Precision.WORD_ALIGNED, Precision.SEGMENT_ALIGNED})
_DEFAULT_RATE = Rational(30, 1)

LIMITATIONS: tuple[str, ...] = (
    "Cut points are as accurate as the word timing upstream measured; where only sentence times "
    "exist the plan says so and no frame accuracy is claimed.",
    "Whether removing a passage preserves its meaning is judged from the story plan's structure "
    "(key information, qualifications, dependencies), not by understanding the words: any removal "
    "of content is a suggestion until a person checks it.",
    "Continuity is assessed from shot-level measurements only; subject identity, eyelines and pose "
    "are not exposed by the analysis and are not judged.",
    "Cover footage comes from the shots alignment ranked for the story's visual opportunities; no "
    "other footage is searched. Music beats, transitions and graphics are requested, not made.",
    "The estimated length is the sum of the clips; nothing has compiled a timeline.",
)


@dataclass(frozen=True, slots=True)
class PlanMeta:
    edit_plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    configuration_fingerprint: str
    story: StoryRef
    inputs: tuple[InputRecord, ...] = ()
    warnings: tuple[str, ...] = ()
    overrides: tuple[ManualOverride, ...] = ()
    advisors: tuple[tuple[str, str], ...] = ()
    stages: tuple[StageReport, ...] = ()


class _Ids:
    """Deterministic ids: the same inputs always give the same plan."""

    def __init__(self) -> None:
        self._count: Counter[str] = Counter()

    def make(self, prefix: str) -> Callable[[], str]:
        def next_id() -> str:
            self._count[prefix] += 1
            return f"{prefix}{self._count[prefix]:03d}"

        return next_id


@dataclass(slots=True)
class _Built:
    asm: Assembly
    pieces: list[Piece]
    timeline: Timeline


def plan_edit(
    story: StoryFacts,
    evidence: Mapping[str, AssetEvidence],
    config: EditConfig,
    meta: PlanMeta,
    hooks: Hooks | None = None,
    pacing: PacingFacts | None = None,
    broll: BrollFacts | None = None,
    cover_evidence: Mapping[str, AssetEvidence] | None = None,
    continuity: ContinuityFacts | None = None,
    framing: FramingFacts | None = None,
) -> EditPlan:
    hooks = hooks or Hooks()
    profile = config.resolve(story.target_format)
    ids = _Ids()
    issue_id, note_id, effect_id, proposal_id = (
        ids.make("i"),
        ids.make("pn"),
        ids.make("fx"),
        ids.make("sp"),
    )
    indexes = {a: AudioIndex(e.audio) for a, e in evidence.items() if e.audio is not None}
    contexts = {
        a: CutContext(e.source, indexes.get(a), e.video, profile, pacing)
        for a, e in evidence.items()
    }
    units = collect_units(story, evidence, indexes)
    if not units:
        raise NothingToEdit("the story plan names no sentences of the recordings")
    jc = JudgeContext(
        story=story,
        evidence=evidence,
        profile=profile,
        protected=protections(story, units, profile),
        recorded_copies=frozenset(
            s
            for r in story.relations
            if r.certainty == "recorded" and r.treatment in ("redundant_copy", "same_recording")
            for s in r.affected_sections
        ),
        overrides={o.decision_id: o for o in meta.overrides},
        pacing_tighten=pacing.tighten_sections() if pacing else frozenset(),
    )
    decisions = judge(units, jc)
    close_dependencies(units, decisions, jc)
    keep_every_essential_section(units, decisions)

    timebase = choose_timebase(config, evidence)
    rate = timebase.frame_rate
    target = resolve_target(config, story, profile)
    opening = _cold_open(story, units, contexts, config)

    def build() -> _Built:
        kept = [
            u
            for u in units
            if decisions[u.decision_id].action is not Action.CUT and u.extent is not None
        ]
        asm = assemble(kept, contexts, profile)
        pieces = ([opening] if opening is not None else []) + asm.pieces
        return _Built(asm, pieces, build_timeline(pieces, evidence, rate, profile))

    proposals: list[StructuralProposal] = []
    if target is not None and target.high is not None and target.mode is not DurationMode.NONE:
        result = tighten(
            units,
            decisions,
            jc,
            lambda: build().timeline.total,
            exact(target.high),
            config.allow_structural,
            proposal_id,
        )
        proposals.extend(result.proposals)
    built = build()
    final = dict(decisions)
    apply_assembly(final, built.asm, profile)

    extra = _reorder_decisions(story, units, final, evidence)
    if opening is not None:
        extra.append(_opening_decision(story, opening, evidence))
        proposals.append(
            StructuralProposal(
                proposal_id=proposal_id(),
                kind=StructuralKind.COLD_OPEN,
                section_ids=(opening.units[0].section_id,) if opening.units else (),
                rationale="the story plan recommends opening with this passage instead of the "
                "recorded start",
                effect_seconds=-round(float(opening.duration), 2),
                applied=True,
                status="applied: the story's own recommended opening (a blueprint choice, not a "
                "redesign)",
            )
        )
    for d in extra:
        final.setdefault(d.decision_id, d)

    # Footage of other recordings (offered by the B-roll analysis) is judged and placed like any
    # candidate, but it is never part of the story: the story's own evidence wins on a clash.
    everything = {**(cover_evidence or {}), **evidence}
    coverage = plan_coverage(
        story,
        units,
        built.pieces,
        built.timeline,
        everything,
        rate,
        profile,
        hooks,
        issue_id,
        effect_id,
        broll,
    )
    for d in coverage.decisions:
        final[d.decision_id] = d
    placements = [*built.timeline.placements, *coverage.placements]
    placements, continuity_issues = assess_cuts(
        placements, everything, profile, hooks, issue_id, continuity
    )
    placements, framing_issues = attach_framing(placements, framing, issue_id)
    placements = _link_cold_open(placements)

    starts = unit_starts([p for p in built.pieces if p.role is PlacementRole.MAIN], _main(built))
    effects, emphasis = plan_effects(units, final, starts, built.timeline.total, profile, effect_id)
    for d in emphasis:
        final.setdefault(d.decision_id, d)
    effects = sorted([*effects, *coverage.effects], key=lambda r: (r.at.value, r.request_id))
    notes = plan_pacing(
        story,
        [p for p in built.pieces if p.role is PlacementRole.MAIN],
        note_id,
        sections=pacing is None,
    )
    notes += _pacing_notes(pacing, built.asm.pacing_notes, note_id)
    notes += _broll_notes(broll, note_id)
    notes += _continuity_notes(continuity, note_id)
    notes += _framing_notes(framing, note_id)

    issues = [
        *coverage.issues,
        *continuity_issues,
        *framing_issues,
        *_evidence_issues(story, evidence, issue_id),
    ]
    issues.extend(_story_issues(story, final, issue_id))
    issues.extend(_unresolved_decisions(final, issue_id))
    stages = [*meta.stages, *_stages(evidence, hooks, pacing, broll, continuity, framing)]

    essential = sum(
        (u.extent.seconds for u in units if u.priority == "essential" and u.extent is not None),
        Fraction(0),
    )
    duration = EditDuration(
        estimated=to_rational(built.timeline.total),
        validated=False,
        assumptions=(
            "the sum of the main clips; transitions add no length",
            "durations are exact source seconds; timeline frames are rounded to the timeline rate",
            "no timeline has been compiled",
        ),
        essential_seconds=round(float(essential), 2),
        target=target,
        met=None,
    )
    ordered_decisions = tuple(sorted(final.values(), key=_decision_sort(units)))
    ordered_placements = tuple(
        sorted(
            placements,
            key=lambda p: (p.track.value, p.layer, p.timeline_start.value, p.placement_id),
        )
    )
    draft = EditPlan(
        edit_plan_id=meta.edit_plan_id,
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=meta.project_id,
        created_at=meta.created_at,
        state=PlanState.OK,
        story=meta.story,
        profile=profile.name,
        target_format=story.target_format,
        timebase=timebase,
        sources=(
            *(_source_record(a, evidence[a]) for a in story.asset_ids if a in evidence),
            *(
                _source_record(a, e)
                for a, e in (cover_evidence or {}).items()
                if a not in evidence and _is_used(a, placements)
            ),
        ),
        decisions=ordered_decisions,
        placements=ordered_placements,
        audio_bridges=tuple(coverage.bridges),
        coverage=tuple(coverage.requests),
        effect_requests=tuple(effects),
        constraints=(),
        issues=(),
        checks=(),
        structural_proposals=tuple(proposals),
        pacing_notes=tuple(notes),
        duration=duration,
        confidence=Confidence(value=0.0, components=()),
        inputs=meta.inputs,
        stages=tuple(stages),
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            editor_version=EDITOR_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=profile.version,
            configuration_fingerprint=meta.configuration_fingerprint,
            advisors=meta.advisors,
        ),
        warnings=meta.warnings,
        limitations=LIMITATIONS,
        overrides=meta.overrides,
    )
    validation = validate(draft, story, everything, target, issue_id)
    all_issues = (*issues, *validation.issues)
    unknown_overrides = tuple(
        Issue(
            issue_id=issue_id(),
            kind=IssueKind.UNKNOWN_REFERENCE,
            severity=Severity.WARNING,
            detail=f"the manual decision for {o.decision_id} matches no decision in this plan",
            subject=o.decision_id,
        )
        for o in meta.overrides
        if o.decision_id not in final
    )
    all_issues = (*all_issues, *unknown_overrides)
    met = None
    if target is not None and target.mode is not DurationMode.NONE:
        met = not any(
            i.kind is IssueKind.TARGET_NOT_MET and i.severity is Severity.WARNING
            for i in all_issues
        )
    violated = any(
        c.kind is ConstraintKind.HARD and c.state is ConstraintState.VIOLATED
        for c in validation.constraints
    )
    state = (
        PlanState.UNMET_CONSTRAINTS
        if violated
        else PlanState.WARNINGS
        if any(i.severity in (Severity.WARNING, Severity.UNRESOLVED) for i in all_issues)
        else PlanState.OK
    )
    return replace(
        draft,
        state=state,
        issues=all_issues,
        checks=validation.checks,
        constraints=validation.constraints,
        duration=replace(duration, met=met),
        confidence=_confidence(ordered_decisions, ordered_placements, evidence),
    )


def _link_cold_open(placements: list[Placement]) -> list[Placement]:
    """A cold open plays material that also appears in the story's own order: say which clip."""
    out: list[Placement] = []
    for p in placements:
        if p.role is PlacementRole.COLD_OPEN:
            origin = next(
                (
                    m
                    for m in placements
                    if m.role is PlacementRole.MAIN
                    and m.track is p.track
                    and m.layer == 0
                    and m.source.asset_id == p.source.asset_id
                    and exact(m.source.start.value)
                    <= exact(p.source.start.value)
                    < exact(m.source.end.value)
                ),
                None,
            )
            if origin is not None:
                p = replace(p, reuse_of=origin.placement_id)
        out.append(p)
    return out


def _main(built: _Built) -> Timeline:
    """The timeline restricted to the pieces that belong to the story's own order."""
    keep = [i for i, p in enumerate(built.pieces) if p.role is PlacementRole.MAIN]
    return Timeline(
        built.timeline.placements,
        built.timeline.total,
        tuple(built.timeline.starts[i] for i in keep),
    )


# --- configuration-derived choices ---------------------------------------------------------
def choose_timebase(config: EditConfig, evidence: Mapping[str, AssetEvidence]) -> Timebase:
    if config.timeline_frame_rate is not None:
        return Timebase(frame_rate=config.timeline_frame_rate, assumed=False, note="configured")
    rates = Counter(
        e.source.constant_frame_rate
        for e in evidence.values()
        if e.source.constant_frame_rate is not None and e.source.has_video is not False
    )
    if rates:
        rate = rates.most_common(1)[0][0]
        return Timebase(
            frame_rate=rate,
            assumed=False,
            note="the most common constant frame rate of the recordings",
        )
    return Timebase(
        frame_rate=_DEFAULT_RATE,
        assumed=True,
        note="no recording fixed a constant frame rate; 30 fps is assumed for timeline frames",
    )


def resolve_target(
    config: EditConfig, story: StoryFacts, profile: EditingProfile
) -> TargetRecord | None:
    if config.target is not None:
        if config.target.mode is DurationMode.NONE:
            return None
        return TargetRecord(
            low=config.target.low, high=config.target.high, mode=config.target.mode, origin="caller"
        )
    if story.target_low is None and story.target_high is None:
        return None
    return TargetRecord(
        low=story.target_low, high=story.target_high, mode=profile.story_target_mode, origin="story"
    )


# --- cold open and reorder -----------------------------------------------------------------
def _cold_open(
    story: StoryFacts,
    units: Sequence[EditUnit],
    contexts: Mapping[str, CutContext],
    config: EditConfig,
) -> Piece | None:
    if not config.use_recommended_opening or story.recommended_opening is None:
        return None
    opening = next((o for o in story.openings if o.candidate_id == story.recommended_opening), None)
    if opening is None or opening.strategy == "natural_start":
        return None
    members = sorted(
        (
            u
            for u in units
            if u.asset_id == opening.source.asset_id
            and u.unit.unit_id in opening.source.unit_ids
            and u.extent is not None
        ),
        key=lambda u: u.source_pos,
    )
    if not members or members[0].extent is None or members[-1].extent is None:
        return None
    ctx = contexts[members[0].asset_id]
    start, end = cut_in(ctx, members[0].extent), cut_out(ctx, members[-1].extent)
    return Piece(
        asset_id=members[0].asset_id,
        start=exact(start.time.value),
        end=exact(end.time.value),
        start_precision=start.precision,
        end_precision=end.precision,
        cut_in=start,
        cut_out=end,
        units=tuple(members),
        role=PlacementRole.COLD_OPEN,
        reuse_unit=members[0].key,
        decision_id=f"open:{opening.candidate_id}",
    )


def _opening_decision(
    story: StoryFacts, piece: Piece, evidence: Mapping[str, AssetEvidence]
) -> Decision:
    opening = next(o for o in story.openings if o.candidate_id == story.recommended_opening)
    confidences = [c.confidence for c in (piece.cut_in, piece.cut_out) if c is not None]
    known = [c for c in confidences if c is not None]
    safe = not opening.risks and not opening.needs_context and bool(known) and min(known) >= 0.6
    return Decision(
        decision_id=piece.decision_id or f"open:{opening.candidate_id}",
        action=Action.REORDER,
        origin=Origin.AUTOMATIC,
        asset_id=piece.asset_id,
        section_id=opening.section_id,
        beat_id=None,
        subject=make_range(
            evidence[piece.asset_id].source,
            piece.start,
            piece.end,
            piece.start_precision,
            piece.end_precision,
        ),
        unit_ids=tuple(u.unit.unit_id for u in piece.units),
        signals=(
            Signal(
                name=SignalName.REORDER,
                value=None,
                basis=SignalBasis.HEURISTIC,
                reason="a recommended opening is a blueprint choice, not a constraint",
            ),
        ),
        executability=Executability.EXECUTABLE if safe else Executability.NEEDS_REVIEW,
        rationale=(
            f"plays the story's recommended opening ({opening.strategy}) first: {opening.excerpt}",
            *(f"risk: {r}" for r in opening.risks),
        ),
        evidence=(EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=opening.section_id),),
        warnings=(
            ("the opening needs earlier sentences to make sense",) if opening.needs_context else ()
        ),
    )


def _reorder_decisions(
    story: StoryFacts,
    units: Sequence[EditUnit],
    decisions: Mapping[str, Decision],
    evidence: Mapping[str, AssetEvidence],
) -> list[Decision]:
    out: list[Decision] = []
    hard = {c.constraint_id for c in story.constraints if c.strength == "hard"}
    for change in story.order_changes:
        members = [
            u
            for u in units
            if u.section_id == change.section_id
            and u.extent is not None
            and decisions[u.decision_id].action is not Action.CUT
        ]
        if not members:
            continue
        first, last = members[0], members[-1]
        if first.extent is None or last.extent is None:
            continue
        forced = any(c in hard for c in change.constraint_ids)
        out.append(
            Decision(
                decision_id=f"ord:{change.section_id}",
                action=Action.REORDER,
                origin=Origin.AUTOMATIC,
                asset_id=first.asset_id,
                section_id=change.section_id,
                beat_id=None,
                subject=make_range(
                    evidence[first.asset_id].source,
                    first.extent.start,
                    last.extent.end if last.asset_id == first.asset_id else first.extent.end,
                    first.extent.precision,
                    first.extent.precision,
                ),
                unit_ids=tuple(u.unit.unit_id for u in members),
                signals=(
                    Signal(
                        name=SignalName.REORDER,
                        value=1.0 if forced else 0.5,
                        basis=SignalBasis.MEASURED,
                        reason=change.reason,
                    ),
                ),
                executability=Executability.EXECUTABLE,
                rationale=(
                    f"plays at position {change.to_index} instead of {change.from_index}, as the "
                    f"story plan orders it: {change.reason}",
                ),
                evidence=tuple(
                    EvidenceRef(kind=EvidenceKind.STORY_CONSTRAINT, ref=c)
                    for c in change.constraint_ids
                )
                or (EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=change.section_id),),
            )
        )
    return out


# --- reporting ---------------------------------------------------------------------------------
def _source_record(asset_id: str, e: AssetEvidence) -> SourceRecord:
    present = tuple(
        name
        for name, found in (
            ("content", e.content),
            ("audio", e.audio),
            ("video", e.video),
            ("alignment", e.alignment),
        )
        if found is not None
    )
    s = e.source
    return SourceRecord(
        asset_id=asset_id,
        name=s.name,
        duration=s.duration,
        frame_rate=s.frame_rate,
        variable_frame_rate=s.variable_frame_rate,
        has_video=s.has_video,
        has_audio=s.has_audio,
        evidence=present,
    )


def _evidence_issues(
    story: StoryFacts, evidence: Mapping[str, AssetEvidence], issue_id: Callable[[], str]
) -> list[Issue]:
    out: list[Issue] = []

    def missing(asset: str, detail: str) -> None:
        out.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.EVIDENCE_MISSING,
                severity=Severity.UNRESOLVED,
                detail=f"{asset}: {detail}",
                subject=asset,
            )
        )

    for asset_id in story.asset_ids:
        e = evidence.get(asset_id)
        if e is None:
            continue
        if e.source.usable is False:
            out.append(
                Issue(
                    issue_id=issue_id(),
                    kind=IssueKind.EVIDENCE_MISSING,
                    severity=Severity.WARNING,
                    detail=f"{asset_id}: the inspection found the media damaged or unusable",
                    subject=asset_id,
                )
            )
        if e.content is None:
            missing(
                asset_id, "no content analysis: the edit works on the story's beats, not sentences"
            )
        if e.audio is None:
            missing(
                asset_id,
                "no audio analysis: pauses, fillers and word-level cut points were not assessed",
            )
        if e.source.has_video is not False and e.video is None:
            missing(
                asset_id, "no video analysis: continuity and cover candidates were not assessed"
            )
        if e.source.has_video is not False and e.alignment is None:
            missing(asset_id, "no alignment: whether a speaker is visible is unknown")
        if e.source.duration is None:
            missing(
                asset_id, "no recording duration is known: ranges cannot be checked against the end"
            )
        elif e.source.frame_rate is None:
            missing(
                asset_id, "no frame rate is known: only seconds are offered, no frame positions"
            )
    return out


def _story_issues(
    story: StoryFacts, decisions: Mapping[str, Decision], issue_id: Callable[[], str]
) -> list[Issue]:
    out: list[Issue] = []
    treatments = Counter(r.treatment for r in story.relations)
    if treatments:
        summary = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in sorted(treatments.items()))
        out.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.RELATED_RECORDINGS,
                severity=Severity.RECOMMENDATION,
                detail=f"the recordings relate ({summary}); the story plan's treatment is "
                "followed, nothing more is inferred",
            )
        )
    review = [
        d
        for d in decisions.values()
        if d.executability is Executability.NEEDS_REVIEW
        and d.action in (Action.CUT, Action.SHORTEN)
    ]
    if review:
        out.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.PACING,
                severity=Severity.RECOMMENDATION,
                detail=f"{len(review)} cut(s) and trim(s) are suggestions that need a person's "
                "check before they run",
            )
        )
    return out


def _unresolved_decisions(
    decisions: Mapping[str, Decision], issue_id: Callable[[], str]
) -> list[Issue]:
    return [
        Issue(
            issue_id=issue_id(),
            kind=IssueKind.EVIDENCE_MISSING,
            severity=Severity.UNRESOLVED,
            detail=f"{d.decision_id}: "
            f"{d.warnings[0] if d.warnings else 'cannot be decided from the evidence'}",
            subject=d.decision_id,
        )
        for d in decisions.values()
        if d.executability is Executability.UNRESOLVED and d.action is not Action.COVER
    ]


def _pacing_notes(
    pacing: PacingFacts | None, kept: Sequence[str], note_id: Callable[[], str]
) -> list[PacingNote]:
    """The pacing analysis' advice, shown as notes: the editor applies what it can safely (pause
    verdicts, holds, which sections to tighten first) and passes the rest on, ranked."""
    if pacing is None:
        return []
    out = [
        PacingNote(note_id=note_id(), level="pacing", subject="pause", detail=text) for text in kept
    ]
    shown = ("tighten_pause", "preserve_pause")
    for advice in sorted(
        (a for a in pacing.advice if a.kind not in shown), key=lambda a: a.priority
    )[:_MAX_PACING_NOTES]:
        confidence = "" if advice.confidence is None else f" (confidence {advice.confidence:.2f})"
        out.append(
            PacingNote(
                note_id=note_id(),
                level="pacing",
                subject=advice.section_id or advice.asset_id,
                detail=f"{advice.kind.replace('_', ' ')}: {advice.proposal}{confidence}",
                seconds=advice.delta_seconds,
            )
        )
    return out


_MAX_PACING_NOTES = 25
_MAX_BROLL_NOTES = 15
_MAX_CONTINUITY_NOTES = 15
_MAX_FRAMING_NOTES = 15


def _continuity_notes(
    continuity: ContinuityFacts | None, note_id: Callable[[], str]
) -> list[PacingNote]:
    """Findings the continuity analysis ranked as worth a look: shown, never applied."""
    if continuity is None:
        return []
    return [
        PacingNote(note_id=note_id(), level="continuity", subject=n.subject, detail=n.detail)
        for n in continuity.notes[:_MAX_CONTINUITY_NOTES]
    ]


def _framing_notes(framing: FramingFacts | None, note_id: Callable[[], str]) -> list[PacingNote]:
    """Shots the framing analysis could not serve well: shown, never applied."""
    if framing is None:
        return []
    return [
        PacingNote(
            note_id=note_id(),
            level="framing",
            subject=s.strategy,
            detail=f"{s.recommendation_id} [{s.feasibility}]: {s.detail}",
        )
        for s in framing.spans
        if s.detail
    ][:_MAX_FRAMING_NOTES]


def _is_used(asset_id: str, placements: Sequence[Placement]) -> bool:
    return any(p.source.asset_id == asset_id for p in placements)


def _broll_notes(broll: BrollFacts | None, note_id: Callable[[], str]) -> list[PacingNote]:
    """B-roll that fits a place the story did not name as a visual: shown, never placed."""
    if broll is None:
        return []
    return [
        PacingNote(
            note_id=note_id(),
            level="broll",
            subject=n.need,
            detail=f"{n.detail} ({n.start:.1f}-{n.end:.1f} s of {n.asset_id})",
        )
        for n in broll.notes[:_MAX_BROLL_NOTES]
    ]


def _stages(
    evidence: Mapping[str, AssetEvidence],
    hooks: Hooks,
    pacing: PacingFacts | None,
    broll: BrollFacts | None,
    continuity: ContinuityFacts | None,
    framing: FramingFacts | None,
) -> list[StageReport]:
    video = any(e.video is not None for e in evidence.values())
    audio = any(e.audio is not None for e in evidence.values())
    word_level = any(e.audio is not None and e.audio.word_level for e in evidence.values())
    return [
        StageReport(
            stage="cut_points",
            state=StageState.OK if word_level else StageState.NOT_AVAILABLE,
            detail="word-level cut points"
            if word_level
            else "no forced-aligned words: boundaries rest on sentence times",
        ),
        StageReport(
            stage="trims",
            state=StageState.OK if audio else StageState.NOT_AVAILABLE,
            detail="" if audio else "no audio analysis: pauses and fillers were not assessed",
        ),
        StageReport(
            stage="coverage",
            state=StageState.OK if video else StageState.NOT_AVAILABLE,
            detail="" if video else "no video analysis: no cover footage can be qualified",
        ),
        StageReport(
            stage="continuity",
            state=StageState.OK if video else StageState.NOT_AVAILABLE,
            detail="" if video else "no video analysis: continuity was not assessed",
        ),
        StageReport(
            stage="advisors",
            state=StageState.OK if (hooks.coverage or hooks.continuity) else StageState.SKIPPED,
            detail=""
            if (hooks.coverage or hooks.continuity)
            else "none supplied: baseline rules only",
        ),
        StageReport(
            stage="pacing",
            state=StageState.OK if pacing is not None else StageState.NOT_AVAILABLE,
            detail=""
            if pacing is not None
            else "no pacing analysis: the editor's baseline pacing notes apply",
        ),
        StageReport(
            stage="broll",
            state=StageState.OK if broll is not None else StageState.NOT_AVAILABLE,
            detail=""
            if broll is not None
            else "no B-roll analysis: cover candidates come from the alignment's shots only",
        ),
        StageReport(
            stage="continuity_analysis",
            state=StageState.OK if continuity is not None else StageState.NOT_AVAILABLE,
            detail=""
            if continuity is not None
            else "no continuity analysis: cuts are judged by the editor's baseline rules",
        ),
        StageReport(
            stage="framing_analysis",
            state=StageState.OK if framing is not None else StageState.NOT_AVAILABLE,
            detail=""
            if framing is not None
            else "no framing analysis: clips carry no crop recommendation",
        ),
    ]


def _decision_sort(units: Sequence[EditUnit]) -> Callable[[Decision], tuple[int, int, str]]:
    position = {u.decision_id: u.order for u in units}

    def key(d: Decision) -> tuple[int, int, str]:
        return (
            position.get(d.decision_id, len(position)),
            0 if d.decision_id in position else 1,
            d.decision_id,
        )

    return key


def _confidence(
    decisions: Sequence[Decision],
    placements: Sequence[Placement],
    evidence: Mapping[str, AssetEvidence],
) -> Confidence:
    parts: list[tuple[str, float]] = []
    main = [p for p in placements if p.layer == 0 and p.track is TrackKind.AUDIO] or [
        p for p in placements if p.layer == 0
    ]
    known = [p.confidence for p in main if p.confidence is not None]
    if known:
        parts.append(("boundaries", round(sum(known) / len(known), 3)))
    if main:
        measured = sum(
            1
            for p in main
            if p.source.start_precision in _MEASURED and p.source.end_precision in _MEASURED
        )
        parts.append(("time_basis", round(measured / len(main), 3)))
    wanted = present = 0
    for e in evidence.values():
        wanted += 3 + (1 if e.source.has_video is not False else 0)
        present += sum(x is not None for x in (e.content, e.audio))
        if e.source.has_video is not False:
            present += sum(x is not None for x in (e.video, e.alignment))
    if wanted:
        parts.append(("evidence", round(present / wanted, 3)))
    edited = [d for d in decisions if d.action in (Action.KEEP, Action.CUT, Action.SHORTEN)]
    if edited:
        share = sum(1 for d in edited if d.executability is Executability.EXECUTABLE) / len(edited)
        parts.append(("executable", round(share, 3)))
    value = round(sum(v for _, v in parts) / len(parts), 3) if parts else 0.0
    return Confidence(value=value, components=tuple(parts))
