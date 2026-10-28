"""Recommendations: what would improve the rhythm, why, how sure, and what it would cost.

A recommendation is a PACING statement about a place ("this silence is dead air", "this passage is
dense and runs fast, give it time", "this demonstration is cut away too soon"). It never says which
source material to remove and edits nothing: whether and how to apply it, given the story, the
continuity and the other edits, is the editing stage's decision. Each carries the measured
condition, the reason codes, the evidence, a confidence AND the reasons it is not higher, the
benefit, the trade-offs and whether it could be applied without a person looking at it.

Speech is never asked to run faster. A faster edit comes from removing silence and genuine
repetition; the profile's speech-rate cap is a limit nothing here exceeds.
"""

from dataclasses import dataclass
from itertools import pairwise

from media_house.modules.pacing_intelligence.domain.context import Context
from media_house.modules.pacing_intelligence.domain.facts import ShotFact, StoryFacts
from media_house.modules.pacing_intelligence.domain.model import (
    EvidenceRef,
    Measure,
    PassageAnalysis,
    PauseAnalysis,
    Recommendation,
    RhythmFinding,
    ShotAnalysis,
    TimelineAnalysis,
    TimeSpan,
)
from media_house.modules.pacing_intelligence.domain.narrative import Narrative
from media_house.modules.pacing_intelligence.domain.picture import CutRun
from media_house.modules.pacing_intelligence.domain.values import (
    Basis,
    EvidenceKind,
    Level,
    PauseVerdict,
    ReasonCode,
    RecommendationType,
    Safety,
    clamp,
)

_PURPOSEFUL_PAUSE = frozenset(
    {
        ReasonCode.PAUSE_BEFORE_EMPHASIS,
        ReasonCode.PAUSE_AFTER_QUESTION,
        ReasonCode.PAUSE_BEFORE_PAYOFF,
        ReasonCode.PAUSE_WITH_REACTION,
    }
)
_DEMONSTRATIONS = frozenset({"demonstration", "evidence", "comparison"})
_REVEALS = frozenset({"payoff", "turning_point"})
_MIN_EXTRA = 0.5
_MIN_SAVING = 1.0
_MIN_PASSAGE_SPEECH = 5.0
_NO_SCORE = Measure(value=None, basis=Basis.HEURISTIC, note="not applicable to this finding")
_PREDICTION = "a prediction from the material, not audience data"


@dataclass(frozen=True, slots=True)
class Findings:
    """Everything the recommendation step reads for one strategy."""

    passages: list[PassageAnalysis]
    pauses: list[PauseAnalysis]
    shots: list[ShotAnalysis]
    rhythm: list[RhythmFinding]
    narrative: Narrative
    timeline: list[TimelineAnalysis]
    cut_runs: list[CutRun]


def recommend(ctx: Context, found: Findings) -> list[Recommendation]:
    out: list[Recommendation] = []
    out += _pauses(ctx, found.pauses)
    for passage in found.passages:
        out += _passage(ctx, passage)
    out += _shots(ctx, found)
    out += _cut_runs(ctx, found)
    out += _demonstrations(ctx)
    out += _narrative(ctx, found)
    out += _beats(ctx, found.rhythm)
    out += _attention(ctx, found, out)
    return out


# --- construction ---------------------------------------------------------------------------
def _make(
    ctx: Context,
    kind: RecommendationType,
    level: Level,
    target: TimeSpan,
    *,
    observed: str,
    proposal: str,
    reasons: list[ReasonCode],
    evidence: list[EvidenceRef],
    confidence: float | None,
    limits: list[str],
    benefit: str,
    tradeoffs: list[str],
    safety: Safety,
    section_id: str | None = None,
    beat_id: str | None = None,
    delta: float | None = None,
    duration: float | None = None,
    speed_up: Measure = _NO_SCORE,
    slow_down: Measure = _NO_SCORE,
    tighten: Measure = _NO_SCORE,
    prerequisites: tuple[str, ...] = (),
) -> Recommendation:
    asset_index = ctx.asset_order.index(target.asset_id)
    start_ms, end_ms = round(target.start.value * 1000), round(target.end.value * 1000)
    if confidence is not None and target.precision.value in ("estimated", "sentence_span"):
        limits = [*limits, "timing is approximate (sentence-level or estimated)"]
        confidence = round(confidence * 0.8, 3)
    return Recommendation(
        recommendation_id=f"rc:{kind.value}:{asset_index}:{start_ms}:{end_ms}",
        type=kind,
        level=level,
        target=target,
        section_id=section_id,
        beat_id=beat_id,
        observed=observed,
        proposal=proposal,
        delta_seconds=None if delta is None else round(delta, 3),
        recommended_duration=None if duration is None else round(duration, 3),
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence=tuple(evidence),
        confidence=confidence,
        confidence_limits=tuple(limits),
        benefit=benefit,
        tradeoffs=tuple(tradeoffs),
        safety=safety,
        priority=0,
        rank_basis="",
        speed_up_score=speed_up,
        slow_down_score=slow_down,
        cut_tightening_score=tighten,
        strategies=(ctx.strategy,),
        prerequisites=prerequisites,
    )


def _score(value: float, note: str) -> Measure:
    return Measure(value=round(clamp(value), 3), basis=Basis.HEURISTIC, note=note)


# --- pauses ---------------------------------------------------------------------------------
def _pauses(ctx: Context, pauses: list[PauseAnalysis]) -> list[Recommendation]:
    out: list[Recommendation] = []
    for pause in pauses:
        limits = _timing_limits(ctx, pause.span.asset_id)
        if pause.verdict in (PauseVerdict.DEAD_AIR, PauseVerdict.LONG) and pause.recommended_keep:
            gain = pause.duration - pause.recommended_keep
            dead = pause.verdict is PauseVerdict.DEAD_AIR
            out.append(
                _make(
                    ctx,
                    RecommendationType.TIGHTEN_PAUSE,
                    Level.PAUSE,
                    pause.span,
                    observed=(
                        f"silence of {pause.duration:.2f} s"
                        + (
                            f", {pause.silence_ratio:.0%} confirmed silent"
                            if pause.silence_ratio is not None
                            else ", silence not measured"
                        )
                    ),
                    proposal=(
                        f"shorten to about {pause.recommended_keep:.2f} s"
                        + ("" if dead else " (long, but nothing marks it as deliberate)")
                    ),
                    reasons=list(pause.reason_codes),
                    evidence=list(pause.evidence),
                    confidence=pause.confidence,
                    limits=limits
                    + ([] if pause.silence_ratio is not None else ["silence was not measured"]),
                    benefit=f"saves about {gain:.2f} s of nothing happening",
                    tradeoffs=[] if dead else ["a pause may be wanted for effect nobody marked"],
                    safety=Safety.SAFE
                    if dead and (pause.confidence or 0.0) >= 0.6
                    else Safety.NEEDS_REVIEW,
                    section_id=pause.section_id,
                    delta=-gain,
                    duration=pause.recommended_keep,
                    tighten=_score(
                        (pause.duration - ctx.natural_pause) / pause.duration,
                        "how far the pause exceeds the natural pause",
                    ),
                    speed_up=_score(gain / pause.duration, "share of the pause that could go"),
                )
            )
        elif (
            pause.verdict is PauseVerdict.MEANINGFUL
            and pause.duration > ctx.natural_pause
            and any(c in _PURPOSEFUL_PAUSE for c in pause.reason_codes)
        ):
            out.append(
                _make(
                    ctx,
                    RecommendationType.PRESERVE_PAUSE,
                    Level.PAUSE,
                    pause.span,
                    observed=f"silence of {pause.duration:.2f} s that gives something room",
                    proposal="keep this pause as it is; it is not dead air",
                    reasons=list(pause.reason_codes),
                    evidence=list(pause.evidence),
                    confidence=pause.confidence,
                    limits=limits,
                    benefit="protects a pause that serves emphasis, a question, a payoff or a "
                    "reaction",
                    tradeoffs=[f"keeps {pause.duration:.2f} s of runtime"],
                    safety=Safety.SAFE,
                    section_id=pause.section_id,
                    delta=0.0,
                    duration=pause.duration,
                    slow_down=_score(0.5, "a deliberate pause; not a request for more time"),
                )
            )
    return out


def _timing_limits(ctx: Context, asset_id: str) -> list[str]:
    audio = ctx.assets[asset_id].audio
    if audio is not None and not audio.word_level:
        return ["word times are estimated within segments, not forced-aligned"]
    return []


# --- passages -------------------------------------------------------------------------------
def _passage(ctx: Context, p: PassageAnalysis) -> list[Recommendation]:
    profile = ctx.profile
    out: list[Recommendation] = []
    load = p.cognitive_load.value
    evidence = [
        EvidenceRef(
            kind=EvidenceKind.CONTENT_SEGMENT,
            ref=p.passage_id,
            asset_id=p.span.asset_id,
            detail="passage measures",
        )
    ]
    limits = _timing_limits(ctx, p.span.asset_id)
    if p.speech_seconds >= _MIN_PASSAGE_SPEECH and load is not None and load >= profile.load_high:
        pressure = clamp((load - profile.load_high) / max(1e-9, 1.0 - profile.load_high))
        breathing = 1.0 - clamp((p.pause_ratio or 0.0) / 0.25)
        extra = (
            p.speech_seconds
            * profile.processing_factor
            * ctx.adjust.processing
            * pressure
            * breathing
        )
        if extra >= _MIN_EXTRA:
            out.append(
                _make(
                    ctx,
                    RecommendationType.ALLOW_PROCESSING_TIME,
                    Level.PASSAGE,
                    p.span,
                    observed=_load_observed(p, load, profile.load_high),
                    proposal=(
                        f"leave up to about {extra:.1f} s more time: a pause between its main "
                        "points, or a picture that shows what is explained"
                    ),
                    reasons=[*p.reason_codes],
                    evidence=evidence,
                    confidence=p.confidence,
                    limits=[
                        *limits,
                        "load is a heuristic estimate of the time a viewer needs",
                    ],
                    benefit="gives the viewer time to follow dense information",
                    tradeoffs=[f"adds up to {extra:.1f} s"],
                    safety=Safety.EXPANDS,
                    section_id=p.section_id,
                    beat_id=p.beat_id,
                    delta=extra,
                    duration=p.elapsed_seconds + extra,
                    slow_down=_score(pressure, "how far the estimated load exceeds the threshold"),
                )
            )
    rate_low = p.elapsed_rate_wpm is not None and p.elapsed_rate_wpm < profile.wpm_low
    density = p.information_density.value
    if (
        rate_low
        and density is not None
        and density < profile.density_low
        and ReasonCode.REPEAT_SERVES_PURPOSE not in p.reason_codes
        and p.elapsed_rate_wpm is not None
    ):
        target_time = p.words * 60 / profile.wpm_low
        saving = min(p.elapsed_seconds - target_time, 0.5 * p.elapsed_seconds)
        if saving >= _MIN_SAVING:
            out.append(
                _make(
                    ctx,
                    RecommendationType.TIGHTEN_DELIVERY,
                    Level.PASSAGE,
                    p.span,
                    observed=(
                        f"{p.elapsed_rate_wpm:.0f} words/min over the whole time (band starts at "
                        f"{profile.wpm_low:.0f}), density {density:.2f}"
                    ),
                    proposal=(
                        f"tighten false starts, hesitations and gaps; about {saving:.1f} s "
                        "would bring it into the profile's band"
                    ),
                    reasons=[ReasonCode.SLOW_DELIVERY, ReasonCode.LOW_DENSITY],
                    evidence=evidence,
                    confidence=p.confidence,
                    limits=[*limits, "slow delivery can be deliberate; the density is a heuristic"],
                    benefit=f"saves about {saving:.1f} s of low-information time",
                    tradeoffs=["may remove natural hesitation that carries tone"],
                    safety=Safety.NEEDS_REVIEW,
                    section_id=p.section_id,
                    beat_id=p.beat_id,
                    delta=-saving,
                    duration=p.elapsed_seconds - saving,
                    speed_up=_score(
                        (profile.wpm_low - p.elapsed_rate_wpm) / profile.wpm_low * 2,
                        "how far below the profile's lowest rate",
                    ),
                    tighten=_score(
                        saving / p.elapsed_seconds, "share of the passage that could go"
                    ),
                )
            )
    tolerance = profile.repetition_tolerance * ctx.adjust.repetition
    repeats = p.repetition.value
    if (
        repeats is not None
        and repeats >= tolerance
        and repeats >= 0.4
        and ReasonCode.NEEDS_CONTEXT not in p.reason_codes
        and p.elapsed_seconds >= _MIN_SAVING
    ):
        saving = p.elapsed_seconds * repeats
        out.append(
            _make(
                ctx,
                RecommendationType.REDUCE_REPETITION,
                Level.PASSAGE,
                p.span,
                observed=f"repeats earlier material ({p.repetition.note})",
                proposal=f"shorten or drop the restatement (up to about {saving:.1f} s)",
                reasons=[ReasonCode.REPEATS_EARLIER],
                evidence=evidence,
                confidence=None if p.confidence is None else round(p.confidence * 0.8, 3),
                limits=[*limits, "repetition is judged from words, not from intent"],
                benefit=f"saves up to {saving:.1f} s of restated material",
                tradeoffs=[
                    "later passages may rely on the restatement",
                    "a restatement can be deliberate emphasis",
                ],
                safety=Safety.NEEDS_REVIEW,
                section_id=p.section_id,
                beat_id=p.beat_id,
                delta=-saving,
                duration=p.elapsed_seconds - saving,
                tighten=_score(repeats, "how much of the passage repeats"),
            )
        )
    return out


def _load_observed(p: PassageAnalysis, load: float, threshold: float) -> str:
    text = f"estimated load {load:.2f} (profile threshold {threshold:.2f})"
    if p.speech_rate_wpm is not None:
        text += f", {p.speech_rate_wpm:.0f} words/min"
    if p.pause_ratio is not None:
        text += f", {p.pause_ratio:.0%} of the time in pauses"
    return text


# --- cutting --------------------------------------------------------------------------------
def _cut_runs(ctx: Context, found: Findings) -> list[Recommendation]:
    out: list[Recommendation] = []
    rate = ctx.cut_rate
    for run in found.cut_runs:
        dense = [
            p
            for p in found.passages
            if p.span.asset_id == run.asset_id
            and p.span.start.value < run.end
            and p.span.end.value > run.start
            and ReasonCode.DENSE_PASSAGE in p.reason_codes
        ]
        section = ctx.section_at(run.asset_id, run.start, run.end)
        out.append(
            _make(
                ctx,
                RecommendationType.HOLD_SHOT,
                Level.SHOT,
                ctx.span(run.asset_id, run.start, run.end, ctx.word_precision(run.asset_id)),
                observed=f"{run.cuts_per_minute:.0f} cuts/min against a tolerance of {rate:.0f}"
                + (" while dense information is being explained" if dense else ""),
                proposal="hold shots longer here: fewer, longer shots",
                reasons=[ReasonCode.FREQUENT_CUTS, *([ReasonCode.DENSE_PASSAGE] if dense else [])],
                evidence=[
                    EvidenceRef(
                        kind=EvidenceKind.VIDEO_SHOT,
                        ref=f"{run.cuts} cuts",
                        asset_id=run.asset_id,
                        detail="cut rate",
                        value=run.cuts_per_minute,
                    )
                ],
                confidence=0.7,
                limits=["quick cutting can be the style; judged only against the profile's rate"],
                benefit="lets the picture be read while the passage is followed",
                tradeoffs=["fewer cuts needs coverage that holds"],
                safety=Safety.NEEDS_REVIEW,
                section_id=section.section_id if section else None,
                slow_down=_score(
                    (run.cuts_per_minute - rate) / rate, "how far the cut rate exceeds the profile"
                ),
            )
        )
    return out


# --- shots ----------------------------------------------------------------------------------
def _shots(ctx: Context, found: Findings) -> list[Recommendation]:
    out: list[Recommendation] = []
    for shot in found.shots:
        if ReasonCode.STATIC_PICTURE not in shot.reason_codes:
            continue
        section = ctx.section_at(shot.span.asset_id, shot.span.start.value, shot.span.end.value)
        out.append(
            _make(
                ctx,
                RecommendationType.ADD_VISUAL_CHANGE,
                Level.SHOT,
                shot.span,
                observed=(
                    f"one shot for {shot.duration:.1f} s with no measured motion, while speech "
                    f"goes on (tolerance {ctx.static_seconds:.0f} s)"
                ),
                proposal="change the picture during the shot: a cutaway, another angle or a "
                "graphic",
                reasons=[ReasonCode.STATIC_PICTURE],
                evidence=[
                    EvidenceRef(
                        kind=EvidenceKind.VIDEO_SHOT,
                        ref=shot.shot_id,
                        asset_id=shot.span.asset_id,
                        detail="static fraction",
                        value=shot.visual_activity.value,
                    )
                ],
                confidence=0.7,
                limits=[
                    "motion is measured on sampled frame pairs",
                    "a long static shot can be right for the format",
                ],
                benefit="gives the eye something new during a long explanation",
                tradeoffs=["needs footage that supports what is said"],
                safety=Safety.NEEDS_REVIEW,
                section_id=section.section_id if section else None,
            )
        )
    return out


def _demonstrations(ctx: Context) -> list[Recommendation]:
    story = ctx.story
    if story is None:
        return []
    out: list[Recommendation] = []
    needed = ctx.profile.demo_min_seconds * ctx.adjust.hold
    for visual in story.visuals:
        ref = visual.source
        evidence = ctx.assets.get(ref.asset_id)
        if (
            visual.purpose not in _DEMONSTRATIONS
            or evidence is None
            or evidence.video is None
            or ref.start is None
            or ref.end is None
        ):
            continue
        shot = _shot_at(evidence.video.shots, ref.start)
        if shot is None:
            continue
        visible = min(shot.end, ref.end) - ref.start
        if visible >= needed or shot.end >= ref.end:
            continue
        gap = needed - visible
        out.append(
            _make(
                ctx,
                RecommendationType.HOLD_DEMONSTRATION,
                Level.SECTION,
                ctx.span(ref.asset_id, ref.start, ref.end, ctx.word_precision(ref.asset_id)),
                observed=(
                    f"'{visual.concept}' stays on screen {visible:.1f} s before the picture "
                    f"changes (the profile wants about {needed:.1f} s)"
                ),
                proposal=f"hold the picture about {gap:.1f} s longer",
                reasons=[ReasonCode.DEMONSTRATION_TOO_SHORT],
                evidence=[
                    EvidenceRef(kind=EvidenceKind.STORY_VISUAL, ref=visual.visual_id),
                    EvidenceRef(
                        kind=EvidenceKind.VIDEO_SHOT, ref=shot.shot_id, asset_id=ref.asset_id
                    ),
                ],
                confidence=0.65,
                limits=["whether the picture shows the thing is not checked, only for how long"],
                benefit="gives the viewer time to see what is demonstrated",
                tradeoffs=[f"adds about {gap:.1f} s"],
                safety=Safety.EXPANDS,
                section_id=visual.section_id,
                delta=gap,
                duration=needed,
                slow_down=_score(gap / needed, "share of the wanted time that is missing"),
            )
        )
    return out


def _shot_at(shots: tuple[ShotFact, ...], moment: float) -> ShotFact | None:
    return next((s for s in shots if s.start <= moment < s.end), None)


# --- narrative ------------------------------------------------------------------------------
def _narrative(ctx: Context, found: Findings) -> list[Recommendation]:
    story = ctx.story
    if story is None:
        return []
    out: list[Recommendation] = []
    n = found.narrative
    for section in n.sections:
        source = next((s for s in story.sections if s.section_id == section.section_id), None)
        if source is None or not source.sources:
            continue
        ref = source.sources[0]
        if ref.start is None or ref.end is None or ref.asset_id not in ctx.assets:
            continue
        span = ctx.span(ref.asset_id, ref.start, ref.end, ctx.word_precision(ref.asset_id))
        if ReasonCode.PREMISE_LATE in section.reason_codes and n.premise_at is not None:
            out.append(
                _make(
                    ctx,
                    RecommendationType.ESTABLISH_PREMISE_EARLIER,
                    Level.SECTION,
                    span,
                    observed=(
                        f"the first statement of the point comes {n.premise_at:.0f} s in "
                        f"(profile deadline "
                        f"{(ctx.profile.premise_deadline or 0) * ctx.adjust.static:.0f} s)"
                    ),
                    proposal="state or promise the point earlier; compress the setup before it",
                    reasons=[ReasonCode.PREMISE_LATE],
                    evidence=[EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=section.section_id)],
                    confidence=0.6,
                    limits=["position comes from the story plan's section lengths"],
                    benefit="the viewer learns what the video is about sooner",
                    tradeoffs=["setup may be what makes the point understandable"],
                    safety=Safety.NEEDS_REVIEW,
                    section_id=section.section_id,
                )
            )
        if ReasonCode.SETUP_HEAVY in section.reason_codes and n.total_seconds and n.setup_share:
            excess = (n.setup_share - ctx.profile.setup_max_share) * n.total_seconds
            out.append(
                _make(
                    ctx,
                    RecommendationType.REDUCE_SETUP,
                    Level.SECTION,
                    span,
                    observed=f"{n.setup_share:.0%} of the runtime is setup before the point "
                    f"(profile allows {ctx.profile.setup_max_share:.0%})",
                    proposal=f"reduce the setup by about {excess:.0f} s",
                    reasons=[ReasonCode.SETUP_HEAVY],
                    evidence=[EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=section.section_id)],
                    confidence=0.55,
                    limits=["setup share counts whole sections of setup/context beats only"],
                    benefit="gets to the point sooner",
                    tradeoffs=["context removed may be needed later"],
                    safety=Safety.NEEDS_REVIEW,
                    section_id=section.section_id,
                    delta=-excess,
                    tighten=_score(
                        excess / n.total_seconds, "share of the runtime in excess setup"
                    ),
                )
            )
    out += _reveals(ctx, story)
    out += _breaths(ctx, story)
    return out


def _reveals(ctx: Context, story: StoryFacts) -> list[Recommendation]:
    out: list[Recommendation] = []
    hold = ctx.profile.reveal_hold * ctx.adjust.hold
    for beat in story.beats:
        ref = beat.source
        if beat.function not in _REVEALS or ref.end is None or ref.asset_id not in ctx.index:
            continue
        audio = ctx.assets[ref.asset_id].audio
        if audio is None or not audio.word_level:
            continue
        words = ctx.index[ref.asset_id].words
        nxt = next((w for w in words if w.start >= ref.end - 0.05), None)
        last = max((w.end for w in words if w.end <= ref.end + 0.05), default=None)
        if nxt is None or last is None:
            continue
        gap = max(0.0, nxt.start - last)
        if gap >= hold:
            continue
        out.append(
            _make(
                ctx,
                RecommendationType.HOLD_REVEAL,
                Level.SECTION,
                ctx.span(ref.asset_id, last, nxt.start, ctx.word_precision(ref.asset_id)),
                observed=f"{beat.function} followed by {gap:.2f} s before speech goes on",
                proposal=f"leave about {hold - gap:.2f} s after it so it lands",
                reasons=[ReasonCode.REVEAL_RUSHED],
                evidence=[EvidenceRef(kind=EvidenceKind.STORY_BEAT, ref=beat.beat_id)],
                confidence=0.75,
                limits=[],
                benefit="gives a reveal or payoff room before the next point",
                tradeoffs=[f"adds about {hold - gap:.2f} s"],
                safety=Safety.EXPANDS,
                section_id=beat.section_id,
                beat_id=beat.beat_id,
                delta=hold - gap,
                duration=hold,
                slow_down=_score((hold - gap) / hold, "share of the wanted hold that is missing"),
            )
        )
    return out


def _breaths(ctx: Context, story: StoryFacts) -> list[Recommendation]:
    out: list[Recommendation] = []
    breath = ctx.profile.section_breath * ctx.adjust.hold
    if breath <= 0:
        return out
    ordered = story.ordered_sections()
    for a, b in pairwise(ordered):
        ra = a.sources[-1] if a.sources else None
        rb = b.sources[0] if b.sources else None
        if (
            ra is None
            or rb is None
            or ra.asset_id != rb.asset_id
            or ra.end is None
            or rb.start is None
            or ra.asset_id not in ctx.index
        ):
            continue
        audio = ctx.assets[ra.asset_id].audio
        if audio is None or not audio.word_level:
            continue
        words = ctx.index[ra.asset_id]
        last = words.last_end_by(ra.end + 0.05)
        first = words.first_word_from(rb.start - 0.05)
        if last is None or first is None or first.start < last:
            continue
        gap = first.start - last
        if gap >= breath:
            continue
        out.append(
            _make(
                ctx,
                RecommendationType.ADD_SECTION_BREATH,
                Level.SECTION,
                ctx.span(ra.asset_id, last, first.start, ctx.word_precision(ra.asset_id)),
                observed=f"only {gap:.2f} s between '{a.label}' and '{b.label}'",
                proposal=f"leave about {breath - gap:.2f} s so the change of section is felt",
                reasons=[ReasonCode.SECTION_ABRUPT],
                evidence=[
                    EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=a.section_id),
                    EvidenceRef(kind=EvidenceKind.STORY_SECTION, ref=b.section_id),
                ],
                confidence=0.65,
                limits=["only sections that follow each other in the same recording are checked"],
                benefit="marks the transition between sections",
                tradeoffs=[f"adds about {breath - gap:.2f} s"],
                safety=Safety.EXPANDS,
                section_id=b.section_id,
                delta=breath - gap,
                duration=breath,
                slow_down=_score(
                    (breath - gap) / breath, "share of the wanted breath that is missing"
                ),
            )
        )
    return out


# --- music and attention --------------------------------------------------------------------
def _beats(ctx: Context, rhythm: list[RhythmFinding]) -> list[Recommendation]:
    if not ctx.profile.prefer_beats:
        return []
    out: list[Recommendation] = []
    for window in rhythm:
        if window.cut_count < 3 or window.cuts_on_beat is None or window.cuts_on_beat >= 0.6:
            continue
        out.append(
            _make(
                ctx,
                RecommendationType.ALIGN_CUTS_TO_BEATS,
                Level.SECTION,
                window.span,
                observed=(
                    f"a {window.tempo_bpm:.0f} bpm pulse (regularity {window.strength:.2f}); only "
                    f"{window.cuts_on_beat:.0%} of {window.cut_count} cuts fall on it"
                ),
                proposal="move cuts onto the pulse where the picture allows",
                reasons=[ReasonCode.CUTS_OFF_PULSE],
                evidence=[
                    EvidenceRef(
                        kind=EvidenceKind.AUDIO_RHYTHM,
                        ref=f"{window.tempo_bpm:.0f}bpm",
                        asset_id=window.span.asset_id,
                        value=window.strength,
                    )
                ],
                confidence=round(window.strength * 0.7, 3),
                limits=[
                    "the pulse comes from the loudness envelope of stretches without speech, "
                    "not from music analysis",
                    "a half or double tempo fits equally well",
                    "music under speech is not detected",
                ],
                benefit="cuts that follow the pulse read as rhythm instead of accident",
                tradeoffs=["not every cut should sit on a beat"],
                safety=Safety.NEEDS_REVIEW,
                tighten=_score(1.0 - window.cuts_on_beat, "share of cuts off the pulse"),
            )
        )
    return out


def _attention(ctx: Context, found: Findings, made: list[Recommendation]) -> list[Recommendation]:
    out: list[Recommendation] = []
    needed = ctx.profile.inactivity_seconds * ctx.adjust.static
    covered = [
        (r.target.asset_id, r.target.start.value, r.target.end.value)
        for r in made
        if r.type is RecommendationType.ADD_VISUAL_CHANGE
    ]
    for line in found.timeline:
        run: list[int] = []
        bins = line.attention
        for i in range(len(bins) + 1):
            quiet = i < len(bins) and bins[i].estimate == 0.0
            if quiet:
                run.append(i)
                continue
            if run:
                start, end = bins[run[0]].start, bins[run[-1]].end
                length = end.value - start.value
                if length >= needed and not any(
                    a == line.asset_id and s < end.value and e > start.value for a, s, e in covered
                ):
                    out.append(_inactive(ctx, line.asset_id, start.value, end.value, length))
            run = []
    return out


def _inactive(
    ctx: Context, asset_id: str, start: float, end: float, length: float
) -> Recommendation:
    section = ctx.section_at(asset_id, start, end)
    return _make(
        ctx,
        RecommendationType.RESTORE_ATTENTION,
        Level.TIMELINE,
        ctx.span(asset_id, start, end, ctx.word_precision(asset_id)),
        observed=(
            f"{length:.0f} s without a cut, an emphasised word, new information or a "
            f"demonstration ({_PREDICTION})"
        ),
        proposal="introduce a change: a cutaway, a shot-size change, a graphic or a concise "
        "transition",
        reasons=[ReasonCode.INACTIVITY],
        evidence=[
            EvidenceRef(
                kind=EvidenceKind.PROFILE,
                ref="inactivity_seconds",
                value=ctx.profile.inactivity_seconds,
            )
        ],
        confidence=0.4,
        limits=[_PREDICTION, "only changes upstream analyses measure are counted"],
        benefit="breaks a long stretch with nothing new for the eye or the mind",
        tradeoffs=["a quiet stretch can be intended (a contemplative or tense moment)"],
        safety=Safety.NEEDS_REVIEW,
        section_id=section.section_id if section else None,
    )
