"""Plan the framing of every shot of the recordings and check how neighbouring shots sit together.

``plan_reframing`` is a pure function of its facts: the same facts, target and configuration give
the same analysis (ids included), so the application can use ``cache_key`` as the cache identity.
A shot whose fingerprint is unchanged since the previous analysis is carried over without planning
again (its recommendation keeps its id); a person's accept/reject follows the segment.

Neighbours: a crop is not judged alone. Two shots that may meet in a cut are compared by the size
and place of the main subject in the output and by headroom. The pairs come from the cuts a stored
continuity analysis judged (those are real) and, where none covers them, from recording order (an
ASSUMPTION the pair says it makes: the editing stage may join shots differently).
"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from itertools import pairwise

from media_house.modules.reframing_intelligence.domain.facts import (
    BeatHint,
    CutHint,
    Inputs,
    ShotFacts,
    SourceFacts,
    VisualNeed,
)
from media_house.modules.reframing_intelligence.domain.geometry import (
    SourceGeometry,
    TargetFormat,
    crop_around,
    widest_crop,
)
from media_house.modules.reframing_intelligence.domain.model import (
    Alternative,
    InputRecord,
    Issue,
    Metrics,
    PairCheck,
    Provenance,
    Recommendation,
    ReframingAnalysis,
    Score,
    SourceRecord,
    StageReport,
)
from media_house.modules.reframing_intelligence.domain.planner import failed_shot, plan_shot
from media_house.modules.reframing_intelligence.domain.profile import (
    ReframingConfig,
    ReframingProfile,
    SafeArea,
)
from media_house.modules.reframing_intelligence.domain.values import (
    ANALYZER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Decision,
    Feasibility,
    IssueKind,
    ResultState,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    Strategy,
)
from media_house.modules.reframing_intelligence.domain.verdict import ShotPlan, rational
from media_house.shared.errors import InvariantViolation

LIMITATIONS = (
    "Positions come from the picture analysis' tracks at its sampling rate: between observations "
    "the path is interpolated only across gaps no longer than the profile's limit, and the window "
    "is never claimed to follow frame by frame.",
    "No hands are tracked upstream: a demonstration that needs hands cannot protect them.",
    "Composition, continuity compatibility and reframing need are heuristics, not measurements of "
    "visual quality; thresholds are uncalibrated design choices.",
    "Head yaw is used for lead room only when a profile asks for it, and its sign convention is "
    "assumed (not validated upstream).",
    "A fill treatment, alternating crops and another shot are PROPOSALS: no stage here can render "
    "or execute them, and the editing stage decides.",
    "Validated on synthetic geometry only; no real footage, tracker or picture quality was "
    "evaluated.",
)
_STRIPPED = (IssueKind.ADJACENT_CROP, IssueKind.CONTINUITY_RISK)
_FRAMING_RANK = {"extreme_close_up": 0, "close_up": 1, "medium": 2, "wide": 3}
_FEASIBILITY_RANK = {Feasibility.FEASIBLE: 0, Feasibility.TRADEOFFS: 1}
_MAX_ALTERNATIVES = 3
#: A cut at ``leaves`` belongs to the shot that ends there, so look just before it.
_EPSILON = Fraction(1, 1000)


@dataclass(frozen=True, slots=True)
class AnalysisMeta:
    """What the application knows and the planner does not: history and how inputs were obtained."""

    analysis_version: int = 1
    supersedes: str | None = None
    project_id: str | None = None
    inputs: tuple[InputRecord, ...] = ()
    stages: tuple[StageReport, ...] = ()
    warnings: tuple[str, ...] = ()


# --- identity ----------------------------------------------------------------------------------
def digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _beats_for(asset_id: str, shot: ShotFacts, beats: tuple[BeatHint, ...]) -> tuple[BeatHint, ...]:
    return tuple(
        sorted(
            (
                b
                for b in beats
                if b.asset_id == asset_id
                and b.start is not None
                and b.end is not None
                and b.start < shot.end
                and b.end > shot.start
            ),
            key=lambda b: (b.start or Fraction(0), b.beat_id),
        )
    )


def _needs_for(
    asset_id: str, shot: ShotFacts, needs: tuple[VisualNeed, ...]
) -> tuple[VisualNeed, ...]:
    return tuple(
        sorted(
            (
                n
                for n in needs
                if n.asset_id == asset_id
                and n.start is not None
                and n.end is not None
                and n.start < shot.end
                and n.end > shot.start
            ),
            key=lambda n: n.visual_id,
        )
    )


def _track_digest(shot: ShotFacts) -> str:
    rows = [
        [
            t.track_id,
            t.kind,
            t.label,
            t.cluster,
            [
                [str(s.t), round(s.box.x0, 5), round(s.box.y0, 5), round(s.box.x1, 5),
                 round(s.box.y1, 5), round(s.confidence, 3)]
                for s in t.samples
            ],
        ]
        for t in sorted(shot.tracks, key=lambda t: t.track_id)
    ]  # fmt: skip
    return digest(rows)


def shot_fingerprint(
    source: SourceFacts,
    shot: ShotFacts,
    target: TargetFormat,
    config_fp: str,
    beats: tuple[BeatHint, ...],
    needs: tuple[VisualNeed, ...],
) -> str:
    g = source.geometry
    return digest(
        {
            "asset": source.asset_id,
            "shot": [shot.shot_id, shot.scene_id, str(shot.start), str(shot.end)],
            "tracks": _track_digest(shot),
            "main": shot.main_track_id,
            "evidence": [shot.framing, shot.usability, shot.head_yaw,
                         shot.attention, shot.retake_group],
            "geometry": [g.display_width, g.display_height, g.sample_width, g.sample_height,
                         g.rotation, g.exact],
            "target": [target.name, target.width, target.height],
            "config": config_fp,
            "beats": [[b.beat_id, b.function, str(b.start), str(b.end)] for b in beats],
            "needs": [[n.visual_id, n.purpose, n.concept, str(n.start), str(n.end)] for n in needs],
            "versions": [ANALYZER_VERSION, VOCABULARY_VERSION],
        }
    )  # fmt: skip


def cache_key(
    inputs: Inputs, target: TargetFormat, config: ReframingConfig, profile: ReframingProfile
) -> dict[str, object]:
    """Everything that can change the analysis, and nothing that cannot (time, job, device)."""
    config_fp = digest(config.fingerprint(profile))
    story = inputs.story
    beats = story.beats if story else ()
    needs = story.needs if story else ()
    return {
        "target": [target.name, target.aspect.numerator, target.aspect.denominator, target.width,
                   target.height],
        "config": config_fp,
        "sources": [
            {
                "asset": s.asset_id,
                "identity": s.identity,
                "clock": [str(s.clock_correction), s.corrected],
                "shots": [
                    shot_fingerprint(
                        s, shot, target, config_fp,
                        _beats_for(s.asset_id, shot, beats),
                        _needs_for(s.asset_id, shot, needs),
                    )
                    for shot in s.shots
                ],
            }
            for s in inputs.sources
        ],
        "story": None if story is None else [story.story_plan_id, story.plan_version],
        "continuity": None if inputs.continuity is None else inputs.continuity.identity,
        "decisions": sorted([k, v[0], v[1]] for k, v in inputs.decisions.items()),
        "versions": {
            "analyzer": ANALYZER_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
        },
    }  # fmt: skip


# --- planning ----------------------------------------------------------------------------------
def plan_reframing(
    inputs: Inputs,
    target: TargetFormat,
    config: ReframingConfig,
    *,
    now: datetime,
    previous: ReframingAnalysis | None = None,
    meta: AnalysisMeta | None = None,
) -> ReframingAnalysis:
    meta = meta or AnalysisMeta()
    story = inputs.story
    profile = config.resolve(story.story_format if story else None)
    key = cache_key(inputs, target, config, profile)
    config_fp = str(key["config"])
    beats = story.beats if story else ()
    needs = story.needs if story else ()
    carried = (
        {r.segment_key: r for r in previous.recommendations}
        if previous is not None
        and previous.target == target
        and previous.provenance.configuration_fingerprint == config_fp
        else {}
    )
    recs: list[Recommendation] = []
    reused = 0
    samples = 0
    skipped: list[Issue] = []
    for source in inputs.sources:
        for shot in source.shots:
            if shot.end <= shot.start:
                skipped.append(
                    Issue(
                        kind=IssueKind.INVALID_GEOMETRY,
                        severity=Severity.WARNING,
                        detail=f"{source.asset_id}:{shot.shot_id} has no duration: not planned",
                        subject=f"{source.asset_id}:{shot.shot_id}",
                    )
                )
                continue
            b = _beats_for(source.asset_id, shot, beats)
            n = _needs_for(source.asset_id, shot, needs)
            fp = shot_fingerprint(source, shot, target, config_fp, b, n)
            segment = f"{source.asset_id}:{shot.shot_id}"
            old = carried.get(segment)
            if old is not None and old.fingerprint == fp:
                recs.append(_stripped(old))
                reused += 1
                continue
            samples += sum(len(t.samples) for t in shot.tracks)
            try:
                plan = plan_shot(shot, source, target, profile, b, n, config.safe_areas)
            except InvariantViolation as exc:  # one bad shot must not cost the whole analysis
                plan = failed_shot(shot, source.geometry, target, profile, b, exc.user_message)
            recs.append(
                _recommendation(
                    source, shot, plan, fp, segment,
                    _alternatives(
                        source, shot, plan, target, profile, config.safe_areas, beats, needs
                    ),
                )
            )  # fmt: skip
    recs, pairs = _neighbours(recs, inputs, profile)
    recs = [_decided(r, inputs.decisions) for r in recs]

    metrics = Metrics(
        shots=len(recs),
        evaluated=len(recs) - reused,
        reused=reused,
        original=sum(r.strategy is Strategy.ORIGINAL for r in recs),
        static=sum(r.strategy in (Strategy.STATIC_CROP, Strategy.REPOSITIONED_CROP) for r in recs),
        dynamic=sum(
            r.strategy in (Strategy.DYNAMIC_CROP, Strategy.PUSH_IN, Strategy.PULL_OUT) for r in recs
        ),
        unreframeable=sum(
            r.strategy in (Strategy.UNREFRAMEABLE, Strategy.FILL_TREATMENT) for r in recs
        ),
        uncertain=sum(r.feasibility is Feasibility.UNCERTAIN for r in recs),
        samples=samples,
    )
    partial = metrics.uncertain > 0 or bool(skipped)
    top: list[Issue] = [
        Issue(
            kind=IssueKind.SOURCE_GEOMETRY,
            severity=Severity.INFO,
            detail=f"recording {s.asset_id}: no stored inspection gave the stored size and pixel "
            "shape, so the display size stands in for them",
        )
        for s in inputs.sources
        if not s.geometry.exact
    ]
    top += skipped
    return ReframingAnalysis(
        reframing_analysis_id=f"fr_{digest(key)[:16]}",
        analysis_version=meta.analysis_version,
        supersedes=meta.supersedes,
        project_id=meta.project_id,
        created_at=now,
        state=ResultState.PARTIAL if partial else ResultState.OK,
        target=target,
        profile=profile.name,
        safe_areas=config.safe_areas,
        sources=tuple(_source_record(s) for s in inputs.sources),
        story_plan_id=story.story_plan_id if story else None,
        story_plan_version=story.plan_version if story else None,
        recommendations=tuple(recs),
        pairs=tuple(pairs),
        metrics=metrics,
        issues=tuple(top),
        inputs=meta.inputs,
        stages=meta.stages or _default_stages(inputs),
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            analyzer_version=ANALYZER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=profile.version,
            configuration_fingerprint=config_fp,
            engines=(("reframing_planner", str(ANALYZER_VERSION)),),
        ),
        warnings=meta.warnings,
        limitations=LIMITATIONS,
    )


def _default_stages(inputs: Inputs) -> tuple[StageReport, ...]:
    return (
        StageReport(
            stage="story",
            state=StageState.OK if inputs.story else StageState.NOT_AVAILABLE,
            detail=""
            if inputs.story
            else "no story plan: no beat or visual need informed the crops",
        ),
        StageReport(
            stage="continuity_analysis",
            state=StageState.OK if inputs.continuity else StageState.NOT_AVAILABLE,
            detail=""
            if inputs.continuity
            else "no continuity analysis: neighbours are paired by recording order",
        ),
    )


def _source_record(s: SourceFacts) -> SourceRecord:
    g = s.geometry
    return SourceRecord(
        asset_id=s.asset_id,
        display_width=g.display_width,
        display_height=g.display_height,
        sample_width=g.sample_width,
        sample_height=g.sample_height,
        rotation=g.rotation,
        geometry_exact=g.exact,
        clock_correction_seconds=float(s.clock_correction),
        clock_corrected=s.corrected,
        shots=len(s.shots),
        analysis_identity=s.identity,
    )


def _recommendation(
    source: SourceFacts,
    shot: ShotFacts,
    plan: ShotPlan,
    fingerprint: str,
    segment: str,
    alternatives: tuple[Alternative, ...],
) -> Recommendation:
    return Recommendation(
        recommendation_id=f"fr_{fingerprint[:12]}",
        segment_key=segment,
        fingerprint=fingerprint,
        asset_id=source.asset_id,
        shot_id=shot.shot_id,
        scene_id=shot.scene_id,
        start=rational(shot.start),
        end=rational(shot.end),
        section_ids=plan.section_ids,
        beat_ids=plan.beat_ids,
        strategy=plan.strategy,
        camera_motion=plan.camera_motion,
        feasibility=plan.feasibility,
        executable=plan.executable,
        keyframes=plan.keyframes,
        elements=plan.elements,
        intervals=plan.intervals,
        scores=plan.scores,
        resolution=plan.resolution,
        confidence=plan.confidence,
        reasons=plan.reasons,
        tradeoffs=plan.tradeoffs,
        preferred_over=plan.preferred_over,
        alternatives=alternatives,
        issues=plan.issues,
        evidence=plan.evidence,
        subject_start=plan.subject_start,
        subject_end=plan.subject_end,
        subject_cluster=plan.subject_cluster,
    )


def _stripped(rec: Recommendation) -> Recommendation:
    """A carried-over recommendation without what the neighbour pass added (it is redone)."""
    return replace(
        rec,
        issues=tuple(i for i in rec.issues if i.kind not in _STRIPPED),
        scores=tuple(_score_without_pairs(s) for s in rec.scores),
    )


def _score_without_pairs(score: Score) -> Score:
    if score.name is not ScoreName.CONTINUITY_COMPATIBILITY:
        return score
    return Score(
        name=score.name,
        value=None,
        basis=score.basis,
        reason="no neighbouring shot was judged",
    )


def _decided(rec: Recommendation, decisions: dict[str, tuple[str, str]]) -> Recommendation:
    found = decisions.get(rec.segment_key)
    if found is None:
        return rec
    return replace(rec, decision=Decision(found[0]), decision_note=found[1])


# --- alternatives ------------------------------------------------------------------------------
def _alternatives(
    source: SourceFacts,
    shot: ShotFacts,
    plan: ShotPlan,
    target: TargetFormat,
    profile: ReframingProfile,
    safe_areas: tuple[SafeArea, ...],
    beats: tuple[BeatHint, ...],
    needs: tuple[VisualNeed, ...],
) -> tuple[Alternative, ...]:
    """What else could serve a shot the chosen crop serves badly or not at all. Only named and
    evaluated from the same picture analysis: nothing is searched in other recordings here."""
    unreframeable = plan.strategy in (Strategy.UNREFRAMEABLE, Strategy.FILL_TREATMENT)
    if not unreframeable and plan.feasibility is not Feasibility.LOW_QUALITY:
        return ()
    out: list[Alternative] = []
    if unreframeable and plan.omitted:
        out += _primary_only(source, shot, plan, target, profile, safe_areas, beats)
        out += _alternating(shot, source.geometry, target, profile)
    out += _other_shots(source, shot, target, profile, safe_areas, beats, needs)
    if not profile.allow_fill or not unreframeable:
        out.append(
            Alternative(
                strategy=Strategy.FILL_TREATMENT,
                summary="show the whole original frame inside the target frame with a fill "
                "(blurred or letterboxed background) instead of cropping",
                feasibility=None,
                executable=False,
                requires="a fill treatment: no stage can render one yet"
                + ("" if profile.allow_fill else " and the profile does not allow it"),
            )
        )
    return tuple(out)


def _primary_only(
    source: SourceFacts,
    shot: ShotFacts,
    plan: ShotPlan,
    target: TargetFormat,
    profile: ReframingProfile,
    safe_areas: tuple[SafeArea, ...],
    beats: tuple[BeatHint, ...],
) -> list[Alternative]:
    loose = replace(profile, participants=replace(profile.participants, required=False))
    alone = plan_shot(shot, source, target, loose, beats, (), safe_areas)
    if not alone.strategy.has_crop or not alone.keyframes:
        return []
    return [
        Alternative(
            strategy=alone.strategy,
            summary="keep only the main subject; "
            f"{', '.join(plan.omitted)} would leave the picture",
            feasibility=alone.feasibility,
            executable=False,
            requires=f"a decision to drop {', '.join(plan.omitted)}: it was required",
            crops=(alone.keyframes[0].crop,),
            omits=plan.omitted,
        )
    ]


def _alternating(
    shot: ShotFacts, geometry: SourceGeometry, target: TargetFormat, profile: ReframingProfile
) -> list[Alternative]:
    from media_house.modules.reframing_intelligence.domain.elements import select

    chosen = [
        c
        for c in select(shot, (), profile, widest_crop(geometry, target)).chosen
        if c.required and c.series.track.kind == "person"
    ]
    crops = []
    for c in sorted(chosen, key=lambda c: c.series.track.track_id):
        boxes = c.series.boxes
        cx = sorted(b.center[0] for b in boxes)[len(boxes) // 2]
        cy = sorted(b.center[1] for b in boxes)[len(boxes) // 2]
        crop = crop_around(geometry, target, 1.0, cx, cy).rounded()
        if all(crop.inside_share(b) >= 0.999 for b in boxes):
            crops.append(crop)
    if len(crops) < 2 or len(crops) != len(chosen):
        return []
    return [
        Alternative(
            strategy=Strategy.ALTERNATING_CROPS,
            summary="cut between one crop per person, each holding that person alone",
            feasibility=None,
            executable=False,
            requires="who speaks when: the picture analysis states speaking per shot, "
            "not per moment",
            crops=tuple(crops),
        )
    ]


def _other_shots(
    source: SourceFacts,
    shot: ShotFacts,
    target: TargetFormat,
    profile: ReframingProfile,
    safe_areas: tuple[SafeArea, ...],
    beats: tuple[BeatHint, ...],
    needs: tuple[VisualNeed, ...],
) -> list[Alternative]:
    own = _FRAMING_RANK.get(shot.framing or "")
    found: list[tuple[int, float, Alternative]] = []
    for other in source.shots:
        if other.shot_id == shot.shot_id:
            continue
        same_take = shot.retake_group is not None and other.retake_group == shot.retake_group
        rank = _FRAMING_RANK.get(other.framing or "")
        wider = (
            shot.scene_id is not None
            and other.scene_id == shot.scene_id
            and own is not None
            and rank is not None
            and rank > own
        )
        if not (wider or same_take):
            continue
        b = _beats_for(source.asset_id, other, beats)
        n = _needs_for(source.asset_id, other, needs)
        tried = plan_shot(other, source, target, profile, b, n, safe_areas)
        if tried.feasibility not in _FEASIBILITY_RANK or not tried.strategy.has_crop:
            continue
        kind = Strategy.ALTERNATIVE_RANGE if same_take else Strategy.WIDER_SOURCE
        why = (
            f"shot {other.shot_id} is another take of the same material"
            if same_take
            else f"shot {other.shot_id} of the same scene frames wider ({other.framing})"
        )
        found.append(
            (
                _FEASIBILITY_RANK[tried.feasibility],
                abs(float(other.start - shot.start)),
                Alternative(
                    strategy=kind,
                    summary=why,
                    feasibility=tried.feasibility,
                    executable=False,
                    requires="the editing stage choosing this moment: its picture is not this "
                    "passage's picture",
                    asset_id=source.asset_id,
                    shot_id=other.shot_id,
                    start=rational(other.start),
                    end=rational(other.end),
                    crops=(tried.keyframes[0].crop,),
                ),
            )
        )
    found.sort(key=lambda x: (x[0], x[1], x[2].shot_id or ""))
    return [a for _, _, a in found[:_MAX_ALTERNATIVES]]


# --- neighbours --------------------------------------------------------------------------------
def _neighbours(
    recs: list[Recommendation], inputs: Inputs, profile: ReframingProfile
) -> tuple[list[Recommendation], list[PairCheck]]:
    by_asset: dict[str, list[Recommendation]] = {}
    for r in recs:
        by_asset.setdefault(r.asset_id, []).append(r)
    for rows in by_asset.values():
        rows.sort(key=lambda r: (r.start.value, r.recommendation_id))
    pairs: list[tuple[Recommendation, Recommendation, str, CutHint | None]] = []
    seen: set[tuple[str, str]] = set()
    continuity = inputs.continuity
    if continuity is not None:
        for cut in continuity.cuts:
            first = _holding(by_asset.get(cut.out_asset, []), cut.leaves - _EPSILON)
            second = _holding(by_asset.get(cut.in_asset, []), cut.resumes)
            if first is None or second is None or first is second:
                continue
            ids = (first.recommendation_id, second.recommendation_id)
            if ids not in seen:
                seen.add(ids)
                pairs.append((first, second, f"continuity:{continuity.analysis_id}", cut))
    for rows in by_asset.values():
        for first, second in pairwise(rows):
            ids = (first.recommendation_id, second.recommendation_id)
            if ids not in seen:
                seen.add(ids)
                pairs.append((first, second, "recording_order", None))

    checks: list[PairCheck] = []
    scores: dict[str, list[float]] = {}
    added: dict[str, list[Issue]] = {}
    for first, second, origin, hint in pairs:
        check, issues = _compare(first, second, origin, hint, profile)
        checks.append(check)
        if check.score is not None:
            for r in (first, second):
                scores.setdefault(r.recommendation_id, []).append(check.score)
        added.setdefault(second.recommendation_id, []).extend(issues)
    out = []
    for r in recs:
        mine = scores.get(r.recommendation_id)
        out.append(
            replace(
                r,
                scores=tuple(
                    Score(
                        name=s.name,
                        value=round(min(mine), 4),
                        basis=ScoreBasis.HEURISTIC,
                    )
                    if mine and s.name is ScoreName.CONTINUITY_COMPATIBILITY
                    else s
                    for s in r.scores
                ),
                issues=(*r.issues, *added.get(r.recommendation_id, [])),
            )
        )
    return out, checks


def _holding(rows: list[Recommendation], moment: Fraction) -> Recommendation | None:
    return next(
        (r for r in rows if _fraction(r.start) <= moment < _fraction(r.end)),
        None,
    )


def _fraction(value: object) -> Fraction:
    return Fraction(getattr(value, "numerator"), getattr(value, "denominator"))  # noqa: B009


def _check(
    first: Recommendation,
    second: Recommendation,
    origin: str,
    risk: float | None,
    *,
    scale_ratio: float | None = None,
    shift: float | None = None,
    headroom_delta: float | None = None,
    effect: str = "unknown",
    score: float | None = None,
    notes: tuple[str, ...] = (),
) -> PairCheck:
    return PairCheck(
        first_id=first.recommendation_id,
        second_id=second.recommendation_id,
        scale_ratio=scale_ratio,
        shift=shift,
        headroom_delta=headroom_delta,
        origin=origin,
        jump_cut_risk=risk,
        jump_cut_effect=effect,
        score=score,
        notes=notes,
    )


def _compare(
    first: Recommendation,
    second: Recommendation,
    origin: str,
    cut: CutHint | None,
    profile: ReframingProfile,
) -> tuple[PairCheck, list[Issue]]:
    adj = profile.adjacency
    a, b = first.subject_end, second.subject_start
    issues: list[Issue] = []
    risk = None if cut is None else cut.risk
    if first.strategy is Strategy.PUSH_IN and second.strategy is Strategy.PUSH_IN:
        issues.append(
            Issue(
                kind=IssueKind.ADJACENT_CROP,
                severity=Severity.WARNING,
                detail="two push-ins in a row: repeated punch-ins read as a tic",
            )
        )
    if a is None or b is None:
        note = ("no main subject on one side: nothing to compare",)
        return _check(first, second, origin, risk, notes=note), issues
    different = (
        first.subject_cluster is not None
        and second.subject_cluster is not None
        and first.subject_cluster != second.subject_cluster
    )
    if different:
        note = ("different people: matching their framing is not the point",)
        return _check(first, second, origin, risk, notes=note), issues
    shift, headroom = abs(a[1] - b[1]), abs(a[2] - b[2])
    if min(a[0], b[0]) <= 0:
        note = ("a subject has no height",)
        return _check(first, second, origin, risk, shift=shift, notes=note), issues
    ratio = max(a[0], b[0]) / min(a[0], b[0])
    step = ratio >= adj.scale_ratio
    jump = cut is not None and risk is not None and (cut.jump_cut or risk >= 0.5)
    effect = "unknown" if not jump else ("hides" if step else "does_not_change")
    notes: list[str] = []
    if jump and step:
        notes.append("the size step is what hides the jump cut the continuity analysis found")
        issues.append(
            Issue(
                kind=IssueKind.ADJACENT_CROP,
                severity=Severity.INFO,
                detail=f"subject size steps x{ratio:.2f} across a cut judged a jump cut: the "
                "step hides it",
            )
        )
    elif jump:
        issues.append(
            Issue(
                kind=IssueKind.CONTINUITY_RISK,
                severity=Severity.WARNING,
                detail=f"the continuity analysis judged this cut a jump cut (risk {risk:.2f}) and "
                f"the crops change the subject's size only x{ratio:.2f} (a step of "
                f"x{adj.scale_ratio} or more reads as a deliberate cut)",
            )
        )
    elif step or shift >= adj.shift:
        issues.append(
            Issue(
                kind=IssueKind.ADJACENT_CROP,
                severity=Severity.WARNING,
                detail=f"the crops step the subject's size x{ratio:.2f} and its place by "
                f"{shift:.2f} of the picture across a cut (limits x{adj.scale_ratio} / "
                f"{adj.shift})",
            )
        )
    penalty = max(
        min(1.0, (ratio - 1.0) / (adj.scale_ratio - 1.0)),
        min(1.0, shift / adj.shift),
        min(1.0, headroom / 0.15),
    )
    return (
        _check(
            first, second, origin, risk,
            scale_ratio=round(ratio, 4), shift=round(shift, 4), headroom_delta=round(headroom, 4),
            effect=effect, score=round(1.0 - penalty, 4), notes=tuple(notes),
        ),
        issues,
    )  # fmt: skip
