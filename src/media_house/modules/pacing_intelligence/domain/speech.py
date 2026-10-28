"""Speech pacing: passages (rate, pauses, density, load, repetition, picture) and pause verdicts.

Definitions used everywhere (and stored on the result so nothing is guessed later):

* A GAP is the silence between two consecutive words that lasts at least ``profile.min_gap``.
* ``speech_rate_wpm`` = words / (elapsed time - gaps). ``elapsed_rate_wpm`` = words / elapsed time.
  A slow elapsed rate with a normal speech rate means pauses, not slow talking.
* A pause is not "dead air" because it is long. It is dead air only when it is long, confirmed
  silent, and nothing gives it a purpose (a question before it, emphasis after it, a reaction in it,
  a payoff after it). Deliberately slow delivery is never penalised.
* Fast speech is not dense content, and slow speech is not simple content: density comes from
  content analysis and is kept apart from the rate.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from media_house.modules.pacing_intelligence.domain.context import (
    AssetIndex,
    Context,
    is_finite_range,
    median,
)
from media_house.modules.pacing_intelligence.domain.facts import (
    AudioFacts,
    PauseFact,
    SegmentFact,
    ShotFact,
    WordFact,
)
from media_house.modules.pacing_intelligence.domain.model import (
    EvidenceRef,
    Measure,
    PassageAnalysis,
    PauseAnalysis,
)
from media_house.modules.pacing_intelligence.domain.values import (
    Basis,
    EvidenceKind,
    PaceClass,
    PauseVerdict,
    Precision,
    ReasonCode,
    clamp,
)

_REACTIONS = frozenset({"laughter", "breath"})
_EMPHASIS = 0.7
_MAX_RUN = 30.0
_MIN_WORDS_FOR_RATE = 3
#: Adds that mean a repeat serves a purpose (it is not "nothing new").
_PURPOSEFUL = frozenset({"recap", "callback", "qualification"})
_REPEAT_VALUE = {"nothing_new": 1.0, "stronger_explanation": 0.4}
_PREMISE_AFTER = frozenset({"payoff", "turning_point"})


@dataclass(frozen=True, slots=True)
class Window:
    """A stretch of one recording that is analysed as one passage."""

    asset_id: str
    number: int
    start: float
    end: float
    segment: SegmentFact | None


def windows_of(ctx: Context) -> list[Window]:
    """Content segments when they are timed; otherwise runs of speech split at long gaps."""
    out: list[Window] = []
    for asset_id in ctx.asset_order:
        evidence = ctx.assets.get(asset_id)
        if evidence is None:
            continue
        timed = (
            [s for s in evidence.content.segments if is_finite_range(s.start, s.end)]
            if evidence.content is not None
            else []
        )
        if timed:
            out.extend(
                Window(asset_id, n, s.start or 0.0, s.end or 0.0, s)
                for n, s in enumerate(sorted(timed, key=lambda s: s.start or 0.0))
            )
        elif evidence.audio is not None:
            out.extend(_runs(asset_id, ctx.index[asset_id].words, ctx))
    return out


def _runs(asset_id: str, words: Sequence[WordFact], ctx: Context) -> list[Window]:
    split = max(ctx.profile.natural_pause * 1.5, 1.2)
    out: list[Window] = []
    first = 0
    for i in range(1, len(words) + 1):
        ends = i == len(words) or words[i].start - words[i - 1].end >= split
        long_run = i < len(words) and words[i].end - words[first].start > _MAX_RUN
        if ends or long_run:
            out.append(Window(asset_id, len(out), words[first].start, words[i - 1].end, None))
            first = i
    return out


def analyse_passage(ctx: Context, window: Window) -> PassageAnalysis:
    profile = ctx.profile
    index = ctx.index[window.asset_id]
    audio = index.evidence.audio
    words = index.words_in(window.start, window.end)
    elapsed = max(0.0, window.end - window.start)
    gaps = _gaps(words, profile.min_gap)
    gap_seconds = sum(g for g in gaps)
    speech = max(0.0, elapsed - gap_seconds)
    segment = window.segment
    count = len(words)
    if count == 0 and segment is not None:
        count = sum(
            u.words
            for u in (index.evidence.content.units if index.evidence.content else ())
            if u.unit_id in segment.unit_ids
        )
    reliable = audio is not None and count >= _MIN_WORDS_FOR_RATE and speech > 0
    rate = count / speech * 60 if reliable and words else None
    elapsed_rate = count / elapsed * 60 if count >= _MIN_WORDS_FOR_RATE and elapsed > 0 else None
    pace = PaceClass.UNKNOWN
    reasons: list[ReasonCode] = []
    if rate is not None:
        if rate > profile.wpm_high:
            pace = PaceClass.FAST
            reasons.append(ReasonCode.FAST_DELIVERY)
        elif rate < profile.wpm_low:
            pace = PaceClass.SLOW
            reasons.append(ReasonCode.SLOW_DELIVERY)
        else:
            pace = PaceClass.STEADY
    density = _density(segment)
    repetition = _repetition(segment, reasons)
    if segment is not None and (segment.requires_context > 0 or segment.depends_on):
        reasons.append(ReasonCode.NEEDS_CONTEXT)
    load = _load(density, rate, segment, ctx)
    if load.value is not None and load.value >= profile.load_high:
        reasons.append(ReasonCode.DENSE_PASSAGE)
    if density.value is not None and density.value < profile.density_low:
        reasons.append(ReasonCode.LOW_DENSITY)
    shots = index.shots_overlapping(window.start, window.end)
    activity = _activity(shots, window.start, window.end)
    cuts = index.cuts_in(window.start, window.end)
    cpm = len(cuts) / (elapsed / 60) if elapsed >= 10.0 and index.shots else None
    if cpm is not None and cpm > ctx.cut_rate:
        reasons.append(ReasonCode.FREQUENT_CUTS)
    if (
        index.shots
        and elapsed > ctx.static_seconds
        and activity.value is not None
        and activity.value < 0.1
    ):
        reasons.append(ReasonCode.STATIC_PICTURE)
    mean_shot = sum(s.end - s.start for s in shots) / len(shots) if shots else None
    section = ctx.section_at(window.asset_id, window.start, window.end)
    beat = ctx.beat_at(window.asset_id, window.start, window.end)
    if segment is not None and segment.unit_ids and section is None:
        section = ctx.section_of_units(window.asset_id, segment.unit_ids)
    speakers = {w.speaker for w in words if w.speaker}
    return PassageAnalysis(
        passage_id=f"ps:{ctx.asset_order.index(window.asset_id)}:{window.number}",
        span=ctx.span(window.asset_id, window.start, window.end, _span_precision(ctx, window)),
        section_id=section.section_id if section else None,
        beat_id=beat.beat_id if beat else None,
        words=count,
        elapsed_seconds=round(elapsed, 3),
        speech_seconds=round(speech, 3),
        speech_rate_wpm=None if rate is None else round(rate, 1),
        elapsed_rate_wpm=None if elapsed_rate is None else round(elapsed_rate, 1),
        pause_seconds=round(gap_seconds, 3),
        pause_ratio=None if elapsed <= 0 or not words else round(gap_seconds / elapsed, 3),
        longest_pause=round(max(gaps, default=0.0), 3),
        pace=pace,
        information_density=density,
        cognitive_load=load,
        repetition=repetition,
        visual_activity=activity,
        shot_count=len(shots),
        cuts_per_minute=None if cpm is None else round(cpm, 1),
        mean_shot_seconds=None if mean_shot is None else round(mean_shot, 3),
        speaker=next(iter(speakers)) if len(speakers) == 1 else None,
        confidence=_confidence(ctx, window, count, words),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def _span_precision(ctx: Context, window: Window) -> Precision:
    return (
        ctx.word_precision(window.asset_id)
        if ctx.index[window.asset_id].words
        else Precision.SENTENCE_SPAN
    )


def _gaps(words: Sequence[WordFact], minimum: float) -> list[float]:
    return [b.start - a.end for a, b in pairwise(words) if b.start - a.end >= minimum]


def _density(segment: SegmentFact | None) -> Measure:
    if segment is None:
        return Measure(value=None, basis=Basis.HEURISTIC, note="no content analysis")
    if segment.density is None:
        return Measure(
            value=None, basis=Basis.HEURISTIC, note="content analysis: too short to estimate"
        )
    return Measure(
        value=segment.density,
        basis=Basis.HEURISTIC,
        note="content intelligence information density",
    )


def _repetition(segment: SegmentFact | None, reasons: list[ReasonCode]) -> Measure:
    if segment is None:
        return Measure(value=None, basis=Basis.DERIVED, note="no content analysis")
    if segment.repeats is None:
        return Measure(value=0.0, basis=Basis.DERIVED, note="repeats nothing earlier")
    if segment.repeats in _PURPOSEFUL:
        reasons.append(ReasonCode.REPEAT_SERVES_PURPOSE)
        return Measure(
            value=0.1, basis=Basis.DERIVED, note=f"repeats earlier material to {segment.repeats}"
        )
    value = _REPEAT_VALUE.get(segment.repeats, 0.2)
    if value >= 0.5:
        reasons.append(ReasonCode.REPEATS_EARLIER)
    return Measure(
        value=value, basis=Basis.DERIVED, note=f"repeats earlier material; adds: {segment.repeats}"
    )


def _load(
    density: Measure, rate: float | None, segment: SegmentFact | None, ctx: Context
) -> Measure:
    """Density, density under speech pressure, and the context a passage needs. Heuristic."""
    if density.value is None:
        return Measure(
            value=None,
            basis=Basis.HEURISTIC,
            note="unknown: needs information density; speech rate alone says nothing about load",
        )
    profile = ctx.profile
    reference = (profile.wpm_low + profile.wpm_high) / 2
    pressure = density.value * clamp((rate or reference) / reference / 1.5)
    context = clamp((segment.requires_context if segment else 0) / 3)
    value = 0.55 * density.value + 0.25 * pressure + 0.20 * context
    return Measure(
        value=round(clamp(value), 3),
        basis=Basis.HEURISTIC,
        note=(
            "0.55 density + 0.25 density x speech rate against the profile's typical rate "
            "+ 0.20 context needed; an estimate of time needed, not a measured load"
        ),
    )


def _activity(shots: Sequence[ShotFact], start: float, end: float) -> Measure:
    """Overlap-weighted share of measured frame pairs with picture or camera motion."""
    weights = 0.0
    total = 0.0
    for shot in shots:
        if shot.static_fraction is None:
            continue
        overlap = min(end, shot.end) - max(start, shot.start)
        if overlap > 0:
            weights += overlap
            total += overlap * (1.0 - shot.static_fraction)
    if weights <= 0:
        return Measure(value=None, basis=Basis.DERIVED, note="no measured motion under the passage")
    return Measure(
        value=round(total / weights, 3),
        basis=Basis.DERIVED,
        note="share of measured frame pairs with picture or camera motion",
    )


def _confidence(
    ctx: Context, window: Window, count: int, words: Sequence[WordFact]
) -> float | None:
    audio = ctx.assets[window.asset_id].audio
    if audio is None:
        return None
    base = 0.9 if audio.word_level else 0.55
    if count < 8:
        base *= 0.6
    known = [w.confidence for w in words if w.confidence is not None]
    if known:
        base *= 0.7 + 0.3 * (sum(known) / len(known))
    if window.segment is None:
        base *= 0.85
    return round(base, 3)


# --- pauses ---------------------------------------------------------------------------------
def analyse_pauses(ctx: Context) -> list[PauseAnalysis]:
    out: list[PauseAnalysis] = []
    for asset_id in ctx.asset_order:
        audio = ctx.assets[asset_id].audio if asset_id in ctx.assets else None
        if audio is None:
            continue
        index = ctx.index[asset_id]
        for number, pause in enumerate(audio.pauses):
            if pause.duration < ctx.profile.min_gap:
                continue
            if audio.word_level and (pause.before_word is None or pause.after_word is None):
                continue  # silence before the first or after the last word is not a pause in speech
            out.append(_pause(ctx, index, audio, asset_id, number, pause))
    return out


def _pause(
    ctx: Context, index: AssetIndex, audio: AudioFacts, asset_id: str, number: int, p: PauseFact
) -> PauseAnalysis:
    profile = ctx.profile
    before = _word(index, p.before_word)
    after = _word(index, p.after_word)
    reasons: list[ReasonCode] = []
    evidence = [
        EvidenceRef(
            kind=EvidenceKind.AUDIO_PAUSE,
            ref=str(number),
            asset_id=asset_id,
            detail="silence ratio",
            value=p.silence_ratio,
        )
    ]
    duration = p.duration
    meaningful = False
    reaction = [
        e for e in audio.events if e.kind in _REACTIONS and e.end > p.start and e.start < p.end
    ]
    if reaction:
        meaningful = True
        reasons.append(ReasonCode.PAUSE_WITH_REACTION)
        evidence.append(
            EvidenceRef(kind=EvidenceKind.AUDIO_EVENT, ref=reaction[0].kind, asset_id=asset_id)
        )
    if duration >= profile.meaningful_pause:
        if after is not None and (after.emphasis or 0.0) >= _EMPHASIS:
            meaningful = True
            reasons.append(ReasonCode.PAUSE_BEFORE_EMPHASIS)
            evidence.append(
                EvidenceRef(
                    kind=EvidenceKind.AUDIO_WORD,
                    ref=str(after.index),
                    asset_id=asset_id,
                    detail="emphasis",
                    value=after.emphasis,
                )
            )
        if _after_question(index, before):
            meaningful = True
            reasons.append(ReasonCode.PAUSE_AFTER_QUESTION)
        beat = ctx.beat_at(asset_id, p.end, p.end + 0.5)
        if beat is not None and beat.function in _PREMISE_AFTER:
            meaningful = True
            reasons.append(ReasonCode.PAUSE_BEFORE_PAYOFF)
            evidence.append(EvidenceRef(kind=EvidenceKind.STORY_BEAT, ref=beat.beat_id))
    turn = (
        before is not None
        and after is not None
        and before.speaker != after.speaker
        and (before.speaker is not None and after.speaker is not None)
    )
    confirmed = p.silence_ratio is not None and p.silence_ratio >= profile.silence_confirm
    keep: float | None = None
    if meaningful:
        verdict = PauseVerdict.MEANINGFUL
    elif turn and duration <= ctx.dead_air:
        verdict = PauseVerdict.NATURAL
        reasons.append(ReasonCode.SPEAKER_TURN_GAP)
    elif duration <= ctx.natural_pause:
        verdict = PauseVerdict.NATURAL
    elif duration >= ctx.dead_air and confirmed:
        verdict = PauseVerdict.DEAD_AIR
        reasons.append(ReasonCode.DEAD_AIR)
        keep = ctx.keep_pause
    else:
        verdict = PauseVerdict.LONG
        reasons.append(ReasonCode.LONG_PAUSE)
        keep = max(ctx.keep_pause, ctx.natural_pause)
    section = ctx.section_at(asset_id, p.start, p.end)
    return PauseAnalysis(
        pause_id=f"pz:{ctx.asset_order.index(asset_id)}:{number}",
        span=ctx.span(asset_id, p.start, p.end, ctx.word_precision(asset_id)),
        duration=round(duration, 3),
        silence_ratio=p.silence_ratio,
        verdict=verdict,
        recommended_keep=None if keep is None or keep >= duration else round(keep, 3),
        section_id=section.section_id if section else None,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence=tuple(evidence),
        confidence=_pause_confidence(audio.word_level, p),
    )


def _pause_confidence(word_level: bool, p: PauseFact) -> float:
    base = 0.9 if word_level else 0.55
    silence = 0.6 if p.silence_ratio is None else 0.6 + 0.4 * p.silence_ratio
    return round(base * silence, 3)


def _word(index: AssetIndex, number: int | None) -> WordFact | None:
    return None if number is None else index.word(number)


def _after_question(index: AssetIndex, before: WordFact | None) -> bool:
    """The sentence the pause follows ends with a question mark."""
    content = index.evidence.content
    if before is None or content is None:
        return False
    for unit in content.units:
        if (
            unit.start is not None
            and unit.end is not None
            and unit.start <= before.start < unit.end
        ):
            return unit.text.rstrip().endswith("?")
    return False


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


__all__ = [
    "Window",
    "analyse_passage",
    "analyse_pauses",
    "coefficient_of_variation",
    "median",
    "windows_of",
]
