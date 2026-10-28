"""Visual coverage: where other footage or a graphic would serve the passage, and what qualifies.

The story names the places (``VisualFact``: purpose, necessity, the verbatim concept) and the
alignment ranked shots for them. This module decides, from separate signals, whether the picture
needs it (``cover_need``) and whether any candidate is fit to use:

* the shot must exist in the video analysis and lie inside the recording;
* it must not be the shot already on screen;
* it must be technically usable, and carry no junk indicator;
* a shot in which a visible face is speaking is refused (it would show lips out of sync);
* it must be long enough to be worth cutting to.

A candidate that passes is EXECUTABLE only when the association is specific (the shot shows text
or an object the passage names, or a description does) and the passage does not negate, doubt or
imagine what it names; a keyword shared with a contradicting sentence is flagged for review, never
run. Nothing is invented: with no qualifying shot the request stays open and is reported as
unresolved. Cover sits on a second picture layer over the main picture; the narration carries on.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import Piece, Timeline
from media_house.modules.editing_intelligence.domain.facts import (
    AssetEvidence,
    ShotChoice,
    ShotFact,
    StoryFacts,
    VisualFact,
)
from media_house.modules.editing_intelligence.domain.hooks import CoverageQuery, Hooks
from media_house.modules.editing_intelligence.domain.model import (
    AudioBridge,
    CoverageRequest,
    CoverCandidate,
    Decision,
    EffectRequest,
    EvidenceRef,
    Issue,
    Placement,
    Signal,
    SourceRange,
)
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.ranges import make_range
from media_house.modules.editing_intelligence.domain.speech import negates
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
    BridgeKind,
    EffectIntent,
    EffectKind,
    EvidenceKind,
    Executability,
    IssueKind,
    Origin,
    PlacementRole,
    Precision,
    Severity,
    SignalBasis,
    SignalName,
    TrackKind,
)

SPECIFIC_GRADES = frozenset({"observed_text", "described", "entity_label"})
_GRAPHIC_PURPOSES = {
    "explainer_graphic": EffectIntent.EXPLAIN,
    "text_on_screen": EffectIntent.SHOW_TEXT,
}
_MIN_USABILITY = 0.4
_BRIDGE_EDGE = Fraction(1)
_BRIDGE_REACH = Fraction(3, 5)
_NECESSITY = {"essential": 1.0, "optional": 0.6}


@dataclass(slots=True)
class CoverageResult:
    requests: list[CoverageRequest] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    bridges: list[AudioBridge] = field(default_factory=list)
    effects: list[EffectRequest] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def plan_coverage(
    story: StoryFacts,
    units: Sequence[EditUnit],
    pieces: Sequence[Piece],
    timeline: Timeline,
    evidence: Mapping[str, AssetEvidence],
    rate: Rational,
    profile: EditingProfile,
    hooks: Hooks,
    issue_id: Callable[[], str],
    effect_id: Callable[[], str],
) -> CoverageResult:
    out = CoverageResult()
    section_order = {s.section_id: s.order for s in story.sections}
    ordered = sorted(
        story.visuals, key=lambda v: (section_order.get(v.section_id, 0), v.source.start or 0.0)
    )
    for visual in ordered:
        _one(
            visual,
            story,
            units,
            pieces,
            timeline,
            evidence,
            rate,
            profile,
            hooks,
            out,
            issue_id,
            effect_id,
        )
    return out


def _one(
    visual: VisualFact,
    story: StoryFacts,
    units: Sequence[EditUnit],
    pieces: Sequence[Piece],
    timeline: Timeline,
    evidence: Mapping[str, AssetEvidence],
    rate: Rational,
    profile: EditingProfile,
    hooks: Hooks,
    out: CoverageResult,
    issue_id: Callable[[], str],
    effect_id: Callable[[], str],
) -> None:
    ref = visual.source
    if ref.start is None or ref.end is None or ref.asset_id not in evidence:
        out.issues.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.EVIDENCE_MISSING,
                severity=Severity.UNRESOLVED,
                detail=f"visual {visual.visual_id}: the passage has no time, so it cannot be "
                "covered",
                subject=visual.visual_id,
            )
        )
        return
    asset = evidence[ref.asset_id]
    start, end = exact(ref.start), exact(ref.end)
    hit = next(
        (
            (i, p)
            for i, p in enumerate(pieces)
            if p.role is PlacementRole.MAIN
            and p.asset_id == ref.asset_id
            and p.start < end
            and p.end > start
        ),
        None,
    )
    if hit is None:
        return  # the passage was cut: nothing to cover
    index, piece = hit
    span_start, span_end = max(start, piece.start), min(end, piece.end)
    source_range = make_range(
        asset.source, span_start, span_end, Precision.SENTENCE_SPAN, Precision.SENTENCE_SPAN
    )
    need = _need(visual, piece, profile, units)
    request_id = f"cov:{visual.visual_id}"
    if need.value is None or need.value < profile.cover_threshold:
        return
    if visual.purpose in _GRAPHIC_PURPOSES:
        _graphic(
            visual,
            request_id,
            source_range,
            need,
            timeline,
            index,
            span_start,
            piece,
            out,
            effect_id,
        )
        return
    accepted, rejected_notes = _candidates(
        visual, evidence, span_start, span_end, piece, profile, hooks, request_id
    )
    chosen = accepted[0] if accepted else None
    placement_id: str | None = None
    if chosen is not None:
        placement = _cover_placement(
            chosen,
            visual,
            request_id,
            index,
            span_start,
            span_end,
            piece,
            timeline,
            pieces,
            evidence,
            rate,
            profile,
            out,
        )
        placement_id = placement.placement_id
        out.placements.append(placement)
    executability = chosen.executability if chosen is not None else Executability.UNRESOLVED
    notes = rejected_notes if chosen is None else tuple(chosen.warnings)
    out.requests.append(
        CoverageRequest(
            request_id=request_id,
            section_id=visual.section_id,
            purpose=visual.purpose,
            necessity=visual.necessity,
            concept=visual.concept,
            span=source_range,
            need=need,
            chosen=chosen,
            alternatives=tuple(accepted[1:4]),
            placement_id=placement_id,
            executability=executability,
            notes=notes,
            decision_id=request_id,
        )
    )
    out.decisions.append(
        Decision(
            decision_id=request_id,
            action=Action.COVER,
            origin=Origin.AUTOMATIC,
            asset_id=ref.asset_id,
            section_id=visual.section_id,
            beat_id=None,
            subject=chosen.range if chosen is not None else source_range,
            unit_ids=ref.unit_ids,
            signals=(need,),
            executability=executability,
            rationale=(
                f"{visual.purpose} for '{visual.concept}': {need.reason}"
                if chosen is not None
                else f"cover is needed for '{visual.concept}' but no qualifying footage exists",
            ),
            evidence=_evidence(visual, chosen),
            warnings=notes,
        )
    )
    if chosen is None:
        out.issues.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.COVER_NEEDED,
                severity=Severity.UNRESOLVED,
                detail=(
                    f"'{visual.concept}' would gain from {visual.purpose} footage, but no shot "
                    "with "
                    "evidence qualifies"
                    + (": " + "; ".join(rejected_notes[:2]) if rejected_notes else "")
                ),
                subject=request_id,
            )
        )
        return
    out.decisions.append(
        Decision(
            decision_id=f"aud:{visual.visual_id}",
            action=Action.PRESERVE_AUDIO,
            origin=Origin.AUTOMATIC,
            asset_id=ref.asset_id,
            section_id=visual.section_id,
            beat_id=None,
            subject=source_range,
            unit_ids=ref.unit_ids,
            signals=(),
            executability=executability,
            rationale=("the narration carries on over the cover; the cover's own sound is muted",),
            evidence=(EvidenceRef(kind=EvidenceKind.STORY_VISUAL, ref=visual.visual_id),),
        )
    )
    if chosen.executability is not Executability.EXECUTABLE:
        out.issues.append(
            Issue(
                issue_id=issue_id(),
                kind=IssueKind.COVER_RISK,
                severity=Severity.WARNING,
                detail="; ".join(chosen.warnings) or "the association needs a person's check",
                subject=request_id,
            )
        )


def _need(
    visual: VisualFact, piece: Piece, profile: EditingProfile, units: Sequence[EditUnit]
) -> Signal:
    parts: list[tuple[str, float]] = []
    named = _NECESSITY.get(visual.necessity, 0.6) * visual.strength
    parts.append((f"{visual.necessity} {visual.purpose} (strength {visual.strength:.2f})", named))
    importance = 1.5 if any(u.priority == "essential" for u in piece.units) else 1.0
    limit = profile.static_seconds * importance
    repeat = max(0.0, min(1.0, float(piece.duration) / limit - 0.5)) if limit > 0 else 0.0
    if repeat > 0:
        parts.append((f"{float(piece.duration):.1f} s without a change of picture", repeat))
    base = max(v for _, v in parts)
    note = ""
    if visual.already_covered:
        base *= 0.3
        note = "; content analysis says footage already shows it"
    value = round(min(1.0, base * (0.4 + 0.6 * profile.cover_appetite)), 3)
    strongest = max(parts, key=lambda item: item[1])[0]
    return Signal(
        name=SignalName.COVER_NEED,
        value=value,
        basis=SignalBasis.HEURISTIC,
        reason=f"{strongest}; profile {profile.name} appetite {profile.cover_appetite:.2f}{note}",
    )


def _candidates(
    visual: VisualFact,
    evidence: Mapping[str, AssetEvidence],
    span_start: Fraction,
    span_end: Fraction,
    piece: Piece,
    profile: EditingProfile,
    hooks: Hooks,
    request_id: str,
) -> tuple[list[CoverCandidate], tuple[str, ...]]:
    choices: Sequence[ShotChoice] = visual.shots
    if hooks.coverage is not None:
        advised = hooks.coverage(
            CoverageQuery(
                request_id=request_id,
                section_id=visual.section_id,
                purpose=visual.purpose,
                concept=visual.concept,
                asset_id=visual.source.asset_id,
                start=float(span_start),
                end=float(span_end),
                baseline=visual.shots,
            )
        )
        if advised is not None:
            choices = advised
    passage = " ".join(u.unit.text for u in piece.units if u.unit.unit_id in visual.source.unit_ids)
    accepted: list[CoverCandidate] = []
    rejected: list[str] = []
    for choice in sorted(choices, key=lambda c: c.rank):
        verdict = _judge(choice, evidence, span_start, span_end, profile, passage)
        if isinstance(verdict, str):
            rejected.append(verdict)
        else:
            accepted.append(verdict)
    accepted.sort(key=lambda c: (c.executability is not Executability.EXECUTABLE, c.rank))
    return accepted, tuple(rejected)


def _judge(
    choice: ShotChoice,
    evidence: Mapping[str, AssetEvidence],
    span_start: Fraction,
    span_end: Fraction,
    profile: EditingProfile,
    passage: str,
) -> CoverCandidate | str:
    """A qualifying candidate, or the reason the shot was refused."""
    target = evidence.get(choice.asset_id)
    shot = target.video.shot(choice.shot_id) if target is not None and target.video else None
    if target is None or shot is None:
        return f"{choice.shot_id}: not found in the video analysis"
    reason = _unfit(shot, target, choice, span_start, span_end)
    if reason is not None:
        return f"{choice.shot_id}: {reason}"
    head = exact(shot.head_seconds) if shot.head_seconds else Fraction(0)
    begin = exact(shot.start) + head
    available = exact(shot.end) - begin
    if available < exact(profile.min_cover_seconds):
        begin, available = exact(shot.start), exact(shot.end) - exact(shot.start)
    length = min(available, exact(profile.max_cover_seconds), span_end - span_start)
    if length < exact(profile.min_cover_seconds):
        return f"{choice.shot_id}: too short to cut to"
    warnings: list[str] = []
    executable = choice.grade in SPECIFIC_GRADES
    if not executable:
        warnings.append(
            f"association is weak (grade {choice.grade}): the shot may merely resemble the passage"
        )
    if not passage:
        executable = False
        warnings.append("the passage text is unavailable, so contradiction could not be checked")
    elif negates(passage):
        executable = False
        warnings.append(
            "the passage negates, doubts or imagines what it names; the footage could imply "
            "something the narration does not say"
        )
    return CoverCandidate(
        asset_id=choice.asset_id,
        shot_id=choice.shot_id,
        range=make_range(
            target.source, begin, begin + length, Precision.FRAME_EXACT, Precision.ESTIMATED
        ),
        grade=choice.grade,
        rank=choice.rank,
        executability=Executability.EXECUTABLE if executable else Executability.NEEDS_REVIEW,
        warnings=tuple(warnings),
    )


def _unfit(
    shot: ShotFact,
    target: AssetEvidence,
    choice: ShotChoice,
    span_start: Fraction,
    span_end: Fraction,
) -> str | None:
    if (
        choice.asset_id == target.source.asset_id
        and exact(shot.start) < span_end
        and exact(shot.end) > span_start
    ):
        return "it is the shot already on screen"
    if shot.usability is not None and shot.usability < _MIN_USABILITY:
        return f"technical usability {shot.usability:.2f} is too low"
    if shot.indicators:
        return "flagged as " + ", ".join(shot.indicators)
    if target.alignment is not None and choice.shot_id in target.alignment.speaking_shots():
        return "a visible face is speaking in it (lips would not match the narration)"
    if target.source.duration is not None and exact(shot.end) > exact(
        target.source.duration
    ) + Fraction(1, 10):
        return "it runs past the end of the recording"
    return None


def _cover_placement(
    chosen: CoverCandidate,
    visual: VisualFact,
    request_id: str,
    index: int,
    span_start: Fraction,
    span_end: Fraction,
    piece: Piece,
    timeline: Timeline,
    pieces: Sequence[Piece],
    evidence: Mapping[str, AssetEvidence],
    rate: Rational,
    profile: EditingProfile,
    out: CoverageResult,
) -> Placement:
    start = timeline.starts[index] + (span_start - piece.start)
    length = to_fraction(chosen.range.end) - to_fraction(chosen.range.start)
    source_start = to_fraction(chosen.range.start)
    source_end = to_fraction(chosen.range.end)
    bridge = _bridge(index, pieces, timeline, start, length, piece, chosen, evidence, profile)
    if bridge is not None:
        extra, boundary = bridge
        length += extra
        source_end += extra
        out.bridges.append(
            AudioBridge(
                bridge_id=f"bridge:{visual.visual_id}",
                kind=BridgeKind.BRIDGE,
                audio_changes_at=to_rational(boundary),
                picture_changes_at=to_rational(start),
                placement_ids=(f"cover:{request_id}",),
                reason=(
                    "the cover starts before the sound is joined and ends after it, so the "
                    "cut in the speech is not seen"
                ),
            )
        )
        out.decisions.append(
            Decision(
                decision_id=f"brg:{visual.visual_id}",
                action=Action.BRIDGE_AUDIO,
                origin=Origin.AUTOMATIC,
                asset_id=visual.source.asset_id,
                section_id=visual.section_id,
                beat_id=None,
                subject=None,
                unit_ids=(),
                signals=(),
                executability=chosen.executability,
                rationale=("the cover spans the cut in the speech (L-cut out, J-cut in)",),
                evidence=(EvidenceRef(kind=EvidenceKind.STORY_VISUAL, ref=visual.visual_id),),
            )
        )
    first = frame_round(start, rate)
    frames = max(1, frame_round(start + length, rate) - first)
    rng = make_range(
        evidence[chosen.asset_id].source,
        source_start,
        source_end,
        chosen.range.start_precision,
        chosen.range.end_precision,
    )
    return Placement(
        placement_id=f"cover:{request_id}",
        track=TrackKind.VIDEO,
        layer=1,
        role=PlacementRole.COVER,
        source=rng,
        timeline_start=to_rational(start),
        timeline_duration=to_rational(length),
        start_frame=first,
        duration_frames=frames,
        section_id=visual.section_id,
        beat_id=None,
        decision_ids=(request_id,),
        audio=AudioBehavior.MUTED,
        fade_in=Rational(0, 1),
        fade_out=Rational(0, 1),
        warnings=chosen.warnings,
    )


def _bridge(
    index: int,
    pieces: Sequence[Piece],
    timeline: Timeline,
    start: Fraction,
    length: Fraction,
    piece: Piece,
    chosen: CoverCandidate,
    evidence: Mapping[str, AssetEvidence],
    profile: EditingProfile,
) -> tuple[Fraction, Fraction] | None:
    """Extend the cover over a real cut in the speech at the end of its piece (J/L cut)."""
    if not profile.bridge_audio or piece.cut_out is None or index + 1 >= len(pieces):
        return None
    following = pieces[index + 1]
    boundary = timeline.starts[index] + piece.duration
    if following.cut_in is None or abs(start + length - boundary) > _BRIDGE_EDGE:
        return None
    reach = min(_BRIDGE_REACH, following.duration / 3)
    wanted = max(Fraction(0), boundary - (start + length)) + reach
    video = evidence[chosen.asset_id].video
    shot = video.shot(chosen.shot_id) if video is not None else None
    if shot is None or exact(shot.end) - to_fraction(chosen.range.end) < wanted:
        return None
    return wanted, boundary


def _graphic(
    visual: VisualFact,
    request_id: str,
    source_range: SourceRange,
    need: Signal,
    timeline: Timeline,
    index: int,
    span_start: Fraction,
    piece: Piece,
    out: CoverageResult,
    effect_id: Callable[[], str],
) -> None:
    at = timeline.starts[index] + (span_start - piece.start)
    intent = _GRAPHIC_PURPOSES[visual.purpose]
    out.effects.append(
        EffectRequest(
            request_id=effect_id(),
            kind=EffectKind.GRAPHIC,
            intent=intent,
            at=to_rational(at),
            duration=None,
            placement_id=None,
            section_id=visual.section_id,
            priority=need.value or 0.0,
            reason=f"{visual.purpose} for '{visual.concept}' (the story names it; nothing is "
            "drawn here)",
            decision_id=request_id,
        )
    )
    out.requests.append(
        CoverageRequest(
            request_id=request_id,
            section_id=visual.section_id,
            purpose=visual.purpose,
            necessity=visual.necessity,
            concept=visual.concept,
            span=source_range,
            need=need,
            chosen=None,
            alternatives=(),
            placement_id=None,
            executability=Executability.NEEDS_REVIEW,
            notes=("a graphic is requested from a later stage",),
            decision_id=request_id,
        )
    )
    out.decisions.append(
        Decision(
            decision_id=request_id,
            action=Action.REQUEST_EFFECT,
            origin=Origin.AUTOMATIC,
            asset_id=visual.source.asset_id,
            section_id=visual.section_id,
            beat_id=None,
            subject=source_range,
            unit_ids=visual.source.unit_ids,
            signals=(need,),
            executability=Executability.NEEDS_REVIEW,
            rationale=(
                f"a {visual.purpose.replace('_', ' ')} is requested for '{visual.concept}'",
            ),
            evidence=(EvidenceRef(kind=EvidenceKind.STORY_VISUAL, ref=visual.visual_id),),
        )
    )


def _evidence(visual: VisualFact, chosen: CoverCandidate | None) -> tuple[EvidenceRef, ...]:
    refs = [EvidenceRef(kind=EvidenceKind.STORY_VISUAL, ref=visual.visual_id)]
    if chosen is not None:
        refs.append(
            EvidenceRef(
                kind=EvidenceKind.ALIGNMENT_MATCH,
                ref=chosen.shot_id,
                asset_id=chosen.asset_id,
                detail=f"grade {chosen.grade}, rank {chosen.rank}",
            )
        )
        refs.append(
            EvidenceRef(kind=EvidenceKind.VIDEO_SHOT, ref=chosen.shot_id, asset_id=chosen.asset_id)
        )
    return tuple(refs)
