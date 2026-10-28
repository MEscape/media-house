"""From decisions to a timeline: runs, trims, pieces, placements.

Kept sentences that follow each other in the recording (and in the story) form a RUN: one
continuous stretch of source. Inside a run, silence longer than the profile allows and isolated
filler words are TRIMMED (the run is split there, the removed span is recorded with its evidence).
A run is also split where the story changes section, at the middle of the gap, with no cut. Each
piece becomes a picture clip and a sound clip on the timeline, placed back to back, in exact
seconds. Nothing here touches media: a piece is a range of a recording and a place in the edit.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import pairwise

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.cutpoints import (
    CutContext,
    cut_in,
    cut_out,
    cut_pair,
)
from media_house.modules.editing_intelligence.domain.facts import AssetEvidence, WordFact
from media_house.modules.editing_intelligence.domain.model import (
    CutPoint,
    Decision,
    EvidenceRef,
    Placement,
    Signal,
    SourceRange,
    Trim,
)
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.speech import FILLERS, Extent
from media_house.modules.editing_intelligence.domain.timing import (
    exact,
    frame_round,
    to_fraction,
    to_rational,
)
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    AudioBehavior,
    EvidenceKind,
    Executability,
    Origin,
    PlacementRole,
    Precision,
    SignalBasis,
    SignalName,
    TrackKind,
    TrimKind,
)
from media_house.shared.errors import InvariantViolation

_REACTION_EVENTS = frozenset({"laughter", "breath"})
_PAYOFF_FUNCTIONS = frozenset({"payoff", "turning_point"})
_MIN_REMOVED = Fraction(5, 100)
_ISOLATED_GAP = 0.12
_FILLER_EMPHASIS = 0.5


@dataclass(frozen=True, slots=True)
class Piece:
    """One continuous stretch of one recording, ready to be placed."""

    asset_id: str
    start: Fraction
    end: Fraction
    start_precision: Precision
    end_precision: Precision
    cut_in: CutPoint | None
    cut_out: CutPoint | None
    units: tuple[EditUnit, ...]
    role: PlacementRole = PlacementRole.MAIN
    #: For a cold open: the unit whose material it repeats.
    reuse_unit: str | None = None
    #: A decision of its own (a cold open), instead of the sentences' decisions.
    decision_id: str | None = None

    @property
    def duration(self) -> Fraction:
        return self.end - self.start


@dataclass(slots=True)
class Assembly:
    pieces: list[Piece] = field(default_factory=list)
    #: Trims per decision id (a pause belongs to the sentence that follows it).
    trims: dict[str, list[Trim]] = field(default_factory=dict)
    #: Confidence of every real cut point touching a sentence, per decision id.
    boundaries: dict[str, list[float | None]] = field(default_factory=dict)
    #: Decisions made while assembling: holds (EXTEND) and kept reactions (REACTION).
    extra: list[Decision] = field(default_factory=list)
    #: Silences the pacing analysis protected or asked to tighten, with what the editor did.
    pacing_notes: list[str] = field(default_factory=list)


def runs_of(kept: Sequence[EditUnit]) -> list[list[EditUnit]]:
    runs: list[list[EditUnit]] = []
    for unit in kept:
        if runs and _joined(runs[-1][-1], unit):
            runs[-1].append(unit)
        else:
            runs.append([unit])
    return runs


def _joined(a: EditUnit, b: EditUnit) -> bool:
    return (
        a.asset_id == b.asset_id
        and b.source_pos == a.source_pos + 1
        and a.extent is not None
        and b.extent is not None
        and not a.coarse
        and not b.coarse
        and b.extent.start >= a.extent.start
    )


def assemble(
    kept: Sequence[EditUnit],
    contexts: Mapping[str, CutContext],
    profile: EditingProfile,
) -> Assembly:
    out = Assembly()
    for run in runs_of(kept):
        out.pieces.extend(_run_pieces(run, contexts[run[0].asset_id], profile, out))
    return out


# --- one run -------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Cut:
    """Source removed between two pieces (``out == in_`` for a plain join)."""

    out: Fraction
    in_: Fraction
    out_cp: CutPoint | None
    in_cp: CutPoint | None
    trim: Trim | None
    owner: EditUnit | None
    #: Where the two times come from when no cut point carries it (a join).
    basis: Precision = Precision.SENTENCE_SPAN


def _run_pieces(
    run: list[EditUnit], ctx: CutContext, profile: EditingProfile, asm: Assembly
) -> list[Piece]:
    first, last = run[0], run[-1]
    first_extent, last_extent = _extent(first), _extent(last)
    if ctx.index is not None and profile.cut_fillers and not first.pure_filler:
        first_extent = _strip_edge(first_extent, ctx, first, asm, front=True)
    if ctx.index is not None and profile.cut_fillers and not last.pure_filler:
        last_extent = _strip_edge(last_extent, ctx, last, asm, front=False)
    hold = profile.payoff_hold if last.function in _PAYOFF_FUNCTIONS else 0.0
    if ctx.pacing is not None:
        hold = max(hold, ctx.pacing.reveal_hold(last.beat_id) or 0.0)
    in_cp = cut_in(ctx, first_extent)
    out_cp = cut_out(ctx, last_extent, hold)
    _remember(asm, first, in_cp)
    _remember(asm, last, out_cp)
    if hold and exact(out_cp.time.value) > last_extent.end + exact(profile.tail_out):
        asm.extra.append(_hold_decision(last, ctx, last_extent, out_cp, hold))
    run_start, run_end = to_fraction(in_cp.time), to_fraction(out_cp.time)
    cuts = [
        c
        for c in _cuts(run, first_extent, last_extent, ctx, profile, asm)
        if c.out > run_start and c.in_ < run_end
    ]
    pieces: list[Piece] = []
    start = run_start
    start_cp: CutPoint | None = in_cp
    start_precision = in_cp.precision
    for cut in cuts:
        pieces.append(
            _piece(
                run, ctx, start, cut.out, start_precision, _end_precision(cut), start_cp, cut.out_cp
            )
        )
        start, start_cp = cut.in_, cut.in_cp
        start_precision = cut.in_cp.precision if cut.in_cp is not None else cut.basis
    pieces.append(
        _piece(
            run,
            ctx,
            start,
            to_fraction(out_cp.time),
            start_precision,
            out_cp.precision,
            start_cp,
            out_cp,
        )
    )
    return [p for p in pieces if p.end > p.start and p.units]


def _extent(unit: EditUnit) -> Extent:
    if unit.extent is None:
        raise InvariantViolation("Only timed sentences are placed")
    return unit.extent


def _end_precision(cut: _Cut) -> Precision:
    return cut.out_cp.precision if cut.out_cp is not None else cut.basis


def _piece(
    run: list[EditUnit],
    ctx: CutContext,
    start: Fraction,
    end: Fraction,
    start_precision: Precision,
    end_precision: Precision,
    cut_in_: CutPoint | None,
    cut_out_: CutPoint | None,
) -> Piece:
    members = tuple(
        u
        for u in run
        if u.extent is not None and start <= (u.extent.start + u.extent.end) / 2 < end
    )
    return Piece(
        asset_id=run[0].asset_id,
        start=start,
        end=end,
        start_precision=start_precision,
        end_precision=end_precision,
        cut_in=cut_in_,
        cut_out=cut_out_,
        units=members,
    )


def _remember(asm: Assembly, unit: EditUnit, point: CutPoint) -> None:
    asm.boundaries.setdefault(unit.decision_id, []).append(point.confidence)


def _strip_edge(
    extent: Extent, ctx: CutContext, unit: EditUnit, asm: Assembly, *, front: bool
) -> Extent:
    """Drop filler words from the very start or end of a run: the run simply begins later."""
    words = list(extent.words)
    while len(words) > 1:
        word = words[0] if front else words[-1]
        if word.text not in FILLERS or (word.emphasis or 0.0) >= _FILLER_EMPHASIS:
            break
        words.pop(0 if front else -1)
        asm.trims.setdefault(unit.decision_id, []).append(
            _trim(
                ctx,
                unit,
                TrimKind.FILLER,
                word.start,
                word.end,
                [word],
                _word_executable(ctx, [word]),
                f"filler word '{word.text}' at the {'start' if front else 'end'} of the passage",
            )
        )
    if len(words) == len(extent.words):
        return extent
    return Extent(exact(words[0].start), exact(words[-1].end), extent.precision, tuple(words))


def _cuts(
    run: list[EditUnit],
    first_extent: Extent,
    last_extent: Extent,
    ctx: CutContext,
    profile: EditingProfile,
    asm: Assembly,
) -> list[_Cut]:
    if ctx.index is None:
        return _joins(run)
    owner_of: dict[int, EditUnit] = {}
    words: list[WordFact] = []
    for i, unit in enumerate(run):
        if i == 0:
            current: Sequence[WordFact] = first_extent.words
        elif i == len(run) - 1:
            current = last_extent.words
        else:
            current = _extent(unit).words
        for word in current:
            owner_of[word.index] = unit
            words.append(word)
    found: list[_Cut] = []
    for i, word in enumerate(words):
        if i + 1 < len(words):
            pause = _pause_cut(
                words[i], words[i + 1], owner_of[words[i + 1].index], ctx, profile, asm
            )
            if pause is not None:
                found.append(pause)
        if 0 < i < len(words) - 1 and profile.cut_fillers:
            filler = _filler_cut(words[i - 1], word, words[i + 1], owner_of[word.index], ctx, asm)
            if filler is not None:
                found.append(filler)
    found.sort(key=lambda c: c.out)
    cleaned: list[_Cut] = []
    for cut in found:
        if cleaned and cut.out < cleaned[-1].in_:
            continue
        cleaned.append(cut)
    covered = [(c.out, c.in_) for c in cleaned]
    joins = [j for j in _joins(run) if not any(o <= j.out <= i for o, i in covered)]
    for cut in cleaned:
        if cut.trim is not None and cut.owner is not None:
            asm.trims.setdefault(cut.owner.decision_id, []).append(cut.trim)
        for point, side in ((cut.out_cp, cut.owner), (cut.in_cp, cut.owner)):
            if point is not None and side is not None:
                _remember(asm, side, point)
    return sorted([*cleaned, *joins], key=lambda c: c.out)


def _joins(run: list[EditUnit]) -> list[_Cut]:
    """A section change inside a run: split at the middle of the gap, remove nothing."""
    out: list[_Cut] = []
    for a, b in pairwise(run):
        if a.section_id != b.section_id and a.extent is not None and b.extent is not None:
            middle = a.extent.end + max(Fraction(0), b.extent.start - a.extent.end) / 2
            out.append(_Cut(middle, middle, None, None, None, None, a.extent.precision))
    return out


def _pause_cut(
    left: WordFact,
    right: WordFact,
    owner: EditUnit,
    ctx: CutContext,
    profile: EditingProfile,
    asm: Assembly,
) -> _Cut | None:
    gap = right.start - left.end
    advised = (
        ctx.pacing.pause_at(ctx.source.asset_id, left.end, right.start) if ctx.pacing else None
    )
    if advised is not None and advised.verdict == "meaningful":
        asm.pacing_notes.append(
            f"kept the {gap:.2f} s silence at {left.end:.2f} s: the pacing analysis marks it "
            f"meaningful ({', '.join(advised.reasons)})"
        )
        return None
    threshold, keep = profile.pause_max, profile.pause_keep
    if advised is not None and advised.verdict in ("dead_air", "long") and advised.keep:
        # The pacing analysis calls this silence dead air or too long; the editor still decides,
        # and every check below (payoff, reaction, emphasis, cut points) still applies.
        threshold, keep = min(threshold, max(advised.keep * 1.5, 0.4)), advised.keep
    if gap < threshold:
        return None
    if (right.emphasis or 0.0) >= profile.hold_before_emphasis:
        return None
    if owner.function in _PAYOFF_FUNCTIONS:
        return None
    reaction = ctx.audio.events_between(left.end, right.start, _REACTION_EVENTS)
    if reaction:
        asm.extra.append(
            _reaction_decision(owner, ctx, reaction[0].kind, reaction[0].start, reaction[0].end)
        )
        return None
    pause = ctx.audio.pause_between(left.end, right.start)
    precision = Precision.WORD_ALIGNED if ctx.audio.audio.word_level else Precision.SEGMENT_ALIGNED
    out_cp, in_cp = cut_pair(ctx, left, right, keep, precision)
    out_t, in_t = to_fraction(out_cp.time), to_fraction(in_cp.time)
    if in_t - out_t < _MIN_REMOVED:
        return None
    confirmed = (
        pause is not None
        and pause.silence_ratio is not None
        and pause.silence_ratio >= profile.pause_confirm
    )
    confident = all((c.confidence or 0.0) >= profile.min_auto_confidence for c in (out_cp, in_cp))
    executable = confirmed and confident and ctx.audio.audio.word_level
    ratio = (
        "not measured"
        if pause is None or pause.silence_ratio is None
        else f"{pause.silence_ratio:.0%}"
    )
    trim = Trim(
        kind=TrimKind.PAUSE,
        range=make_range(ctx.source, out_t, in_t, out_cp.precision, in_cp.precision),
        reason=(
            f"silence of {gap:.2f} s is longer than {threshold:.2f} s; "
            f"{keep:.2f} s remain (silent for {ratio})"
            + (
                " - the pacing analysis calls it " + advised.verdict.replace("_", " ")
                if advised
                else ""
            )
        ),
        executability=Executability.EXECUTABLE if executable else Executability.NEEDS_REVIEW,
        evidence=(
            EvidenceRef(
                kind=EvidenceKind.AUDIO_PAUSE,
                ref=f"{left.index}-{right.index}",
                asset_id=ctx.source.asset_id,
                value=None if pause is None else pause.silence_ratio,
                detail="silence ratio",
            ),
            EvidenceRef(
                kind=EvidenceKind.PROFILE_RULE,
                ref=f"{profile.name}.pause_max",
                value=profile.pause_max,
            ),
        ),
    )
    return _Cut(out_t, in_t, out_cp, in_cp, trim, owner)


def _filler_cut(
    before: WordFact,
    word: WordFact,
    after: WordFact,
    owner: EditUnit,
    ctx: CutContext,
    asm: Assembly,
) -> _Cut | None:
    if word.text not in FILLERS or (word.emphasis or 0.0) >= _FILLER_EMPHASIS or owner.pure_filler:
        return None
    room = max(word.start - before.end, after.start - word.end)
    if room < _ISOLATED_GAP:
        return None
    precision = Precision.WORD_ALIGNED if ctx.audio.audio.word_level else Precision.SEGMENT_ALIGNED
    out_cp, in_cp = cut_pair(ctx, before, after, 0.12, precision)
    out_t, in_t = to_fraction(out_cp.time), to_fraction(in_cp.time)
    if not out_t <= exact(word.start) or not in_t >= exact(word.end):
        return None
    trim = _trim(
        ctx,
        owner,
        TrimKind.FILLER,
        float(out_t),
        float(in_t),
        [word],
        _word_executable(ctx, [word]) and all((c.confidence or 0) >= 0.6 for c in (out_cp, in_cp)),
        f"filler word '{word.text}' with room around it",
    )
    return _Cut(out_t, in_t, out_cp, in_cp, trim, owner)


def _word_executable(ctx: CutContext, words: list[WordFact]) -> bool:
    return ctx.audio.audio.word_level and all((w.confidence or 0.0) >= 0.7 for w in words)


def _trim(
    ctx: CutContext,
    owner: EditUnit,
    kind: TrimKind,
    start: float,
    end: float,
    words: list[WordFact],
    executable: bool,
    reason: str,
) -> Trim:
    precision = Precision.WORD_ALIGNED if ctx.audio.audio.word_level else Precision.SEGMENT_ALIGNED
    return Trim(
        kind=kind,
        range=make_range(ctx.source, exact(start), exact(end), precision, precision),
        reason=reason,
        executability=Executability.EXECUTABLE if executable else Executability.NEEDS_REVIEW,
        evidence=tuple(
            EvidenceRef(
                kind=EvidenceKind.AUDIO_WORD,
                ref=str(w.index),
                asset_id=ctx.source.asset_id,
                value=w.confidence,
                detail=w.text,
            )
            for w in words
        ),
    )


def _hold_decision(
    unit: EditUnit, ctx: CutContext, extent: Extent, point: CutPoint, hold: float
) -> Decision:
    return Decision(
        decision_id=f"ext:{unit.key}",
        action=Action.EXTEND,
        origin=Origin.AUTOMATIC,
        asset_id=unit.asset_id,
        section_id=unit.section_id,
        beat_id=unit.beat_id,
        subject=make_range(
            ctx.source, extent.end, to_fraction(point.time), extent.precision, point.precision
        ),
        unit_ids=(unit.unit.unit_id,),
        signals=(),
        executability=Executability.EXECUTABLE
        if (point.confidence or 0.0) >= 0.6
        else Executability.NEEDS_REVIEW,
        rationale=(f"holds {hold:.2f} s past the last word of a {unit.function} passage",),
        evidence=(EvidenceRef(kind=EvidenceKind.STORY_BEAT, ref=unit.beat_id or unit.section_id),),
    )


def _reaction_decision(
    unit: EditUnit, ctx: CutContext, kind: str, start: float, end: float
) -> Decision:
    return Decision(
        decision_id=f"rea:{unit.key}:{kind}",
        action=Action.REACTION,
        origin=Origin.AUTOMATIC,
        asset_id=unit.asset_id,
        section_id=unit.section_id,
        beat_id=unit.beat_id,
        subject=make_range(
            ctx.source, exact(start), exact(end), Precision.ESTIMATED, Precision.ESTIMATED
        ),
        unit_ids=(unit.unit.unit_id,),
        signals=(),
        executability=Executability.EXECUTABLE,
        rationale=(f"a long gap is not shortened: {kind} is heard in it",),
        evidence=(
            EvidenceRef(
                kind=EvidenceKind.AUDIO_EVENT,
                ref=kind,
                asset_id=unit.asset_id,
                detail="heard event",
            ),
        ),
    )


# --- decisions after assembly ------------------------------------------------------------------
def apply_assembly(decisions: dict[str, Decision], asm: Assembly, profile: EditingProfile) -> None:
    """Fold trims and boundary confidence into the sentence decisions (in place)."""
    for decision_id, trims in asm.trims.items():
        current = decisions[decision_id]
        if current.action is Action.CUT or current.origin is Origin.MANUAL:
            continue
        removed = sum(t.range.seconds for t in trims)
        span = current.subject.seconds if current.subject is not None else 0.0
        signals = [s for s in current.signals if s.name is not SignalName.SHORTEN]
        signals.append(
            Signal(
                name=SignalName.SHORTEN,
                value=round(min(1.0, removed / span), 3) if span > 0 else None,
                basis=SignalBasis.MEASURED,
                reason=f"{removed:.2f} s of pauses and fillers removed from {span:.2f} s",
            )
        )
        executability = current.executability
        if any(t.executability is not Executability.EXECUTABLE for t in trims):
            executability = _weaker(executability, Executability.NEEDS_REVIEW)
        decisions[decision_id] = Decision(
            decision_id=current.decision_id,
            action=Action.SHORTEN,
            origin=current.origin,
            asset_id=current.asset_id,
            section_id=current.section_id,
            beat_id=current.beat_id,
            subject=current.subject,
            unit_ids=current.unit_ids,
            signals=tuple(signals),
            executability=executability,
            rationale=(
                *current.rationale,
                f"shortened: {len(trims)} trim(s), {removed:.2f} s removed",
            ),
            constraints=current.constraints,
            trims=tuple(trims),
            evidence=current.evidence,
            warnings=current.warnings,
            note=current.note,
        )
    for decision_id, confidences in asm.boundaries.items():
        current = decisions[decision_id]
        known = [c for c in confidences if c is not None]
        low = min(known) if known else None
        signals = [s for s in current.signals if s.name is not SignalName.CUT_POINT_CONFIDENCE]
        signals.append(
            Signal(
                name=SignalName.CUT_POINT_CONFIDENCE,
                value=low,
                basis=SignalBasis.HEURISTIC,
                reason="weakest boundary of the stretch this sentence starts or ends"
                if low is not None
                else "no evidence about the boundary",
            )
        )
        executability = current.executability
        warnings = current.warnings
        if (
            low is None or low < profile.min_auto_confidence
        ) and current.origin is Origin.AUTOMATIC:
            executability = _weaker(executability, Executability.NEEDS_REVIEW)
            warnings = (
                *warnings,
                "the cut point next to this passage is not safe enough to run unreviewed",
            )
        decisions[decision_id] = Decision(
            decision_id=current.decision_id,
            action=current.action,
            origin=current.origin,
            asset_id=current.asset_id,
            section_id=current.section_id,
            beat_id=current.beat_id,
            subject=current.subject,
            unit_ids=current.unit_ids,
            signals=tuple(signals),
            executability=executability,
            rationale=current.rationale,
            constraints=current.constraints,
            trims=current.trims,
            evidence=current.evidence,
            warnings=warnings,
            note=current.note,
        )
    for extra in asm.extra:
        decisions.setdefault(extra.decision_id, extra)


def _weaker(a: Executability, b: Executability) -> Executability:
    order = (Executability.EXECUTABLE, Executability.NEEDS_REVIEW, Executability.UNRESOLVED)
    return max(a, b, key=order.index)


# --- the timeline ----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Timeline:
    placements: tuple[Placement, ...]
    total: Fraction
    #: Where each piece starts on the timeline (parallel to the pieces).
    starts: tuple[Fraction, ...]


def build_timeline(
    pieces: Sequence[Piece],
    evidence: Mapping[str, AssetEvidence],
    rate: Rational,
    profile: EditingProfile,
) -> Timeline:
    placements: list[Placement] = []
    starts: list[Fraction] = []
    cursor = Fraction(0)
    fade = to_rational(exact(profile.audio_fade))
    zero = Rational(0, 1)
    for i, piece in enumerate(pieces):
        source = evidence[piece.asset_id].source
        starts.append(cursor)
        rng = make_range(source, piece.start, piece.end, piece.start_precision, piece.end_precision)
        first_frame = frame_round(cursor, rate)
        frames = max(1, frame_round(cursor + piece.duration, rate) - first_frame)
        decision_ids = (
            (piece.decision_id,) if piece.decision_id else tuple(u.decision_id for u in piece.units)
        )
        beat = piece.units[0].beat_id if piece.units else None
        section = piece.units[0].section_id if piece.units else None
        confidences = [
            c.confidence
            for c in (piece.cut_in, piece.cut_out)
            if c is not None and c.confidence is not None
        ]
        confidence = min(confidences) if confidences else None
        has_video, has_audio = media_tracks(evidence[piece.asset_id])
        base = f"clip:{i:03d}"
        links = {TrackKind.VIDEO: f"{base}:a", TrackKind.AUDIO: f"{base}:v"}
        for track, present, suffix in (
            (TrackKind.VIDEO, has_video, "v"),
            (TrackKind.AUDIO, has_audio, "a"),
        ):
            if not present:
                continue
            placements.append(
                Placement(
                    placement_id=f"{base}:{suffix}",
                    track=track,
                    layer=0,
                    role=piece.role,
                    source=rng,
                    timeline_start=to_rational(cursor),
                    timeline_duration=to_rational(piece.duration),
                    start_frame=first_frame,
                    duration_frames=frames,
                    section_id=section,
                    beat_id=beat,
                    decision_ids=decision_ids,
                    audio=AudioBehavior.SOURCE_SYNC,
                    fade_in=fade if track is TrackKind.AUDIO and piece.cut_in is not None else zero,
                    fade_out=fade
                    if track is TrackKind.AUDIO and piece.cut_out is not None
                    else zero,
                    cut_in=piece.cut_in,
                    cut_out=piece.cut_out,
                    linked=links[track] if has_video and has_audio else None,
                    confidence=confidence,
                )
            )
        cursor += piece.duration
    return Timeline(tuple(placements), cursor, tuple(starts))


def media_tracks(evidence: AssetEvidence) -> tuple[bool, bool]:
    """Which tracks a recording contributes. An unknown stream is taken from the analyses that
    exist for it; speech implies sound, and nothing is assumed about a picture."""
    source = evidence.source
    has_video = source.has_video if source.has_video is not None else evidence.video is not None
    has_audio = (
        source.has_audio
        if source.has_audio is not None
        else (evidence.audio is not None or evidence.content is not None)
    )
    return bool(has_video), bool(has_audio)


def source_range(evidence: AssetEvidence, piece: Piece) -> SourceRange:
    return make_range(
        evidence.source, piece.start, piece.end, piece.start_precision, piece.end_precision
    )
