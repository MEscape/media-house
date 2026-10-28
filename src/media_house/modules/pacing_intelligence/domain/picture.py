"""Visual rhythm: shot lengths and activity, the pulse in the sound against the cuts, attention.

No rule here says a long shot is bad or a short one good. A shot is only reported when it is long
AND measured as static AND speech is going on under it; cuts are only called frequent against the
profile's own rate. The attention curve counts changes that tend to hold attention (a cut, an
emphasised word, a demonstration, a reaction); it is a prediction from the material, not audience
data.
"""

from bisect import bisect_left
from dataclasses import dataclass

from media_house.modules.pacing_intelligence.domain.context import Context, exact, median
from media_house.modules.pacing_intelligence.domain.facts import ShotFact
from media_house.modules.pacing_intelligence.domain.model import (
    AttentionBin,
    Measure,
    PassageAnalysis,
    RhythmFinding,
    ShotAnalysis,
    TimelineAnalysis,
)
from media_house.modules.pacing_intelligence.domain.speech import coefficient_of_variation
from media_house.modules.pacing_intelligence.domain.values import Basis, ReasonCode, clamp

_NEIGHBOURS = 3
_STATIC = 0.85
_SATURATION = 3
_EMPHASIS = 0.7
_DENSE_START = 0.6
_UNKNOWN_BASIS = "no motion measurement for this shot"


def analyse_shots(ctx: Context) -> list[ShotAnalysis]:
    out: list[ShotAnalysis] = []
    for asset_id in ctx.asset_order:
        evidence = ctx.assets.get(asset_id)
        if evidence is None or evidence.video is None:
            continue
        shots = evidence.video.shots
        lengths = [s.end - s.start for s in shots]
        speech = _speech_spans(ctx, asset_id)
        for position, shot in enumerate(shots):
            around = (
                lengths[max(0, position - _NEIGHBOURS) : position]
                + lengths[position + 1 : position + 1 + _NEIGHBOURS]
            )
            typical = median(around)
            reasons: list[ReasonCode] = []
            length = lengths[position]
            if (
                length > ctx.static_seconds
                and shot.static_fraction is not None
                and shot.static_fraction >= _STATIC
                and _overlaps(speech, shot.start, shot.end) > 0.5 * length
            ):
                reasons.append(ReasonCode.STATIC_PICTURE)
            out.append(
                ShotAnalysis(
                    shot_id=shot.shot_id,
                    span=ctx.span(
                        asset_id, shot.start, shot.end, ctx.word_precision(asset_id), shot=shot
                    ),
                    duration=round(length, 3),
                    relative_duration=None if not typical else round(length / typical, 3),
                    visual_activity=_shot_activity(shot),
                    camera=shot.camera,
                    reason_codes=tuple(reasons),
                )
            )
    return out


def _shot_activity(shot: ShotFact) -> Measure:
    if shot.static_fraction is None:
        return Measure(value=None, basis=Basis.DERIVED, note=_UNKNOWN_BASIS)
    return Measure(
        value=round(1.0 - shot.static_fraction, 3),
        basis=Basis.DERIVED,
        note="share of measured frame pairs with picture or camera motion",
    )


def _speech_spans(ctx: Context, asset_id: str) -> list[tuple[float, float]]:
    words = ctx.index[asset_id].words
    return [(w.start, w.end) for w in words]


def _overlaps(spans: list[tuple[float, float]], start: float, end: float) -> float:
    """Seconds of speech inside ``[start, end)`` (spans are sorted by start)."""
    total = 0.0
    first = bisect_left([s for s, _ in spans], start) - 1
    for s, e in spans[max(0, first) :]:
        if s >= end:
            break
        total += max(0.0, min(e, end) - max(s, start))
    return total


@dataclass(frozen=True, slots=True)
class CutRun:
    """A stretch of one recording that is cut more often than the profile tolerates."""

    asset_id: str
    start: float
    end: float
    cuts: int
    cuts_per_minute: float


_RATE_WINDOW = 20.0
_RATE_STEP = 10.0


def analyse_cut_rate(ctx: Context) -> list[CutRun]:
    """Windows of ``_RATE_WINDOW`` seconds whose cut rate exceeds the profile, merged when they
    overlap. Short passages are too short to rate on their own, so the rate is taken over time."""
    out: list[CutRun] = []
    for asset_id in ctx.asset_order:
        evidence = ctx.assets.get(asset_id)
        if evidence is None or evidence.video is None:
            continue
        index = ctx.index[asset_id]
        total = evidence.source.duration or (
            evidence.video.shots[-1].end if evidence.video.shots else 0.0
        )
        if total < _RATE_WINDOW / 2:
            continue
        size = min(_RATE_WINDOW, total)
        start = 0.0
        runs: list[CutRun] = []
        while start < total - 1e-9:
            end = min(total, start + size)
            cuts = len(index.cuts_in(start, end))
            rate = cuts / ((end - start) / 60)
            if rate > ctx.cut_rate:
                if runs and start <= runs[-1].end:
                    previous = runs.pop()
                    merged = len(index.cuts_in(previous.start, end))
                    runs.append(
                        CutRun(
                            asset_id,
                            previous.start,
                            end,
                            merged,
                            merged / ((end - previous.start) / 60),
                        )
                    )
                else:
                    runs.append(CutRun(asset_id, start, end, cuts, rate))
            if end >= total:
                break
            start += _RATE_STEP
        out.extend(runs)
    return out


# --- pulse in the sound against the cuts ----------------------------------------------------
def analyse_rhythm(ctx: Context) -> list[RhythmFinding]:
    out: list[RhythmFinding] = []
    for asset_id in ctx.asset_order:
        evidence = ctx.assets.get(asset_id)
        if evidence is None or evidence.audio is None:
            continue
        index = ctx.index[asset_id]
        for window in evidence.audio.rhythm:
            if window.strength < ctx.profile.min_rhythm_strength:
                continue
            cuts = index.cuts_in(window.start, window.end)
            share = _on_beat(cuts, window.beats, ctx.profile.beat_tolerance) if cuts else None
            out.append(
                RhythmFinding(
                    span=ctx.span(asset_id, window.start, window.end, ctx.word_precision(asset_id)),
                    tempo_bpm=window.tempo_bpm,
                    strength=window.strength,
                    beat_count=len(window.beats),
                    cuts_on_beat=share,
                    cut_count=len(cuts),
                )
            )
    return out


def _on_beat(cuts: list[float], beats: tuple[float, ...], tolerance: float) -> float | None:
    if not beats:
        return None
    near = sum(1 for c in cuts if min(abs(c - b) for b in beats) <= tolerance)
    return round(near / len(cuts), 3)


# --- attention and whole-recording rhythm --------------------------------------------------------
def analyse_timeline(
    ctx: Context, asset_id: str, passages: list[PassageAnalysis]
) -> TimelineAnalysis:
    evidence = ctx.assets[asset_id]
    index = ctx.index[asset_id]
    total = evidence.source.duration
    if total is None and evidence.audio is not None:
        total = evidence.audio.duration
    if total is None and evidence.video is not None and evidence.video.shots:
        total = evidence.video.shots[-1].end
    mine = [p for p in passages if p.span.asset_id == asset_id]
    rates = [p.speech_rate_wpm for p in mine if p.speech_rate_wpm is not None]
    speech = sum(p.speech_seconds for p in mine)
    words = sum(p.words for p in mine if p.speech_rate_wpm is not None)
    timed = sum(p.speech_seconds for p in mine if p.speech_rate_wpm is not None)
    mean_rate = words / timed * 60 if timed > 0 else None
    lengths = [s.end - s.start for s in index.shots]
    rate_cv = coefficient_of_variation(rates)
    shot_cv = coefficient_of_variation(lengths)
    cpm = (len(index.shots) - 1) / (total / 60) if total and total >= 10 and index.shots else None
    consistency = _consistency(rate_cv, shot_cv)
    bins = _attention(ctx, asset_id, total, mine)
    return TimelineAnalysis(
        asset_id=asset_id,
        seconds=None if total is None else round(total, 3),
        speech_seconds=round(speech, 3),
        mean_speech_rate_wpm=None if mean_rate is None else round(mean_rate, 1),
        rate_variation=None if rate_cv is None else round(rate_cv, 3),
        shot_duration_variation=None if shot_cv is None else round(shot_cv, 3),
        cuts_per_minute=None if cpm is None else round(cpm, 1),
        pacing_consistency=consistency,
        attention=bins,
        summary=_summary(mean_rate, cpm, consistency, ctx),
    )


def _consistency(rate_cv: float | None, shot_cv: float | None) -> Measure:
    """1 = even rhythm, 0 = very uneven. Uneven is not bad: a story may speed up and slow down."""
    parts = [1.0 - clamp(cv / 1.0) for cv in (rate_cv, shot_cv) if cv is not None]
    if not parts:
        return Measure(
            value=None, basis=Basis.DERIVED, note="needs at least three passages or three shots"
        )
    return Measure(
        value=round(sum(parts) / len(parts), 3),
        basis=Basis.DERIVED,
        note="1 - variation of speech rate and of shot length (a description, not a verdict)",
    )


def _summary(mean_rate: float | None, cpm: float | None, consistency: Measure, ctx: Context) -> str:
    bits: list[str] = []
    if mean_rate is not None:
        band = ctx.profile
        where = (
            "above"
            if mean_rate > band.wpm_high
            else "below"
            if mean_rate < band.wpm_low
            else "within"
        )
        bits.append(
            f"speech {mean_rate:.0f} words/min of speaking time, {where} the "
            f"{band.name} band {band.wpm_low:.0f}-{band.wpm_high:.0f}"
        )
    if cpm is not None:
        bits.append(f"{cpm:.1f} cuts/min (profile tolerates {ctx.cut_rate:.0f})")
    if consistency.value is not None:
        bits.append(f"rhythm consistency {consistency.value:.2f}")
    return "; ".join(bits) if bits else "not enough evidence to describe the rhythm"


def _attention(
    ctx: Context, asset_id: str, total: float | None, passages: list[PassageAnalysis]
) -> tuple[AttentionBin, ...]:
    if total is None or total <= 0:
        return ()
    size = max(0.5, ctx.profile.attention_bin)
    count = int(total // size) + (1 if total % size else 0)
    index = ctx.index[asset_id]
    changes: list[list[str]] = [[] for _ in range(count)]

    def put(moment: float, label: str) -> None:
        position = int(moment // size)
        if 0 <= position < count and label not in changes[position]:
            changes[position].append(label)

    for cut in index.cuts_in(0.0, total):
        put(cut, "cut")
    for word in index.words:
        if (word.emphasis or 0.0) >= _EMPHASIS:
            put(word.start, "emphasis")
    audio = ctx.assets[asset_id].audio
    if audio is not None:
        for event in audio.events:
            if event.kind == "laughter":
                put(event.start, "reaction")
    for passage in passages:
        density = passage.information_density.value
        if density is not None and density >= _DENSE_START:
            put(passage.span.start.value, "new_information")
    if ctx.story is not None:
        for visual in ctx.story.visuals:
            ref = visual.source
            if ref.asset_id == asset_id and ref.start is not None:
                put(ref.start, "demonstration")
    return tuple(
        AttentionBin(
            start=exact(i * size),
            end=exact(min(total, (i + 1) * size)),
            estimate=round(min(1.0, len(found) / _SATURATION), 3),
            changes=tuple(found),
        )
        for i, found in enumerate(changes)
    )
