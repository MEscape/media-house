"""The B-roll analysis: passages in, ranked evidence-backed footage recommendations out. Pure.

For each passage that needs supplementary footage (``need``), in order of importance so the most
important passage gets first pick of the footage:

1. **candidates**: the shots the words found (``retrieve``) plus the shots the picture embedding
   found (``RetrievalFacts``); nothing else is looked at;
2. **assess** each one on its own observations (``match``): association, identity, role, refusals;
3. **range**: a bounded source range inside the shot, from the moments that carry the evidence;
4. **score**: independent scores (``scoring``); **gate**: does it satisfy what the need requires
   (``select.support_for``); anything below the profile's quality threshold is refused;
5. **choose**: drop repeats, rank satisfying candidates first, keep a few labelled illustrative
   alternatives; with no satisfying candidate the passage is ``missing_coverage`` and, where it
   matters, a ``CoverageGap`` that says why.

No model runs here and nothing is read from disk: the embedding similarities arrive as facts.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.broll_intelligence.domain.facts import (
    Match,
    PoolFact,
    RetrievalFacts,
    ShotFact,
    TargetFact,
)
from media_house.modules.broll_intelligence.domain.gaps import build_gap, wants_gap
from media_house.modules.broll_intelligence.domain.match import Assessment, assess
from media_house.modules.broll_intelligence.domain.model import (
    BrollAnalysis,
    Candidate,
    EvidenceRef,
    InputRecord,
    NoChange,
    PoolRecord,
    Provenance,
    Recommendation,
    Score,
    SourceInterval,
    StageReport,
    StoryRef,
    SuggestedPlacement,
)
from media_house.modules.broll_intelligence.domain.need import NeedDecision, decide
from media_house.modules.broll_intelligence.domain.profile import BrollConfig, BrollProfile
from media_house.modules.broll_intelligence.domain.ranges import Chosen, choose_ranges
from media_house.modules.broll_intelligence.domain.scoring import (
    audio_plan,
    overall,
    score_candidate,
    use_for,
)
from media_house.modules.broll_intelligence.domain.select import (
    Chosen as Taken,
)
from media_house.modules.broll_intelligence.domain.select import (
    duplicate_of,
    novelty_of,
    support_for,
)
from media_house.modules.broll_intelligence.domain.validation import validate
from media_house.modules.broll_intelligence.domain.values import (
    ANALYZER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    AudioBehavior,
    NeedKind,
    Precision,
    Priority,
    ResultState,
    ScoreBasis,
    ScoreName,
    StageState,
    Support,
    UseKind,
)

_PRIORITY_RANK = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
_MAX_REFUSED = 5
_PASSAGE_CHARS = 240
LIMITATIONS = (
    "Validated on synthetic footage and hand-built analyses; no real footage or real "
    "vision/text model has been evaluated, and every threshold is an uncalibrated design choice.",
    "A shot is judged on what its analysis observed (labels, text, a scene description, an "
    "embedding); an unobserved fact is unknown, so footage that is right but unlabelled can be "
    "missed and a wrong description can mislead.",
    "Source ranges are estimates from the analysis's events and shot boundaries; only a whole "
    "shot has frame-exact ends. The final in/out is the editing stage's decision.",
    "Continuity compatibility is a basic comparison of environment and light, not a continuity "
    "assessment, and synchronisation of a shot's own sound is only claimed from its own alignment.",
)


@dataclass(frozen=True, slots=True)
class AnalysisMeta:
    broll_analysis_id: str
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    configuration_fingerprint: str
    story: StoryRef | None
    story_format: str | None
    sources: tuple[str, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    warnings: tuple[str, ...]
    pool_identity: str


@dataclass(slots=True)
class _Draft:
    shot: ShotFact
    assessment: Assessment
    chosen: Chosen
    use: UseKind
    audio: AudioBehavior
    audio_reason: str
    audio_score: Score
    scores: tuple[Score, ...]
    overall: float | None
    overall_basis: str
    support: Support
    support_reason: str
    duplicate_of: str | None = None
    reused: bool = False

    @property
    def tier(self) -> int:
        return {Support.SATISFIES: 0, Support.ILLUSTRATIVE_ONLY: 1, Support.REJECTED: 2}[
            self.support
        ]

    @property
    def order(self) -> tuple[int, float, str, Fraction]:
        return (self.tier, -(self.overall or 0.0), self.shot.key, self.chosen.start)


def rational(value: Fraction) -> Rational:
    value = max(Fraction(0), value)
    return Rational(value.numerator, value.denominator)


def analyse_broll(
    targets: Sequence[TargetFact],
    shots: Sequence[ShotFact],
    retrieval: RetrievalFacts,
    lexical: Mapping[str, Sequence[str]],
    pool: PoolFact,
    pool_identity: str,
    config: BrollConfig,
    meta: AnalysisMeta,
) -> BrollAnalysis:
    profile = config.resolve(meta.story_format)
    by_key = {s.key: s for s in shots}
    decisions = [(t, decide(t, profile)) for t in targets]
    no_change = tuple(_no_change(t, d) for t, d in decisions if d.kind is NeedKind.NO_VISUAL_NEEDED)
    needs = [(t, d) for t, d in decisions if d.kind is not NeedKind.NO_VISUAL_NEEDED]
    needs.sort(
        key=lambda item: (
            _PRIORITY_RANK[item[1].priority],
            -(item[0].strength or 0.0),
            item[0].start,
        )
    )
    if config.max_targets is not None:
        needs = needs[: config.max_targets]
    angles = frozenset(
        s.asset_id
        for s in shots
        if any(
            r.kind in ("alternate_take_of", "temporally_overlaps_with")
            and r.certainty == "confirmed"
            for r in s.relations
        )
    )
    used: dict[str, list[tuple[Fraction, Fraction]]] = {}
    recommendations: list[Recommendation] = []
    gaps = []
    for target, need in needs:
        keys = _candidate_keys(target, lexical, retrieval, profile)
        recommendation, assessed, refused, kept = _recommend(
            target, need, [by_key[k] for k in keys if k in by_key], retrieval, profile, used, angles
        )
        gap = None
        if recommendation.missing_coverage and wants_gap(target, need, profile):
            gap = build_gap(
                target,
                need,
                recommendation.recommendation_id,
                recommendation.placement.target_start,
                recommendation.placement.target_end,
                pool,
                assessed,
                kept,
                refused,
                retrieval.visual_model is not None,
            )
            recommendation = _with_gap(recommendation, gap.gap_id)
            gaps.append(gap)
        recommendations.append(recommendation)
    recommendations.sort(
        key=lambda r: (
            r.placement.target_start.numerator / r.placement.target_start.denominator,
            r.recommendation_id,
        )
    )
    stages = [*meta.stages, StageReport(stage="rank", state=StageState.OK)]
    notes = list(retrieval.notes)
    state = ResultState.PARTIAL if notes or pool.skipped or pool.limits else ResultState.OK
    result = BrollAnalysis(
        broll_analysis_id=meta.broll_analysis_id,
        analysis_version=meta.analysis_version,
        supersedes=meta.supersedes,
        project_id=meta.project_id,
        created_at=meta.created_at,
        state=state,
        story=meta.story,
        profile=profile.name,
        sources=meta.sources,
        pool=PoolRecord(
            assets=pool.assets,
            with_shots=pool.with_shots,
            shots=pool.shots,
            shots_with_embedding=pool.shots_with_embedding,
            identity=pool_identity,
            skipped=pool.skipped,
            limits=pool.limits,
        ),
        recommendations=tuple(recommendations),
        gaps=tuple(gaps),
        no_change=no_change,
        inputs=meta.inputs,
        stages=tuple(stages),
        issues=(),
        provenance=Provenance(
            analyzer_version=ANALYZER_VERSION,
            schema_version=SCHEMA_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile_name=profile.name,
            profile_version=profile.version,
            configuration_fingerprint=meta.configuration_fingerprint,
            visual_model=retrieval.visual_model,
            text_model=retrieval.text_model,
        ),
        warnings=(*meta.warnings, *notes),
        limitations=LIMITATIONS if meta.story is not None else (*LIMITATIONS, _NO_STORY),
    )
    return validated(result)


_NO_STORY = (
    "No story plan was available: narrative relevance is not judged and the places were taken from "
    "the content analysis and the pacing/continuity evidence only."
)


def validated(analysis: BrollAnalysis) -> BrollAnalysis:
    return replace(analysis, issues=tuple(validate(analysis)))


def _no_change(target: TargetFact, need: NeedDecision) -> NoChange:
    return NoChange(
        target_id=target.target_id,
        asset_id=target.asset_id,
        origin=target.origin,
        start=rational(Fraction(target.start).limit_denominator(1_000_000)),
        end=rational(Fraction(target.end).limit_denominator(1_000_000)),
        reason_code=need.no_change_code,
        reason=need.reasons[0] if need.reasons else "",
    )


def _candidate_keys(
    target: TargetFact,
    lexical: Mapping[str, Sequence[str]],
    retrieval: RetrievalFacts,
    profile: BrollProfile,
) -> list[str]:
    keys = dict.fromkeys(lexical.get(target.target_id, ()))
    visual = sorted(
        (
            (m.visual_z if m.visual_z is not None else (m.visual_similarity or 0.0), key)
            for (tid, key), m in retrieval.matches.items()
            if tid == target.target_id and m.visual_similarity is not None
        ),
        reverse=True,
    )
    for _, key in visual:
        keys.setdefault(key, None)
    return list(keys)[: profile.max_candidates * 2]


def _recommend(
    target: TargetFact,
    need: NeedDecision,
    shots: Sequence[ShotFact],
    retrieval: RetrievalFacts,
    profile: BrollProfile,
    used: dict[str, list[tuple[Fraction, Fraction]]],
    angles: frozenset[str],
) -> tuple[Recommendation, int, list[Candidate], list[Candidate]]:
    drafts: list[_Draft] = []
    for shot in shots:
        drafts.extend(
            _drafts(
                target, need, shot, retrieval.matches.get((target.target_id, shot.key)), profile
            )
        )
    drafts.sort(key=lambda d: d.order)
    taken = Taken()
    kept: list[_Draft] = []
    refused: list[_Draft] = [d for d in drafts if d.support is Support.REJECTED]
    limit = profile.max_alternatives + 1
    counts = {Support.SATISFIES: 0, Support.ILLUSTRATIVE_ONLY: 0}
    for draft in [d for d in drafts if d.support is not Support.REJECTED]:
        if counts[draft.support] >= limit:
            continue
        shot, chosen = draft.shot, draft.chosen
        twin = duplicate_of(
            shot.key,
            shot.asset_id,
            shot.duplicate_group,
            chosen.start,
            chosen.end,
            taken,
            retrieval,
            angles,
        )
        if twin is not None:
            draft.duplicate_of = twin
            draft.support, draft.support_reason = Support.REJECTED, f"repeats {twin}"
            refused.append(draft)
            continue
        elsewhere = _used_elsewhere(shot, chosen, used)
        novelty, why = novelty_of(
            shot.key, shot.asset_id, shot.scene_id, taken, elsewhere, retrieval
        )
        scores = _renovel(draft, novelty, why)
        draft.scores = scores
        draft.overall, draft.overall_basis = overall(scores, profile)
        draft.reused = elsewhere
        draft.support, draft.support_reason = support_for(
            target, need, draft.assessment, profile, draft.overall
        )
        if draft.support is Support.REJECTED:
            refused.append(draft)
            continue
        counts[draft.support] += 1
        kept.append(draft)
        taken.keys.append(shot.key)
        taken.assets.add(shot.asset_id)
        if shot.duplicate_group:
            taken.groups.add(shot.duplicate_group)
        if shot.scene_id:
            taken.scenes.add((shot.asset_id, shot.scene_id))
        taken.ranges.append((shot.asset_id, chosen.start, chosen.end))
    kept.sort(key=lambda d: d.order)
    best = next((d for d in kept if d.support is Support.SATISFIES), None)
    if best is not None:
        used.setdefault(best.shot.asset_id, []).append((best.chosen.start, best.chosen.end))
    rid = f"rec:{target.target_id}"
    candidates = tuple(_candidate(target, d, rank, profile) for rank, d in enumerate(kept, start=1))
    rejected = tuple(
        _candidate(target, d, 0, profile)
        for d in sorted(refused, key=lambda d: d.order)[:_MAX_REFUSED]
    )
    missing = best is None
    recommendation = Recommendation(
        recommendation_id=rid,
        target_id=target.target_id,
        asset_id=target.asset_id,
        origin=target.origin,
        need=need.kind,
        purpose=need.purpose,
        priority=need.priority,
        concept=target.concept,
        passage=target.text[:_PASSAGE_CHARS],
        requires=need.requires,
        placement=_placement(
            target, need, profile, candidates[0] if candidates and not missing else None
        ),
        candidates=candidates,
        rejected=rejected,
        missing_coverage=missing,
        gap_id=None,
        reasons=need.reasons,
        evidence=_target_evidence(target),
        warnings=_warnings(target, candidates, missing),
        segment_id=target.segment_id,
        section_id=target.section_id,
        beat_id=target.beat_id,
        story_visual_id=target.story_visual_id,
    )
    return (
        recommendation,
        len(drafts),
        [_candidate(target, d, 0, profile) for d in refused],
        list(candidates),
    )


def _with_gap(recommendation: Recommendation, gap_id: str) -> Recommendation:
    return replace(recommendation, gap_id=gap_id)


def _used_elsewhere(
    shot: ShotFact, chosen: Chosen, used: dict[str, list[tuple[Fraction, Fraction]]]
) -> bool:
    return any(a < chosen.end and b > chosen.start for a, b in used.get(shot.asset_id, ()))


def _drafts(
    target: TargetFact,
    need: NeedDecision,
    shot: ShotFact,
    match: Match | None,
    profile: BrollProfile,
) -> list[_Draft]:
    assessment = assess(target, need, shot, match, profile)
    ranges = (
        choose_ranges(shot, target, need, assessment, profile) if not assessment.refusals else None
    )
    if assessment.refusals or isinstance(ranges, str) or ranges is None:
        reason = (
            assessment.refusals[0][1]
            if assessment.refusals
            else (ranges if isinstance(ranges, str) else "no usable range")
        )
        whole = Chosen(
            start=shot.start,
            end=shot.end,
            precision=Precision.SHOT_BOUNDARY,
            temporal=None,
            temporal_reason="refused before a range was chosen",
            usable=shot.seconds,
            wanted=Fraction(profile.min_insert_seconds).limit_denominator(1000),
            moments=0,
        )
        audio, audio_reason, audio_score = audio_plan(shot, whole, need)
        draft = _make(
            target, need, shot, assessment, whole, profile, audio, audio_reason, audio_score
        )
        draft.support, draft.support_reason = Support.REJECTED, reason
        return [draft]
    out = []
    for chosen in ranges:
        audio, audio_reason, audio_score = audio_plan(shot, chosen, need)
        out.append(
            _make(target, need, shot, assessment, chosen, profile, audio, audio_reason, audio_score)
        )
    return out


def _make(
    target: TargetFact,
    need: NeedDecision,
    shot: ShotFact,
    assessment: Assessment,
    chosen: Chosen,
    profile: BrollProfile,
    audio: AudioBehavior,
    audio_reason: str,
    audio_score: Score,
) -> _Draft:
    scores = score_candidate(
        target, need, shot, assessment, chosen, profile, audio_score, None,
        "decided when the candidates are chosen",
    )  # fmt: skip
    total, basis = overall(scores, profile)
    support, why = support_for(target, need, assessment, profile, total)
    return _Draft(
        shot=shot,
        assessment=assessment,
        chosen=chosen,
        use=use_for(target, need, shot, assessment),
        audio=audio,
        audio_reason=audio_reason,
        audio_score=audio_score,
        scores=scores,
        overall=total,
        overall_basis=basis,
        support=support,
        support_reason=why,
    )


def _renovel(draft: _Draft, novelty: float, why: str) -> tuple[Score, ...]:
    return tuple(
        Score(name=ScoreName.NOVELTY, value=round(novelty, 3), basis=ScoreBasis.DERIVED, reason=why)
        if s.name is ScoreName.NOVELTY
        else s
        for s in draft.scores
    )


def _candidate(target: TargetFact, d: _Draft, rank: int, profile: BrollProfile) -> Candidate:
    shot, chosen, a = d.shot, d.chosen, d.assessment
    seconds = float(chosen.end - chosen.start)
    reason_codes = list(a.reason_codes)
    if d.support is Support.ILLUSTRATIVE_ONLY:
        reason_codes.append("illustrative_only")
    if d.support is Support.REJECTED:
        reason_codes.append("refused")
    warnings = [*a.warnings]
    if d.support is not Support.SATISFIES:
        warnings.append(d.support_reason)
    if d.reused:
        warnings.append("the same footage is already recommended for another passage")
    if shot.clock.value == "video_analysis":
        warnings.append("source time is the video analysis clock; no container offset was verified")
    return Candidate(
        candidate_id=(
            f"cand:{target.target_id}:{shot.asset_id}:{shot.shot_id}:{int(chosen.start * 1000)}"
        ),
        rank=rank,
        asset_id=shot.asset_id,
        analyzed_asset_id=shot.analyzed_asset_id,
        via=shot.via,
        asset_name=shot.asset_name,
        shot_id=shot.shot_id,
        scene_id=shot.scene_id,
        shot_range=SourceInterval(
            start=rational(shot.start), end=rational(shot.end), precision=Precision.SHOT_BOUNDARY
        ),
        source=SourceInterval(
            start=rational(chosen.start), end=rational(chosen.end), precision=chosen.precision
        ),
        clock=shot.clock,
        role=a.role,
        association=a.association,
        identity=a.identity,
        support=d.support,
        scores=d.scores,
        overall=d.overall,
        overall_basis=d.overall_basis,
        insert_min_seconds=min(seconds, profile.min_insert_seconds),
        insert_max_seconds=seconds,
        use=d.use,
        audio=d.audio,
        audio_reason=d.audio_reason,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        evidence=a.evidence,
        warnings=tuple(dict.fromkeys(warnings)),
        duplicate_of=d.duplicate_of,
        reused=d.reused,
    )


def _placement(
    target: TargetFact,
    need: NeedDecision,
    profile: BrollProfile,
    best: Candidate | None,
) -> SuggestedPlacement:
    start = Fraction(target.start).limit_denominator(1_000_000)
    end = Fraction(target.end).limit_denominator(1_000_000)
    main = target.main
    keep_speaker: bool | None = None
    if main is not None and main.speaking_face is not None:
        keep_speaker = bool(main.speaking_face and target.emphatic)
    insert_max = profile.max_insert_seconds
    insert_min = profile.min_insert_seconds
    if best is not None:
        insert_max = min(insert_max, best.insert_max_seconds)
        insert_min = min(insert_min, insert_max)
    return SuggestedPlacement(
        use=best.use if best is not None else UseKind.NO_CHANGE,
        target_start=rational(start),
        target_end=rational(max(start, end)),
        insert_min_seconds=round(insert_min, 3),
        insert_max_seconds=round(insert_max, 3),
        keep_speaker_visible=keep_speaker,
        context_before_seconds=profile.context_before_seconds,
        context_after_seconds=profile.context_after_seconds,
        audio=best.audio if best is not None else AudioBehavior.FLAG_FOR_EDITOR,
        note=(
            "times are on the target recording's own clock (not timeline time); the editing "
            "stage places the insert and decides the final in/out"
        ),
    )


def _target_evidence(target: TargetFact) -> tuple[EvidenceRef, ...]:
    refs = []
    if target.story_visual_id:
        refs.append(EvidenceRef(kind="story_visual", ref=target.story_visual_id))
    if target.segment_id:
        refs.append(EvidenceRef(kind="content_opportunity", ref=target.segment_id))
    if target.reason:
        refs.append(EvidenceRef(kind="pacing", ref=target.target_id, detail=target.reason[:120]))
    for reference in target.references:
        refs.append(
            EvidenceRef(
                kind="reference",
                ref=reference.phrase,
                detail=f"{reference.state}: {reference.reason}"[:120],
            )
        )
    return tuple(refs[:8])


def _warnings(
    target: TargetFact, candidates: Sequence[Candidate], missing: bool
) -> tuple[str, ...]:
    out: list[str] = []
    if any(r.state == "ambiguous" for r in target.references):
        out.append(
            "a reference in the passage is ambiguous; identity-dependent footage cannot be proven"
        )
    elif any(r.state == "unresolved" and not r.about_activity for r in target.references):
        out.append("a reference in the passage could not be resolved to a named thing")
    if target.negated:
        out.append("the passage negates or doubts what it names")
    if missing and candidates:
        out.append("only illustrative footage was found")
    return tuple(out)
