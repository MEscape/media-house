"""The pacing engine: story plan (optional) + evidence in, one validated pacing analysis out.

Pure: no clock, I/O or model. For each strategy the same steps run on the same evidence:

1. passages and pauses (``speech``), shots, the pulse in the sound, the attention curve
   (``picture``);
2. the story's narrative pacing, only when a story plan exists (``narrative``);
3. recommendations (``recommend``), de-duplicated so none counts seconds another saved;
4. ranking and the runtime they imply, against the target if there is one.

The selected strategy's result is stored; the other four are summarised as projections, so the
caller sees what each would change without five full analyses. Missing evidence stays unknown and
is reported; nothing is filled in.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from media_house.modules.pacing_intelligence.domain.context import Context
from media_house.modules.pacing_intelligence.domain.errors import NothingToAnalyse
from media_house.modules.pacing_intelligence.domain.facts import AssetEvidence, StoryFacts
from media_house.modules.pacing_intelligence.domain.model import (
    DurationEstimate,
    InputRecord,
    Issue,
    PacingAnalysis,
    PassageAnalysis,
    PauseAnalysis,
    Provenance,
    Recommendation,
    RhythmFinding,
    ShotAnalysis,
    SourceRecord,
    StageReport,
    StoryRef,
    StrategyProjection,
    TargetCheck,
    TimelineAnalysis,
)
from media_house.modules.pacing_intelligence.domain.narrative import Narrative, analyse_narrative
from media_house.modules.pacing_intelligence.domain.picture import (
    analyse_cut_rate,
    analyse_rhythm,
    analyse_shots,
    analyse_timeline,
)
from media_house.modules.pacing_intelligence.domain.profile import (
    STRATEGIES,
    PacingConfig,
    PacingProfile,
    TargetDuration,
)
from media_house.modules.pacing_intelligence.domain.recommend import Findings, recommend
from media_house.modules.pacing_intelligence.domain.speech import (
    analyse_passage,
    analyse_pauses,
    windows_of,
)
from media_house.modules.pacing_intelligence.domain.validation import validate
from media_house.modules.pacing_intelligence.domain.values import (
    ANALYZER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    IssueKind,
    RecommendationType,
    ResultState,
    Severity,
    StageState,
    Strategy,
)

LIMITATIONS: tuple[str, ...] = (
    "Scores are heuristic or derived from upstream measurements; none is a calibrated probability "
    "or "
    "a validated measure of cognitive load.",
    "The attention curve is a prediction from cuts, emphasis, new information and demonstrations; "
    "no audience data was used.",
    "Information density comes from content analysis (word-level features): it does not understand "
    "meaning, and a short passage has none.",
    "The pulse in the sound is read from the loudness envelope of stretches without speech; it is "
    "not "
    "music analysis and does not see music under speech.",
    "Recordings share no clock: the story-wide runtime is a sum of lengths, not a timeline.",
    "Recommendations are pacing statements; which edit applies them is the editing stage's "
    "decision.",
)
_UNESTIMATED_SECONDS = 2.0
_UNKNOWN_CONFIDENCE = 0.3
_CONTRACTIONS = frozenset(
    {RecommendationType.TIGHTEN_DELIVERY, RecommendationType.REDUCE_REPETITION}
)


@dataclass(frozen=True, slots=True)
class PlanMeta:
    pacing_analysis_id: str
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    configuration_fingerprint: str
    story: StoryRef | None = None
    inputs: tuple[InputRecord, ...] = ()
    warnings: tuple[str, ...] = ()
    upstream: tuple[tuple[str, str], ...] = ()
    stages: tuple[StageReport, ...] = ()


@dataclass(slots=True)
class _Run:
    ctx: Context
    passages: list[PassageAnalysis]
    pauses: list[PauseAnalysis]
    shots: list[ShotAnalysis]
    rhythm: list[RhythmFinding]
    narrative: Narrative
    timeline: list[TimelineAnalysis]
    recommendations: list[Recommendation]


def analyse_pacing(
    story: StoryFacts | None,
    evidence: Mapping[str, AssetEvidence],
    asset_order: tuple[str, ...],
    config: PacingConfig,
    meta: PlanMeta,
) -> PacingAnalysis:
    profile = config.resolve(story.target_format if story else None)
    runs = {s: _run(story, evidence, asset_order, profile, s) for s in Strategy}
    selected = runs[config.strategy]
    if not selected.passages and not selected.shots:
        raise NothingToAnalyse("no speech passages and no shots")
    current, basis = _current_runtime(selected, evidence, asset_order)
    merged = _merge_strategies(runs)
    ranked = _rank(selected.ctx, merged[config.strategy], current, config.target)
    story_based = selected.narrative.total_seconds is not None
    contraction, expansion = _changes(ranked, story_based)
    proposed = None if current is None else current - contraction + expansion
    target = _target_check(config.target, proposed, ranked, current)
    projections = tuple(
        _projection(s, merged[s], current, config.target, story_based) for s in Strategy
    )
    issues = list(_evidence_issues(story, evidence, asset_order))
    if target is not None and target.status != "within":
        issues.append(
            Issue(
                issue_id="",
                kind=IssueKind.TARGET,
                severity=Severity.WARNING,
                detail=target.note,
            )
        )
    analysis = PacingAnalysis(
        pacing_analysis_id=meta.pacing_analysis_id,
        analysis_version=meta.analysis_version,
        supersedes=meta.supersedes,
        project_id=meta.project_id,
        created_at=meta.created_at,
        state=ResultState.OK,
        profile=profile.name,
        strategy=config.strategy,
        story=meta.story,
        sources=tuple(_source_record(a, evidence[a]) for a in asset_order if a in evidence),
        passages=tuple(selected.passages),
        pauses=tuple(selected.pauses),
        shots=tuple(selected.shots),
        sections=tuple(selected.narrative.sections),
        timeline=tuple(selected.timeline),
        rhythm=tuple(selected.rhythm),
        recommendations=tuple(ranked),
        duration=DurationEstimate(
            source_seconds=_source_seconds(evidence, asset_order),
            current_seconds=None if current is None else round(current, 3),
            current_basis=basis,
            proposed_seconds=None if proposed is None else round(proposed, 3),
            contraction_seconds=round(contraction, 3),
            expansion_seconds=round(expansion, 3),
            assumptions=(
                "a recommendation's change is the sum of the seconds it names; speech is never "
                "sped up",
                "recordings are added, not placed on a shared clock",
                "nothing has been edited: this is the runtime if every recommendation were applied",
            ),
            target=target,
        ),
        strategy_projections=projections,
        summary=_summary(selected, ranked, current, proposed),
        issues=(),
        inputs=meta.inputs,
        stages=(*meta.stages, *_stages(story, evidence)),
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            analyzer_version=ANALYZER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=profile.version,
            strategy=config.strategy,
            configuration_fingerprint=meta.configuration_fingerprint,
            upstream=meta.upstream,
        ),
        warnings=meta.warnings,
        limitations=LIMITATIONS,
    )
    ids = iter(f"i{n:03d}" for n in range(1, 10_000))
    found = [replace(i, issue_id=next(ids)) for i in issues]
    found += [replace(i, issue_id=next(ids)) for i in validate(analysis, evidence)]
    state = _state(found, selected, evidence, asset_order)
    return replace(analysis, issues=tuple(found), state=state)


def _run(
    story: StoryFacts | None,
    evidence: Mapping[str, AssetEvidence],
    asset_order: tuple[str, ...],
    profile: PacingProfile,
    strategy: Strategy,
) -> _Run:
    ctx = Context(
        profile=profile,
        adjust=STRATEGIES[strategy],
        strategy=strategy,
        assets=evidence,
        story=story,
        asset_order=asset_order,
    )
    passages = [analyse_passage(ctx, w) for w in windows_of(ctx)]
    pauses = analyse_pauses(ctx)
    shots = analyse_shots(ctx)
    rhythm = analyse_rhythm(ctx)
    narrative = analyse_narrative(ctx, passages)
    timeline = [analyse_timeline(ctx, a, passages) for a in asset_order if a in evidence]
    found = Findings(passages, pauses, shots, rhythm, narrative, timeline, analyse_cut_rate(ctx))
    recs = _deduplicate(recommend(ctx, found))
    return _Run(ctx, passages, pauses, shots, rhythm, narrative, timeline, recs)


def _deduplicate(recs: list[Recommendation]) -> list[Recommendation]:
    """Seconds saved inside a passage by a pause recommendation are not counted again by the
    passage's own saving. A passage left with less than a second to save has nothing to add."""
    pauses = [r for r in recs if r.type is RecommendationType.TIGHTEN_PAUSE]
    out: list[Recommendation] = []
    for rec in recs:
        if rec.type in _CONTRACTIONS and rec.delta_seconds is not None:
            inside = sum(
                -(p.delta_seconds or 0.0)
                for p in pauses
                if p.target.asset_id == rec.target.asset_id
                and p.target.start.value >= rec.target.start.value
                and p.target.end.value <= rec.target.end.value
            )
            remaining = -rec.delta_seconds - inside
            if remaining < 1.0:
                continue
            rec = replace(
                rec,
                delta_seconds=round(-remaining, 3),
                recommended_duration=None
                if rec.recommended_duration is None
                else round(rec.target.seconds - remaining, 3),
            )
        out.append(rec)
    return out


def _merge_strategies(runs: Mapping[Strategy, _Run]) -> dict[Strategy, list[Recommendation]]:
    """Each recommendation lists every strategy under which the same one applies."""
    where: dict[str, list[Strategy]] = {}
    for strategy, run in runs.items():
        for rec in run.recommendations:
            where.setdefault(rec.recommendation_id, []).append(strategy)
    return {
        s: [replace(r, strategies=tuple(where[r.recommendation_id])) for r in run.recommendations]
        for s, run in runs.items()
    }


# --- ranking, runtime and target ------------------------------------------------------------
def _delta(rec: Recommendation) -> float:
    return rec.delta_seconds or 0.0


def _changes(recs: Sequence[Recommendation], story_based: bool) -> tuple[float, float]:
    """Seconds the recommendations would remove and add.

    When the current runtime is the story's own length, only recommendations inside a story
    section change it: material the story left out is not part of that runtime.
    """
    counted = [r for r in recs if not story_based or r.section_id is not None]
    return (
        -sum(_delta(r) for r in counted if _delta(r) < 0),
        sum(_delta(r) for r in counted if _delta(r) > 0),
    )


def _weight(rec: Recommendation) -> float:
    seconds = abs(rec.delta_seconds) if rec.delta_seconds else _UNESTIMATED_SECONDS
    return seconds * (rec.confidence if rec.confidence is not None else _UNKNOWN_CONFIDENCE)


def _rank(
    ctx: Context,
    recs: list[Recommendation],
    current: float | None,
    target: TargetDuration | None,
) -> list[Recommendation]:
    shorten = (
        target is not None
        and current is not None
        and (target.maximum is not None and current > target.maximum)
    )
    lengthen = (
        target is not None
        and current is not None
        and (target.minimum is not None and current < target.minimum)
    )

    def key(rec: Recommendation) -> tuple[float, str]:
        weight = _weight(rec)
        delta = rec.delta_seconds or 0.0
        if (shorten and delta < 0) or (lengthen and delta > 0):
            weight *= 2.0
        return (-weight, rec.recommendation_id)

    basis = "affected seconds (2 s when not estimable) x confidence"
    if shorten:
        basis += "; shortening doubled because the material exceeds the target"
    if lengthen:
        basis += "; lengthening doubled because the material is under the target"
    ordered = sorted(recs, key=key)
    needed = 0.0
    if target is not None and current is not None:
        if shorten and target.maximum is not None:
            needed = current - target.maximum
        elif lengthen and target.minimum is not None:
            needed = target.minimum - current
    out: list[Recommendation] = []
    gained = 0.0
    for number, rec in enumerate(ordered, 1):
        delta = rec.delta_seconds or 0.0
        helps = (
            needed > 0 and gained < needed and ((shorten and delta < 0) or (lengthen and delta > 0))
        )
        if helps:
            gained += abs(delta)
        out.append(replace(rec, priority=number, rank_basis=basis, contributes_to_target=helps))
    return out


def _current_runtime(
    run: _Run, evidence: Mapping[str, AssetEvidence], order: tuple[str, ...]
) -> tuple[float | None, str]:
    if run.narrative.total_seconds is not None:
        return run.narrative.total_seconds, "story plan section lengths"
    seconds = [t.seconds for t in run.timeline if t.seconds is not None]
    if seconds and len(seconds) == len([a for a in order if a in evidence]):
        return sum(seconds), "recording lengths added"
    return None, "unknown: no section lengths and not every recording has a length"


def _source_seconds(evidence: Mapping[str, AssetEvidence], order: tuple[str, ...]) -> float | None:
    values = [evidence[a].source.duration for a in order if a in evidence]
    known = [v for v in values if v is not None]
    return sum(known) if known and len(known) == len(values) else None


def _target_check(
    target: TargetDuration | None,
    proposed: float | None,
    recs: list[Recommendation],
    current: float | None,
) -> TargetCheck | None:
    if target is None or proposed is None or current is None:
        return None
    status, shortfall, note = "within", 0.0, "the proposed runtime is inside the target"
    if target.maximum is not None and proposed > target.maximum + 0.5:
        shortfall = proposed - target.maximum
        status = "over"
        note = (
            f"even with every recommendation the material runs {shortfall:.1f} s over the "
            f"{target.maximum:.0f} s maximum: the rest would mean removing content (or faster "
            "speech, "
            "which is not recommended)"
        )
    elif target.minimum is not None and proposed < target.minimum - 0.5:
        shortfall = target.minimum - proposed
        status = "under"
        note = (
            f"the material runs {shortfall:.1f} s under the {target.minimum:.0f} s minimum; "
            "nothing is added just to fill time"
        )
    elif (target.preferred_high is not None and proposed > target.preferred_high + 0.5) or (
        target.preferred_low is not None and proposed < target.preferred_low - 0.5
    ):
        note = "inside the limits but outside the preferred range"
    return TargetCheck(
        minimum=target.minimum,
        maximum=target.maximum,
        preferred_low=target.preferred_low,
        preferred_high=target.preferred_high,
        status=status,
        shortfall_seconds=round(shortfall, 3),
        note=note,
    )


def _projection(
    strategy: Strategy,
    recs: list[Recommendation],
    current: float | None,
    target: TargetDuration | None,
    story_based: bool,
) -> StrategyProjection:
    contraction, expansion = _changes(recs, story_based)
    proposed = None if current is None else current - contraction + expansion
    meets: bool | None = None
    if target is not None and proposed is not None:
        meets = (target.maximum is None or proposed <= target.maximum + 0.5) and (
            target.minimum is None or proposed >= target.minimum - 0.5
        )
    return StrategyProjection(
        strategy=strategy,
        recommendations=len(recs),
        contraction_seconds=round(contraction, 3),
        expansion_seconds=round(expansion, 3),
        proposed_seconds=None if proposed is None else round(proposed, 3),
        meets_target=meets,
    )


# --- reporting ------------------------------------------------------------------------------
def _summary(
    run: _Run, recs: Sequence[Recommendation], current: float | None, proposed: float | None
) -> str:
    kinds = Counter(r.type.value for r in recs)
    parts = [
        ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(kinds.items()))
        or "no recommendations"
    ]
    if current is not None and proposed is not None:
        parts.append(f"runtime {current:.0f} s -> {proposed:.0f} s if all were applied")
    parts.extend(t.summary for t in run.timeline)
    return "; ".join(parts)


def _source_record(asset_id: str, e: AssetEvidence) -> SourceRecord:
    present = tuple(
        name
        for name, found in (
            ("audio", e.audio),
            ("content", e.content),
            ("video", e.video),
            ("alignment", e.alignment),
        )
        if found is not None
    )
    return SourceRecord(
        asset_id=asset_id,
        name=e.source.name,
        duration=e.source.duration,
        frame_rate=e.source.frame_rate,
        evidence=present,
    )


def _evidence_issues(
    story: StoryFacts | None, evidence: Mapping[str, AssetEvidence], order: tuple[str, ...]
) -> list[Issue]:
    out: list[Issue] = []

    def missing(kind: IssueKind, detail: str, subject: str | None = None) -> None:
        out.append(
            Issue(
                issue_id="", kind=kind, severity=Severity.UNRESOLVED, detail=detail, subject=subject
            )
        )

    if story is None:
        missing(
            IssueKind.NARRATIVE_LIMITED,
            "no story plan: narrative pacing (premise, setup, reveals, section breaths) was not "
            "analysed",
        )
    for asset_id in order:
        e = evidence.get(asset_id)
        if e is None:
            continue
        if e.audio is None:
            missing(
                IssueKind.EVIDENCE_MISSING,
                f"{asset_id}: no audio analysis: speech pacing not analysed",
                asset_id,
            )
        elif not e.audio.word_level:
            missing(
                IssueKind.TIMING,
                f"{asset_id}: word times are estimated, so rates and pause findings are less "
                "certain",
                asset_id,
            )
        if e.content is None:
            missing(
                IssueKind.EVIDENCE_MISSING,
                f"{asset_id}: no content analysis: density, load and repetition are unknown",
                asset_id,
            )
        if e.source.has_video is not False and e.video is None:
            missing(
                IssueKind.EVIDENCE_MISSING,
                f"{asset_id}: no video analysis: visual rhythm and cut-rate findings are "
                "unavailable",
                asset_id,
            )
        if e.source.duration is None:
            missing(
                IssueKind.EVIDENCE_MISSING,
                f"{asset_id}: no recording length is known: ranges cannot be checked against the "
                "end",
                asset_id,
            )
    return out


def _stages(story: StoryFacts | None, evidence: Mapping[str, AssetEvidence]) -> list[StageReport]:
    def state(ok: bool) -> StageState:
        return StageState.OK if ok else StageState.NOT_AVAILABLE

    audio = any(e.audio is not None for e in evidence.values())
    return [
        StageReport(
            stage="speech", state=state(audio), detail="" if audio else "no audio analysis"
        ),
        StageReport(
            stage="information",
            state=state(any(e.content is not None for e in evidence.values())),
            detail="density and repetition come from content analysis",
        ),
        StageReport(
            stage="picture",
            state=state(any(e.video is not None for e in evidence.values())),
            detail="shot rhythm needs video analysis",
        ),
        StageReport(
            stage="narrative",
            state=state(story is not None),
            detail="" if story else "no story plan",
        ),
        StageReport(
            stage="rhythm",
            state=state(any(e.audio is not None and e.audio.rhythm for e in evidence.values())),
            detail="a pulse in the sound was found only in stretches without speech",
        ),
    ]


def _state(
    issues: list[Issue], run: _Run, evidence: Mapping[str, AssetEvidence], order: tuple[str, ...]
) -> ResultState:
    if any(e.audio is None for e in evidence.values()) or not run.passages:
        return ResultState.PARTIAL
    if any(i.severity in (Severity.WARNING, Severity.UNRESOLVED) for i in issues):
        return ResultState.WARNINGS
    return ResultState.OK


__all__ = ["LIMITATIONS", "PlanMeta", "analyse_pacing"]
