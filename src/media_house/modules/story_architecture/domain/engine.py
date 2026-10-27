"""Analysed passages in, one story plan out. Pure: no clock, no I/O, no model.

The steps, each in its own module: sections and beats -> dependencies and order -> claims and
issues -> the main message and openings -> visuals -> durations and pacing -> alternatives. A
language model, when one is used, only rewrites the wording of some statements afterwards
(``refine.py``); the structure, order, priorities and references never depend on it, so the
same analysis and configuration always give the same plan.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from media_house.modules.story_architecture.domain import (
    alternatives,
    openings,
    pacing,
    visuals,
)
from media_house.modules.story_architecture.domain.errors import NothingToPlan
from media_house.modules.story_architecture.domain.facts import AssetFacts, AssetLinkFacts
from media_house.modules.story_architecture.domain.integrity import Integrity
from media_house.modules.story_architecture.domain.links import apply_links
from media_house.modules.story_architecture.domain.model import (
    AssetRecord,
    AudienceIntent,
    Beat,
    CompletenessIssue,
    Confidence,
    DurationEstimate,
    InputRecord,
    NarrativePattern,
    Provenance,
    Section,
    SetupPayoffLink,
    StageReport,
    StoryPlan,
    VisualOpportunity,
)
from media_house.modules.story_architecture.domain.profile import (
    FormatProfile,
    StoryConfig,
    Thresholds,
)
from media_house.modules.story_architecture.domain.sections import (
    Draft,
    build_beats,
    build_drafts,
    mean,
    purpose_of,
    source_ref,
)
from media_house.modules.story_architecture.domain.structure import (
    Index,
    Structure,
    derive,
    order_sections,
    with_distance,
)
from media_house.modules.story_architecture.domain.values import (
    PLANNER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Basis,
    BeatFunction,
    ConstraintKind,
    DurationBasis,
    IssueKind,
    LinkKind,
    LinkState,
    Priority,
    ResultState,
    Severity,
    StageState,
    Strength,
)

_STRUCTURE_QUALITY = {"ok": 1.0, "ok_with_warnings": 0.8, "partial": 0.5}
_CENTRAL_ROLES = ("main_point", "conclusion", "resolution", "turning_point")


@dataclass(frozen=True, slots=True)
class PlanMeta:
    """Everything about the run that the analysis itself does not contain."""

    story_plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    configuration_fingerprint: str
    inputs: tuple[InputRecord, ...] = ()
    warnings: tuple[str, ...] = ()


LIMITATIONS: tuple[str, ...] = (
    "Claims are recorded as stated; none is verified against the world.",
    "'Unsupported' means no supporting passage was found in these recordings, not 'false'.",
    "Priorities, scores and confidence are advisory evidence from design-choice formulas.",
    "Section order changes only to give a dependency its context; chronology is not modelled.",
    "Several recordings are one story in the order given; the order between them is not inferred.",
    "Durations are as recorded (pauses included) or counted from words; they are not cut lengths.",
    "Exact keep, cut and timing decisions belong to the editing stage.",
)


def plan_story(
    assets: tuple[AssetFacts, ...],
    config: StoryConfig,
    meta: PlanMeta,
    asset_links: tuple[AssetLinkFacts, ...] = (),
) -> StoryPlan:
    profile = config.resolve()
    t = config.thresholds
    drafts = build_drafts(assets)
    if not drafts:
        raise NothingToPlan("the content analysis has no passages")
    index = Index(drafts, assets)
    beats_of = {d.section_id: build_beats(d) for d in drafts}
    all_beats = tuple(b for d in drafts for b in beats_of[d.section_id])

    structure = derive(index)
    order, changes = order_sections(index, structure, profile.reorder)
    position = {s: i for i, s in enumerate(order)}
    forward = [
        c
        for c in structure.constraints
        if c.kind is ConstraintKind.MUST_PRECEDE
        and c.strength is Strength.HARD
        and position[c.first] > position[c.second]
    ]

    integrity = Integrity(index, t)
    integrity.read_claims()
    integrity.find_unsupported()
    integrity.find_relations()
    links = with_distance(structure.links, order)
    integrity.find_open_links(links)
    integrity.check_order(order, structure, forward)
    effects = apply_links(asset_links, assets, drafts)
    for spec in effects.issues:
        integrity.issue(spec.kind, spec.severity, spec.detail, related=spec.related)

    message = openings.main_message(assets)
    premise = openings.find_premise(index, t)
    durations = {d.section_id: pacing.estimate_duration(d, profile) for d in drafts}
    _plan_level_issues(integrity, drafts, beats_of, durations, premise, profile, t)
    links = _with_payoff(links, premise, all_beats, order, index)

    candidates = openings.build_candidates(
        index,
        message=message,
        premise=premise,
        links=links,
        credibility=integrity.credibility(),
        profile=profile,
        thresholds=t,
    )
    chosen = openings.recommend(candidates)
    opportunities = visuals.build(index, all_beats, t)

    priorities = _priorities(drafts, structure, index, t, effects.redundant or {})
    sections = _sections(
        drafts,
        beats_of,
        priorities,
        durations,
        order,
        structure,
        integrity.issues,
        opportunities,
        profile.reorder.value,
        index,
    )
    duration = pacing.plan_duration(sections, profile, t)
    flagged = pacing.duration_issue(duration)
    if flagged is not None:
        integrity.issue(flagged[0], Severity.WARNING, flagged[1])
    notes = pacing.pacing_notes(sections, beats_of, tuple(integrity.issues), t)
    alternatives_ = alternatives.build(
        sections=sections,
        source_order=tuple(d.section_id for d in drafts),
        recommended_order=order,
        constraints=structure.constraints,
        openings=candidates,
        recommended=chosen,
        duration=duration,
        profile=profile,
        thresholds=t,
    )
    ordered_beats = tuple(b for s in order for b in beats_of[s])
    observed = tuple(dict.fromkeys(b.function for b in ordered_beats))
    state = _state(assets, meta)
    return StoryPlan(
        story_plan_id=meta.story_plan_id,
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=meta.project_id,
        created_at=meta.created_at,
        state=state,
        source_assets=tuple(
            AssetRecord(
                asset_id=a.asset_id,
                name=a.name,
                duration=a.duration,
                language=a.language,
                content_state=a.state,
            )
            for a in assets
        ),
        target_format=profile.name,
        audience_intent=AudienceIntent(
            format=profile.name, intent=profile.intent, audience=config.audience
        ),
        narrative_pattern=NarrativePattern(
            pattern=profile.pattern,
            observed=observed,
            source_order=tuple(d.section_id for d in drafts),
            recommended_order=order,
            changes=changes,
        ),
        main_message=message,
        premise=premise.statement if premise else None,
        candidate_openings=candidates,
        recommended_opening=chosen.candidate_id if chosen else None,
        sections=sections,
        beats=ordered_beats,
        key_information=tuple(integrity.information),
        supporting_evidence=tuple(integrity.evidence),
        ordering_constraints=structure.constraints,
        context_dependencies=structure.dependencies,
        setup_payoff_links=links,
        visual_opportunities=opportunities,
        pacing_notes=notes,
        estimated_duration=duration,
        completeness_issues=tuple(integrity.issues),
        asset_relations=effects.relations,
        alternative_structures=alternatives_,
        confidence=_confidence(assets, ordered_beats, sections, integrity),
        inputs=meta.inputs,
        stages=_stages(assets),
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            planner_version=PLANNER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            format=profile.name,
            format_version=profile.version,
            engines=(),
            prompt_version=None,
            configuration_fingerprint=meta.configuration_fingerprint,
        ),
        warnings=(*meta.warnings, *(w for a in assets for w in a.warnings)),
        limitations=LIMITATIONS,
    )


# --- priorities ------------------------------------------------------------------------------
def _priorities(
    drafts: tuple[Draft, ...],
    structure: Structure,
    index: Index,
    t: Thresholds,
    redundant_assets: Mapping[str, str],
) -> dict[str, tuple[Priority, tuple[str, ...], float | None]]:
    out: dict[str, tuple[Priority, list[str], float | None]] = {}
    repeats: dict[str, str] = {}
    for asset in index.assets:
        for rel in asset.relations:
            if rel.kind in ("repeats", "overlaps") and rel.adds is None:
                src = index.section_of(asset.asset_id, rel.source_segment)
                tgt = index.section_of(asset.asset_id, rel.target_segment)
                if src and tgt and src != tgt:
                    repeats.setdefault(src, index.draft(tgt).label)
    for d in drafts:
        importance = mean([s.importance for s in d.segments])
        removal = mean([s.removal for s in d.segments])
        central = any(s.has_role(*_CENTRAL_ROLES) for s in d.segments)
        if importance is not None and importance >= t.essential_importance:
            priority, why = (
                Priority.ESSENTIAL,
                f"central to the subject (importance {importance:.2f})",
            )
        elif central:
            priority, why = Priority.ESSENTIAL, "states the main point, a turn or the conclusion"
        elif importance is None:
            priority, why = Priority.SUPPORTING, "no importance score upstream"
        elif importance < t.optional_importance:
            priority, why = Priority.OPTIONAL, f"low importance ({importance:.2f})"
        else:
            priority, why = Priority.SUPPORTING, f"moderate importance ({importance:.2f})"
        reasons = [why]
        if (
            d.section_id in repeats
            and priority is not Priority.ESSENTIAL
            and removal is not None
            and removal >= t.redundant_removal
        ):
            priority = Priority.REDUNDANT_CANDIDATE
            reasons.append(f"repeats '{repeats[d.section_id]}' and adds nothing new")
        if d.asset_id in redundant_assets and priority is not Priority.REDUNDANT_CANDIDATE:
            priority = Priority.REDUNDANT_CANDIDATE
            reasons.append(f"its recording is {redundant_assets[d.asset_id]}")
        out[d.section_id] = (priority, reasons, importance)

    # What an essential section cannot be understood without is not optional either.
    changed = True
    while changed:
        changed = False
        for c in structure.constraints:
            if c.kind is not ConstraintKind.MUST_PRECEDE or c.strength is not Strength.HARD:
                continue
            need, reasons, imp = out[c.first]
            if out[c.second][0] is Priority.ESSENTIAL and need is not Priority.ESSENTIAL:
                reasons.append(f"needed to understand '{index.draft(c.second).label}'")
                out[c.first] = (Priority.ESSENTIAL, reasons, imp)
                changed = True
            elif (
                need in (Priority.OPTIONAL, Priority.REDUNDANT_CANDIDATE)
                and out[c.second][0] is not Priority.REDUNDANT_CANDIDATE
            ):
                reasons.append(f"'{index.draft(c.second).label}' depends on it")
                out[c.first] = (Priority.SUPPORTING, reasons, imp)
                changed = True
    return {k: (p, tuple(r), i) for k, (p, r, i) in out.items()}


# --- sections --------------------------------------------------------------------------------
def _sections(
    drafts: tuple[Draft, ...],
    beats_of: dict[str, tuple[Beat, ...]],
    priorities: dict[str, tuple[Priority, tuple[str, ...], float | None]],
    durations: dict[str, DurationEstimate],
    order: tuple[str, ...],
    structure: Structure,
    issues: list[CompletenessIssue],
    opportunities: tuple[VisualOpportunity, ...],
    policy: str,
    index: Index,
) -> tuple[Section, ...]:
    position = {s: i for i, s in enumerate(order)}
    requires: dict[str, list[str]] = {}
    for dep in structure.dependencies:
        requires.setdefault(dep.section_id, []).append(dep.requires_section_id)
    adjacent: dict[str, str] = {}
    for c in structure.constraints:
        if c.kind is ConstraintKind.KEEP_ADJACENT and c.strength is Strength.HARD:
            adjacent.setdefault(c.first, c.second)
            adjacent.setdefault(c.second, c.first)
    sections: list[Section] = []
    for d in drafts:
        beats = beats_of[d.section_id]
        priority, reasons, importance = priorities[d.section_id]
        i = position[d.section_id]
        locked: str | None = None
        if policy == "preserve":
            locked = "the format keeps the recorded order"
        elif d.section_id in adjacent:
            locked = f"has to stay next to '{index.draft(adjacent[d.section_id]).label}'"
        sections.append(
            Section(
                section_id=d.section_id,
                source_index=d.source_index,
                order=i,
                label=d.label,
                purpose=purpose_of(d, beats),
                summary=d.summary,
                priority=priority,
                priority_reasons=reasons,
                importance=importance,
                sources=(source_ref(d.asset_id, d.segments),),
                beat_ids=tuple(b.beat_id for b in beats),
                requires=tuple(dict.fromkeys(requires.get(d.section_id, ()))),
                before=order[i - 1] if i > 0 else None,
                after=order[i + 1] if i + 1 < len(order) else None,
                reorder_locked=locked,
                issue_ids=tuple(x.issue_id for x in issues if x.section_id == d.section_id),
                visual_ids=tuple(
                    v.visual_id for v in opportunities if v.section_id == d.section_id
                ),
                duration=durations[d.section_id],
                pacing=pacing.pacing_profile(d, beats, durations[d.section_id]),
            )
        )
    return tuple(sections)


# --- plan-level findings, links, confidence --------------------------------------------------
def _plan_level_issues(
    integrity: Integrity,
    drafts: tuple[Draft, ...],
    beats_of: dict[str, tuple[Beat, ...]],
    durations: dict[str, DurationEstimate],
    premise: openings.Premise | None,
    profile: FormatProfile,
    t: Thresholds,
) -> None:
    present = {b.function for d in drafts for b in beats_of[d.section_id]}
    for function in profile.expected:
        if function not in present:
            integrity.issue(
                IssueKind.MISSING_EXPECTED_ROLE,
                Severity.INFO,
                f"Stories in the '{profile.name}' format usually have a {function.value} step; "
                "none was found in the recording",
            )
    if premise is None:
        integrity.issue(
            IssueKind.MISSING_EXPECTED_ROLE,
            Severity.INFO,
            "No passage clearly states the main point",
        )
    elif (
        premise.delay_seconds is not None and premise.delay_seconds > profile.premise_window_seconds
    ):
        integrity.issue(
            IssueKind.DELAYED_PREMISE,
            Severity.WARNING,
            f"The point is first stated {premise.delay_seconds:.0f} s in; the format aims for "
            f"{profile.premise_window_seconds:.0f} s",
            section=premise.section_id,
            source=source_ref(premise.asset_id, (premise.segment,)),
        )
    untimed = [d for d in drafts if durations[d.section_id].basis is not DurationBasis.OBSERVED]
    if untimed:
        integrity.issue(
            IssueKind.UNTIMED_MATERIAL,
            Severity.INFO,
            f"{len(untimed)} of {len(drafts)} sections lack complete word times; "
            "their durations are counted from words",
        )
    _ = t


def _with_payoff(
    links: tuple[SetupPayoffLink, ...],
    premise: openings.Premise | None,
    beats: tuple[Beat, ...],
    order: tuple[str, ...],
    index: Index,
) -> tuple[SetupPayoffLink, ...]:
    """The premise and the conclusion that pays it off: an inferred link, said so."""
    if premise is None:
        return links
    position = {s: i for i, s in enumerate(order)}
    payoffs = [
        b
        for b in beats
        if b.function is BeatFunction.PAYOFF
        and position[b.section_id] > position[premise.section_id]
    ]
    setup = index.ref_of_segment(premise.asset_id, premise.segment.segment_id)
    if not payoffs or setup is None:
        return links
    payoff = max(payoffs, key=lambda b: position[b.section_id])
    link = SetupPayoffLink(
        link_id="",
        kind=LinkKind.SETUP_PAYOFF,
        state=LinkState.RESOLVED,
        setup=setup,
        setup_section=premise.section_id,
        payoff=payoff.source,
        payoff_section=payoff.section_id,
        distance=None,
        basis=Basis.INFERRED,
    )
    return with_distance((*links, link), order)


def _state(assets: tuple[AssetFacts, ...], meta: PlanMeta) -> ResultState:
    states = {a.state for a in assets}
    if "partial" in states:
        return ResultState.PARTIAL
    if "ok_with_warnings" in states or meta.warnings:
        return ResultState.WARNINGS
    return ResultState.OK


def _stages(assets: tuple[AssetFacts, ...]) -> tuple[StageReport, ...]:
    aligned = any(a.alignment_identity for a in assets)
    return (
        StageReport(stage="sections", state=StageState.OK),
        StageReport(stage="structure", state=StageState.OK),
        StageReport(stage="integrity", state=StageState.OK),
        StageReport(stage="openings", state=StageState.OK),
        StageReport(
            stage="visuals",
            state=StageState.OK if aligned else StageState.NOT_AVAILABLE,
            detail=""
            if aligned
            else "no alignment document: no shots are attached to the opportunities",
        ),
    )


def _confidence(
    assets: tuple[AssetFacts, ...],
    beats: tuple[Beat, ...],
    sections: tuple[Section, ...],
    integrity: Integrity,
) -> Confidence:
    components: list[tuple[str, float]] = [
        (
            "structure",
            round(mean([_STRUCTURE_QUALITY.get(a.state, 0.5) for a in assets]) or 0.0, 4),
        ),
        ("grounding", round(sum(b.basis is Basis.DIRECT for b in beats) / len(beats), 4)),
        (
            "timing",
            round(
                sum(s.duration.basis is DurationBasis.OBSERVED for s in sections) / len(sections),
                4,
            ),
        ),
    ]
    needing = [
        i
        for i in integrity.information
        if i.priority is Priority.ESSENTIAL and i.kind.value in ("claim", "stated_fact")
    ]
    if needing:
        components.append(
            ("evidence", round(sum(bool(i.support_ids) for i in needing) / len(needing), 4))
        )
    return Confidence(
        value=round(sum(v for _, v in components) / len(components), 4),
        components=tuple(components),
    )
