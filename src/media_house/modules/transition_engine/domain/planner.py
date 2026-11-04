"""The transition decision: is a transition needed at this boundary, and if so which one.

Order of reasoning per boundary (main picture layer, in timeline order):

1. A person's decision wins (``USE`` a kind, or keep a ``HARD_CUT``); only technical limits
   (handles, room in the clips) can still refuse it, and that is recorded.
2. Continuous material (one recording, adjacent frames) and gaps stay hard cuts.
3. Brand bookends (membership plug, end screen) take the identity's transition.
4. The contexts of the boundary (editorial request, section change, reveal, change of time or
   scene, montage, jump cut) name candidate kinds from the profile, most important context
   first. NO context with candidates -> hard cut (the default, with the reason).
5. Each candidate is checked: approved vocabulary, intensity ceiling (profile and style),
   budget (whole-edit cap, spacing, expressive spacing/count, no two expressive in a row),
   speech running across the cut, picture compatibility (motion, ghosting, composition, text)
   and timing (handles, room in both clips; shortened or realigned when needed). The first
   that passes is used; when none passes the cut stays hard and the fallback is recorded.

Audio is planned separately (``_audio``): J/L cuts from the edit are kept exactly (the
picture never moves), every crossfade is equal-power by default and is shortened so that no
word is faded or leaked from a handle.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import pairwise

from media_house.modules.transition_engine.domain.facts import (
    AudioBridgeMark,
    Bookend,
    ContinuityFact,
    EdgeFact,
    PacingCue,
    SourceFacts,
    StyleLimits,
    TransitionClip,
    TransitionSequence,
    fr,
    rat,
)
from media_house.modules.transition_engine.domain.model import (
    AudioTransition,
    Fallback,
    Handles,
    Overlay,
    QcIssue,
    Rejection,
    Transition,
)
from media_house.modules.transition_engine.domain.profile import (
    TransitionConfig,
    TransitionDecision,
    TransitionProfile,
)
from media_house.modules.transition_engine.domain.timing import (
    Fit,
    available_after,
    available_before,
    fit,
    frame_of,
    samples,
    speech_safe_fade,
    time_of,
    words_near,
)
from media_house.modules.transition_engine.domain.values import (
    CUT_KINDS,
    DEFAULT_SAMPLE_RATE,
    LIBRARY,
    REVEAL_FUNCTIONS,
    Alignment,
    AudioKind,
    BookendKind,
    BoundaryKind,
    Context,
    Curve,
    DecisionAction,
    DecisionStatus,
    Family,
    QcCategory,
    Severity,
    TransitionKind,
)

#: Context -> the profile's candidate list, most important first.
_CONTEXT_LISTS: tuple[tuple[Context, str], ...] = (
    (Context.REQUESTED, "requested"),
    (Context.SECTION_CHANGE, "section_change"),
    (Context.REVEAL, "reveal"),
    (Context.TIME_CHANGE, "time_change"),
    (Context.SCENE_CHANGE, "scene_change"),
    (Context.MONTAGE, "montage"),
    (Context.JUMP_CUT, "jump_cut"),
)
_MOVING = frozenset({"pan", "tilt", "push_in", "pull_out", "mixed"})
_PANS = frozenset({"pan", "tilt"})
#: Room for the picture after an end screen begins (it lasts about 20 s).
_UNBOUNDED = 10**6


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanInputs:
    sequence: TransitionSequence
    sources: Mapping[str, SourceFacts]
    #: ``(clip_id, "out" | "in")`` -> the picture at that side of the clip.
    edges: Mapping[tuple[str, str], EdgeFact] = field(default_factory=dict)
    #: ``(outgoing clip id, incoming clip id)`` -> continuity's judgement of that cut.
    continuity: Mapping[tuple[str, str], ContinuityFact] = field(default_factory=dict)
    pacing: tuple[PacingCue, ...] = ()
    style: StyleLimits | None = None
    decisions: Mapping[str, TransitionDecision] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class _Boundary:
    transition_id: str
    boundary: BoundaryKind
    out: TransitionClip | None
    inc: TransitionClip | None
    section_id: str | None
    cut: Fraction
    cut_frame: int
    room_out: int
    room_in: int
    contexts: list[Context] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    request_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Choice:
    kind: TransitionKind
    fit: Fit
    direction: str


@dataclass(slots=True, kw_only=True)
class Planned:
    transitions: list[Transition] = field(default_factory=list)
    issues: list[QcIssue] = field(default_factory=list)


def transition_id(out_clip_id: str | None, in_clip_id: str | None) -> str:
    """Stable from the adjacent clip ids, so person decisions survive replans."""
    if out_clip_id is None:
        return f"start:{in_clip_id}"
    if in_clip_id is None:
        return f"end:{out_clip_id}"
    return f"cut:{out_clip_id}>{in_clip_id}"


def plan_boundaries(
    inputs: PlanInputs, profile: TransitionProfile, config: TransitionConfig
) -> Planned:
    return _Planner(inputs, profile, config).run()


class _Planner:
    def __init__(
        self, inputs: PlanInputs, profile: TransitionProfile, config: TransitionConfig
    ) -> None:
        self.inputs = inputs
        self.seq = inputs.sequence
        self.profile = profile
        self.config = config
        self.fps = fr(self.seq.frame_rate)
        self.main = self.seq.main()
        self.out = Planned()
        end = max(c.timeline_end for c in self.main)
        self.cap = math.ceil(profile.budget.per_minute * end / 60) if end > 0 else 0
        self.count = 0
        self.expressive = 0
        self.last_end: Fraction | None = None
        self.last_expressive: Fraction | None = None
        self.prev_expressive = False
        self.head_used: dict[str, int] = {}

    # --- boundaries ---------------------------------------------------------------------------
    def run(self) -> Planned:
        first, last = self.main[0], self.main[-1]
        self._decide(self._start(first))
        for out, inc in pairwise(self.main):
            self._decide(self._cut(out, inc))
        self._decide(self._end(last))
        self._unplaced_bookends()
        return self.out

    def _frames(self, clip: TransitionClip) -> int:
        return frame_of(clip.timeline_end, self.fps) - frame_of(fr(clip.timeline_start), self.fps)

    def _start(self, first: TransitionClip) -> _Boundary:
        cut = fr(first.timeline_start)
        return _Boundary(
            transition_id=transition_id(None, first.clip_id),
            boundary=BoundaryKind.SEQUENCE_START,
            out=None,
            inc=first,
            section_id=first.section_id,
            cut=cut,
            cut_frame=frame_of(cut, self.fps),
            room_out=0,
            room_in=self._frames(first) // 2,
        )

    def _end(self, last: TransitionClip) -> _Boundary:
        end_screen = self._bookend(BookendKind.END_SCREEN, last.timeline_end)
        cut = last.timeline_end
        b = _Boundary(
            transition_id=transition_id(last.clip_id, None),
            boundary=BoundaryKind.SEQUENCE_END,
            out=last,
            inc=None,
            section_id=last.section_id,
            cut=cut,
            cut_frame=frame_of(cut, self.fps),
            room_out=self._frames(last) - self.head_used.get(last.clip_id, 0),
            room_in=_UNBOUNDED if end_screen is not None else 0,
        )
        if end_screen is not None:
            b.contexts.append(Context.END_SCREEN)
            b.evidence.append(f"end screen at {float(fr(end_screen.timeline_at)):.3f} s")
        return b

    def _cut(self, out: TransitionClip, inc: TransitionClip) -> _Boundary:
        cut = fr(inc.timeline_start)
        b = _Boundary(
            transition_id=transition_id(out.clip_id, inc.clip_id),
            boundary=BoundaryKind.CUT,
            out=out,
            inc=inc,
            section_id=inc.section_id,
            cut=cut,
            cut_frame=frame_of(cut, self.fps),
            room_out=self._frames(out) - self.head_used.get(out.clip_id, 0),
            room_in=self._frames(inc) // 2,
        )
        self._contexts(b, out, inc)
        return b

    def _contexts(self, b: _Boundary, out: TransitionClip, inc: TransitionClip) -> None:
        p, frame = self.profile, Fraction(1) / self.fps
        ctx, ev = b.contexts, b.evidence
        gap = fr(inc.timeline_start) - out.timeline_end
        if abs(gap) > frame / 2:
            ctx.append(Context.GAP)
            ev.append(f"{'gap' if gap > 0 else 'overlap'} of {float(abs(gap)):.3f} s at the cut")
        skip = fr(inc.source_start) - fr(out.source_end)
        oe, ie = self._edge(out, "out"), self._edge(inc, "in")
        if out.asset_id == inc.asset_id:
            if abs(skip) <= frame:
                ctx.append(Context.CONTINUOUS)
            elif 0 < skip <= Fraction(str(p.detection.time_jump)):
                ctx.append(Context.JUMP_CUT)
                ev.append(f"same take, {float(skip):.2f} s skipped")
            else:
                ctx.append(Context.TIME_CHANGE)
                ev.append(f"same recording, time moves by {float(skip):+.2f} s")
        elif oe is not None and ie is not None:
            if _scene_changes(oe, ie):
                ctx.append(Context.SCENE_CHANGE)
                ev.append(
                    f"scene {oe.scene_id or oe.environment} -> {ie.scene_id or ie.environment}"
                )
            else:
                ctx.append(Context.ANGLE_CHANGE)
                ev.append("another camera in the same scene")
        else:
            ctx.append(Context.SOURCE_CHANGE)
            ev.append("another recording (no picture analysis to tell scene from angle)")
        if inc.section_id is not None and inc.section_id != out.section_id:
            ctx.append(Context.SECTION_CHANGE)
            ev.append(f"section {out.section_id} -> {inc.section_id}{self._purpose(inc)}")
        tolerance = Fraction(str(p.detection.request_tolerance))
        for r in self.seq.requests:
            near = abs(fr(r.timeline_at) - b.cut) <= tolerance
            into = r.section_id is not None and r.section_id == inc.section_id != out.section_id
            if near or into:
                b.request_ids.append(r.request_id)
        if b.request_ids:
            ctx.append(Context.REQUESTED)
            ev.append("editorial transition request " + ", ".join(b.request_ids))
        cues = self._cues(out, inc)
        if (inc.beat_function in REVEAL_FUNCTIONS and inc.beat_id != out.beat_id) or (
            "hold_reveal" in cues
        ):
            ctx.append(Context.REVEAL)
            ev.append(f"reveal ({inc.beat_function or 'pacing hold_reveal'})")
        if "add_section_breath" in cues:
            ctx.append(Context.BREATH)
            ev.append("pacing asks for a section breath")
        short = Fraction(str(p.detection.montage_max_clip))
        if out.duration <= short and inc.duration <= short:
            ctx.append(Context.MONTAGE)
        cont = self.inputs.continuity.get((out.clip_id, inc.clip_id))
        if cont is not None:
            ev += [f"continuity: {i}" for i in cont.issues]
            if (
                cont.compatibility is not None
                and cont.compatibility >= p.detection.match_compatibility
                and Context.SCENE_CHANGE not in ctx
            ):
                ctx.append(Context.MATCH)
        if _moving_together(oe, ie, p):
            ctx.append(Context.ACTION)
        reach = Fraction(str(p.detection.speech_reach))
        so, si = self._source(out), self._source(inc)
        if (
            so is not None
            and si is not None
            and words_near(so.words, fr(out.source_end), reach)
            and words_near(si.words, fr(inc.source_start), reach)
        ):
            ctx.append(Context.SPEECH_ACROSS)
        if self._bridge(b.cut) is not None:
            ctx.append(Context.AUDIO_BRIDGE)
        if self._bookend(BookendKind.MEMBERSHIP_PLUG, b.cut) is not None:
            ctx.append(Context.MEMBERSHIP_PLUG)

    # --- decision -----------------------------------------------------------------------------
    def _decide(self, b: _Boundary) -> None:
        p = self.config.for_section(self.profile, b.section_id)
        decision = self.inputs.decisions.get(b.transition_id)
        rejected: list[Rejection] = []
        reasons: list[str] = []
        fallback: Fallback | None = None
        choice: _Choice | None = None
        status = DecisionStatus.NOT_NEEDED
        confidence = 0.9 if self._evidenced(b) else 0.7
        wanted: list[TransitionKind] = []
        note = ""
        if decision is not None:
            note = decision.note
            status = DecisionStatus.OVERRIDDEN
            confidence = 1.0
            if decision.action is DecisionAction.HARD_CUT or decision.kind is None:
                reasons.append("a person chose a hard cut")
            else:
                wanted = [decision.kind]
                choice, why = self._try(b, decision.kind, p, frames=decision.duration_frames)
                if choice is None:
                    rejected.append(Rejection(kind=decision.kind, reason=why))
                    fallback = Fallback(wanted=decision.kind, used=self._cut_label(b), reason=why)
                    status = DecisionStatus.FALLBACK
                    reasons.append(f"a person asked for {decision.kind}, which cannot work: {why}")
                else:
                    reasons.append(f"a person chose {decision.kind}")
        elif Context.CONTINUOUS in b.contexts:
            reasons.append("continuous material: the cut joins adjacent frames of one recording")
        elif Context.GAP in b.contexts:
            reasons.append("the clips do not meet: a transition cannot be placed on a gap")
        elif brand := self._brand_kind(b, p):
            kind, rule = brand
            status, confidence = DecisionStatus.BRAND, 0.9
            if kind is TransitionKind.HARD_CUT:
                reasons.append(f"brand bookend: {rule} asks for a hard cut")
            else:
                wanted = [kind]
                choice, why = self._try(b, kind, p, brand=True)
                if choice is None:
                    rejected.append(Rejection(kind=kind, reason=why))
                    fallback = Fallback(wanted=kind, used=self._cut_label(b), reason=why)
                    status = DecisionStatus.FALLBACK
                    reasons.append(f"brand bookend ({rule}) not possible: {why}")
                else:
                    reasons.append(f"brand bookend: {rule}")
        else:
            wanted = self._candidates(b, p)
            requested = Context.REQUESTED in b.contexts
            for kind in wanted:
                choice, why = self._try(b, kind, p, budget=not requested)
                if choice is not None:
                    break
                rejected.append(Rejection(kind=kind, reason=why))
            if choice is not None:
                status = DecisionStatus.REQUESTED if requested else DecisionStatus.RECOMMENDED
                confidence = self._confidence(b, choice.kind)
                reasons.append(self._why(b, choice.kind))
            elif wanted:
                status = DecisionStatus.FALLBACK
                confidence = 0.8
                fallback = Fallback(
                    wanted=wanted[0], used=self._cut_label(b), reason=rejected[0].reason
                )
                reasons.append(f"{wanted[0]} was wanted but {rejected[0].reason}: hard cut")
            else:
                reasons.append(self._why_cut(b))
        self._emit(b, choice, status, reasons, confidence, rejected, fallback, note, p)

    def _candidates(self, b: _Boundary, p: TransitionProfile) -> list[TransitionKind]:
        if b.boundary is BoundaryKind.SEQUENCE_START:
            names: tuple[str, ...] = p.contexts.sequence_start
        elif b.boundary is BoundaryKind.SEQUENCE_END:
            names = p.contexts.sequence_end
        else:
            names = tuple(
                n
                for context, attr in _CONTEXT_LISTS
                if context in b.contexts
                for n in getattr(p.contexts, attr)
            )
        out: list[TransitionKind] = []
        for n in names:
            kind = TransitionKind(n)
            if kind not in out and kind not in CUT_KINDS:
                out.append(kind)
        return out

    def _brand_kind(self, b: _Boundary, p: TransitionProfile) -> tuple[TransitionKind, str] | None:
        style = self.inputs.style if p.bookends.follow_style else None
        if Context.MEMBERSHIP_PLUG in b.contexts:
            if style is not None:
                return TransitionKind(style.membership_transition), (
                    "style end_screen.membership_transition"
                )
            return TransitionKind(p.bookends.membership_plug), "profile bookends.membership_plug"
        if Context.END_SCREEN in b.contexts:
            if style is not None:
                return TransitionKind(style.end_screen_transition), (
                    "style end_screen.entry_transition"
                )
            return TransitionKind(p.bookends.end_screen), "profile bookends.end_screen"
        return None

    def _try(
        self,
        b: _Boundary,
        kind: TransitionKind,
        p: TransitionProfile,
        *,
        budget: bool = True,
        brand: bool = False,
        frames: int | None = None,
    ) -> tuple[_Choice | None, str]:
        spec = LIBRARY[kind]
        checked = not brand and b.transition_id not in self.inputs.decisions
        if checked:
            if kind.value not in p.vocabulary.allowed or kind.value in p.vocabulary.prohibited:
                return None, "not approved by the profile"
            why = self._identity_refuses(kind)
            if why:
                return None, why
            ceiling = self._ceiling(p, b.section_id)
            if spec.intensity > ceiling:
                return None, f"intensity {spec.intensity} is above the ceiling {ceiling}"
            if budget and b.boundary is BoundaryKind.CUT:
                why = self._budget(b, spec.intensity, p)
                if why:
                    return None, why
            if Context.SPEECH_ACROSS in b.contexts and spec.interrupts:
                return None, "the sentence runs across the cut: it must not be interrupted"
            why = self._compatible(b, kind, p)
            if why:
                return None, why
        wanted = frames if frames is not None else self._wanted_frames(b, kind, p)
        alignment = spec.alignment
        if spec.alignment is Alignment.CENTER:
            alignment = Alignment(p.durations.alignment)
        so = self._source(b.out) if b.out is not None else None
        si = self._source(b.inc) if b.inc is not None else None
        safety = p.detection.vfr_safety_frames
        handle_out = available_after(so, fr(b.out.source_end), self.fps, safety) if b.out else None
        handle_in = (
            available_before(si, fr(b.inc.source_start), self.fps, safety) if b.inc else None
        )
        found = fit(
            wanted=wanted,
            minimum=min(p.durations.min_frames, wanted),
            alignment=alignment,
            overlap=spec.overlap,
            handle_out=handle_out,
            handle_in=handle_in,
            room_out=b.room_out,
            room_in=b.room_in,
        )
        if found is None:
            if spec.overlap and (handle_out is None or handle_in is None):
                return None, "the source length is unknown, so the handles cannot be checked"
            if spec.overlap:
                return None, (
                    f"insufficient handles (outgoing {handle_out}, incoming {handle_in} frames;"
                    f" at least {min(p.durations.min_frames, wanted)} needed)"
                )
            return None, "the clips are too short for it"
        return _Choice(kind=kind, fit=found, direction=self._direction(b, kind)), ""

    def _budget(self, b: _Boundary, intensity: int, p: TransitionProfile) -> str:
        bud = p.budget
        if intensity == 0:
            return ""
        if self.count + 1 > self.cap:
            return f"the budget of {self.cap} transitions for this edit is used"
        if self.last_end is not None and b.cut - self.last_end < Fraction(str(bud.min_spacing)):
            return f"only {float(b.cut - self.last_end):.1f} s after the previous transition"
        if intensity >= 3:
            if self.prev_expressive:
                return "the previous boundary already has an expressive transition"
            if self.expressive >= bud.max_expressive:
                return f"the {bud.max_expressive} expressive transitions of this edit are used"
            if self.last_expressive is not None and b.cut - self.last_expressive < Fraction(
                str(bud.expressive_spacing)
            ):
                return "too close to the previous expressive transition"
        return ""

    def _compatible(self, b: _Boundary, kind: TransitionKind, p: TransitionProfile) -> str:
        spec, c = LIBRARY[kind], p.compatibility
        oe = self._edge(b.out, "out") if b.out else None
        ie = self._edge(b.inc, "in") if b.inc else None
        if spec.motion:
            if oe is None or ie is None:
                return "no picture analysis: camera motion at the cut is unknown"
            if kind is TransitionKind.WHIP_PAN:
                pans = [e for e in (oe, ie) if e.camera_movement in _PANS]
                if not pans:
                    return "neither side pans: a whip pan would invent camera motion"
            else:
                pans = [e for e in (oe, ie) if e.camera_movement in _MOVING]
                if len(pans) < 2:
                    return "both sides must move for a motion match"
            dirs = [e.camera_direction for e in pans if e.camera_direction is not None]
            if len(dirs) == 2 and _angle(dirs[0], dirs[1]) > c.direction_tolerance:
                return "the cameras move in different directions"
        if (
            spec.moves_frame
            and c.protect_text
            and ((oe is not None and oe.has_text) or (ie is not None and ie.has_text))
        ):
            return "on-screen text would be moved or covered"
        if spec.overlap and spec.family is Family.BLEND and _ghosting(oe, ie, p):
            return "subjects move apart on both sides: a dissolve would ghost"
        if kind is TransitionKind.ZOOM and _shifted(oe, ie, c.composition_shift):
            return "the compositions differ too much for a zoom"
        return ""

    # --- emit ---------------------------------------------------------------------------------
    def _emit(
        self,
        b: _Boundary,
        choice: _Choice | None,
        status: DecisionStatus,
        reasons: list[str],
        confidence: float,
        rejected: list[Rejection],
        fallback: Fallback | None,
        note: str,
        p: TransitionProfile,
    ) -> None:
        kind = choice.kind if choice is not None else self._cut_label(b)
        spec = LIBRARY[kind]
        before, after = (choice.fit.before, choice.fit.after) if choice else (0, 0)
        alignment = choice.fit.alignment if choice else Alignment.CENTER
        if choice is not None and choice.fit.shortened:
            reasons.append(f"shortened to {choice.fit.frames} frames to fit the handles/clips")
        if choice is not None and choice.fit.realigned:
            reasons.append(f"aligned to {alignment} of the cut: the other side has no handle")
        start, end = b.cut_frame - before, b.cut_frame + after
        so = self._source(b.out) if b.out is not None else None
        si = self._source(b.inc) if b.inc is not None else None
        safety = p.detection.vfr_safety_frames
        handles = Handles(
            required_out=after if spec.overlap else 0,
            required_in=before if spec.overlap else 0,
            available_out=available_after(so, fr(b.out.source_end), self.fps, safety)
            if b.out
            else None,
            available_in=available_before(si, fr(b.inc.source_start), self.fps, safety)
            if b.inc
            else None,
        )
        overlay = None
        if spec.overlay is not None and choice is not None:
            overlay = Overlay(
                request_id=f"tx:{b.transition_id}",
                overlay=spec.overlay,
                timeline_start=rat(time_of(start, self.fps)),
                local_duration=rat(time_of(end - start, self.fps)),
            )
        audio = self._audio(b, choice, p)
        transition = Transition(
            transition_id=b.transition_id,
            boundary=b.boundary,
            outgoing_clip_id=b.out.clip_id if b.out else None,
            incoming_clip_id=b.inc.clip_id if b.inc else None,
            section_id=b.section_id,
            cut_at=rat(time_of(b.cut_frame, self.fps)),
            cut_frame=b.cut_frame,
            contexts=tuple(b.contexts),
            status=status,
            required=kind not in CUT_KINDS,
            kind=kind,
            family=spec.family,
            intensity=spec.intensity,
            preset_id=f"{self.profile.name}.{kind.value}",
            alignment=alignment,
            duration_frames=end - start,
            duration=rat(time_of(end - start, self.fps)),
            timeline_start=rat(time_of(start, self.fps)),
            timeline_end=rat(time_of(end, self.fps)),
            start_frame=start,
            end_frame=end,
            direction=choice.direction if choice else "",
            handles=handles,
            audio=audio,
            reasons=tuple(reasons),
            confidence=round(confidence, 3),
            evidence=tuple(b.evidence),
            rejected=tuple(rejected),
            fallback=fallback,
            overlay=overlay,
            decision_note=note,
        )
        self.out.transitions.append(transition)
        self._account(b, transition)

    def _account(self, b: _Boundary, t: Transition) -> None:
        if b.inc is not None:
            self.head_used[b.inc.clip_id] = t.end_frame - t.cut_frame
        if t.kind in CUT_KINDS or b.boundary is not BoundaryKind.CUT:
            # The opening and closing of the edit are bookends: they use no budget.
            self.prev_expressive = False
            return
        if t.status in (DecisionStatus.RECOMMENDED, DecisionStatus.REQUESTED):
            self.count += 1
        self.last_end = fr(t.timeline_end)
        self.prev_expressive = t.intensity >= 3
        if t.intensity >= 3:
            self.expressive += 1
            self.last_expressive = b.cut

    # --- audio --------------------------------------------------------------------------------
    def _audio(self, b: _Boundary, choice: _Choice | None, p: TransitionProfile) -> AudioTransition:
        a = p.audio
        ms = Fraction(1, 1000)
        so = self._source(b.out) if b.out is not None else None
        si = self._source(b.inc) if b.inc is not None else None
        rate = (si.sample_rate if si else None) or (so.sample_rate if so else None)
        rate = rate or DEFAULT_SAMPLE_RATE
        curve = Curve(a.curve)
        reasons: list[str] = []
        at = b.cut
        out_point = fr(b.out.source_end) if b.out is not None else None
        in_point = fr(b.inc.source_start) if b.inc is not None else None
        if b.boundary is BoundaryKind.SEQUENCE_START:
            kind, before, after = AudioKind.FADE_IN, Fraction(0), a.head_fade_ms * ms
            reasons.append("fade in at the start of the edit")
        elif b.boundary is BoundaryKind.SEQUENCE_END:
            kind, before, after = AudioKind.FADE_OUT, a.tail_fade_ms * ms, Fraction(0)
            reasons.append("dialogue tail faded at the end (music is music_and_sound_design's)")
        elif Context.CONTINUOUS in b.contexts:
            kind, before, after = AudioKind.NONE, Fraction(0), Fraction(0)
            reasons.append("continuous recording: no audio edit")
        elif (bridge := self._bridge(b.cut)) is not None:
            at = fr(bridge.audio_changes_at)
            shift = at - b.cut
            out_point = (out_point or Fraction(0)) + shift
            in_point = (in_point or Fraction(0)) + shift
            kind = {"j_cut": AudioKind.J_CUT, "l_cut": AudioKind.L_CUT}.get(
                bridge.kind, AudioKind.CROSSFADE
            )
            before = after = a.bridge_crossfade_ms * ms / 2
            reasons.append(
                f"{bridge.kind} from the edit: the sound changes {float(shift):+.3f} s from the"
                " picture (the picture is not moved)"
            )
        else:
            cont = (
                self.inputs.continuity.get((b.out.clip_id, b.inc.clip_id))
                if b.out is not None and b.inc is not None
                else None
            )
            if cont is not None and cont.audio_crossfade:
                length = a.repair_crossfade_ms * ms
                reasons.append("continuity asks for a crossfade to smooth the sound at this cut")
            elif choice is not None and LIBRARY[choice.kind].overlap and a.follow_picture:
                length = min(time_of(choice.fit.frames, self.fps), a.max_follow_ms * ms)
                reasons.append("crossfade with the picture transition")
            else:
                length = a.cut_crossfade_ms * ms
                reasons.append("click guard at the cut")
            kind = AudioKind.CROSSFADE if length > a.cut_crossfade_ms * ms else AudioKind.CUT
            before = after = length / 2
        if kind is not AudioKind.NONE:
            before, after, protected = self._protect(
                b, so, si, out_point, in_point, before, after, p
            )
            if protected:
                reasons.append("shortened so no word is faded or leaked")
        else:
            protected = ()
        if kind in (AudioKind.FADE_IN, AudioKind.FADE_OUT) and before + after == 0:
            kind, curve = AudioKind.NONE, Curve.NONE
            reasons.append("no fade: the profile keeps full level (e.g. a looping Short)")
        if kind is AudioKind.NONE:
            curve = Curve.NONE
        sb, sa = samples(before, rate), samples(after, rate)
        return AudioTransition(
            kind=kind,
            curve=curve,
            timeline_at=rat(at),
            before=rat(before),
            after=rat(after),
            samples_before=sb,
            samples_after=sa,
            sample_rate=rate,
            duration_ms=round((sb + sa) * 1000 / rate, 3),
            protected=protected,
            reasons=tuple(reasons),
        )

    def _protect(
        self,
        b: _Boundary,
        so: SourceFacts | None,
        si: SourceFacts | None,
        out_point: Fraction | None,
        in_point: Fraction | None,
        before: Fraction,
        after: Fraction,
        p: TransitionProfile,
    ) -> tuple[Fraction, Fraction, tuple[str, ...]]:
        ms = Fraction(1, 1000)
        fade = speech_safe_fade(
            before=before,
            after=after,
            out_point=out_point if b.out is not None else None,
            out_words=so.words if so else (),
            in_point=in_point if b.inc is not None else None,
            in_words=si.words if si else (),
            guard=p.audio.speech_guard_ms * ms,
            minimum=p.audio.min_ms * ms,
        )
        before, after = fade.before, fade.after
        # Material beyond the edit point on each side is handle audio: it must exist.
        if b.out is not None and out_point is not None and so is not None and so.end is not None:
            after = max(Fraction(0), min(after, so.end - out_point))
        if b.inc is not None and in_point is not None and si is not None:
            before = max(Fraction(0), min(before, in_point - si.start))
        return before, after, fade.protected

    # --- helpers ------------------------------------------------------------------------------
    def _source(self, clip: TransitionClip | None) -> SourceFacts | None:
        return None if clip is None else self.inputs.sources.get(clip.asset_id)

    def _edge(self, clip: TransitionClip | None, side: str) -> EdgeFact | None:
        return None if clip is None else self.inputs.edges.get((clip.clip_id, side))

    def _purpose(self, clip: TransitionClip) -> str:
        found = next((s for s in self.seq.sections if s.section_id == clip.section_id), None)
        return (
            f" ({found.label or found.purpose})" if found and (found.label or found.purpose) else ""
        )

    def _cues(self, out: TransitionClip, inc: TransitionClip) -> set[str]:
        reach = Fraction(str(self.profile.detection.request_tolerance))
        found: set[str] = set()
        for cue in self.inputs.pacing:
            for clip, point in ((out, fr(out.source_end)), (inc, fr(inc.source_start))):
                if cue.asset_id == clip.asset_id and cue.start - reach <= point <= cue.end + reach:
                    found.add(cue.kind)
        return found

    def _bridge(self, cut: Fraction) -> AudioBridgeMark | None:
        half = Fraction(1, 2) / self.fps
        return next(
            (m for m in self.seq.bridges if abs(fr(m.picture_changes_at) - cut) <= half), None
        )

    def _bookend(self, kind: BookendKind, at: Fraction) -> Bookend | None:
        tolerance = Fraction(str(self.profile.detection.request_tolerance))
        return next(
            (
                e
                for e in self.seq.bookends
                if e.kind is kind and abs(fr(e.timeline_at) - at) <= tolerance
            ),
            None,
        )

    def _unplaced_bookends(self) -> None:
        placed = {
            Context.MEMBERSHIP_PLUG: BookendKind.MEMBERSHIP_PLUG,
            Context.END_SCREEN: BookendKind.END_SCREEN,
        }
        seen = {placed[c] for t in self.out.transitions for c in t.contexts if c in placed}
        for e in self.seq.bookends:
            if e.kind not in seen:
                self.out.issues.append(
                    QcIssue(
                        code="bookend.not_at_cut",
                        severity=Severity.WARNING,
                        category=QcCategory.TECHNICAL,
                        message=f"the {e.kind.value} at {float(fr(e.timeline_at)):.3f} s is not"
                        " at a cut of the edit: no transition was planned for it",
                        timeline_start=e.timeline_at,
                    )
                )

    def _ceiling(self, p: TransitionProfile, section_id: str | None) -> int:
        style = self.inputs.style
        return min(p.budget.max_intensity, style.ceiling(section_id) if style else 4)

    def _wanted_frames(self, b: _Boundary, kind: TransitionKind, p: TransitionProfile) -> int:
        d, spec = p.durations, LIBRARY[kind]
        if kind in (TransitionKind.CROSS_DISSOLVE,):
            seconds = d.dissolve
        elif kind in (TransitionKind.DIP_TO_BLACK, TransitionKind.DIP_TO_WHITE):
            seconds = d.dip
        elif kind in (TransitionKind.FADE_IN, TransitionKind.FADE_OUT):
            seconds = d.fade
        elif spec.family is Family.MOVEMENT:
            seconds = d.movement
        elif spec.family is Family.STYLISED:
            seconds = d.stylised
        else:
            seconds = d.overlay
        value = Fraction(str(seconds))
        if Context.BREATH in b.contexts:
            value *= Fraction(str(d.breath_factor))
        if Context.MONTAGE in b.contexts:
            value *= Fraction(str(d.fast_factor))
        return max(d.min_frames, round(value * self.fps))

    def _identity_refuses(self, kind: TransitionKind) -> str:
        """The channel identity's transition language (v2 styles); brand bookends never get
        here (they ARE the identity's choice)."""
        style = self.inputs.style
        if style is None or not style.approved:
            return ""
        if kind.value in style.prohibited or kind.value not in style.approved:
            return "not in the channel identity's approved transitions"
        if kind.value in style.bookend_only:
            return "reserved by the channel identity for its brand bookend"
        return ""

    def _direction(self, b: _Boundary, kind: TransitionKind) -> str:
        if kind in (TransitionKind.ZOOM, TransitionKind.PUSH):
            return "in"
        if kind in (TransitionKind.SLIDE, TransitionKind.WIPE):
            return "left"
        if kind is TransitionKind.WHIP_PAN:
            for clip, side in ((b.out, "out"), (b.inc, "in")):
                e = self._edge(clip, side)
                if e is not None and e.camera_direction is not None:
                    return "right" if math.cos(math.radians(e.camera_direction)) >= 0 else "left"
            return "left"
        return ""

    def _cut_label(self, b: _Boundary) -> TransitionKind:
        if Context.JUMP_CUT in b.contexts:
            return TransitionKind.JUMP_CUT
        if Context.MATCH in b.contexts:
            return TransitionKind.MATCH_CUT
        if Context.ACTION in b.contexts:
            return TransitionKind.ACTION_CUT
        return TransitionKind.HARD_CUT

    def _evidenced(self, b: _Boundary) -> bool:
        if b.boundary is not BoundaryKind.CUT:
            return True
        return self._edge(b.out, "out") is not None and self._edge(b.inc, "in") is not None

    def _confidence(self, b: _Boundary, kind: TransitionKind) -> float:
        value = 0.7
        if Context.REQUESTED in b.contexts:
            value += 0.1
        if Context.SECTION_CHANGE in b.contexts or Context.BREATH in b.contexts:
            value += 0.05
        if not self._evidenced(b):
            value -= 0.15
        return max(0.3, min(0.95, value))

    def _why(self, b: _Boundary, kind: TransitionKind) -> str:
        context = next((c for c, _ in _CONTEXT_LISTS if c in b.contexts), None)
        if b.boundary is BoundaryKind.SEQUENCE_START:
            return f"{kind} opens the edit (profile)"
        if b.boundary is BoundaryKind.SEQUENCE_END:
            return f"{kind} closes the edit (profile)"
        label = context.value.replace("_", " ") if context else "this boundary"
        return f"{kind} marks the {label} (profile {self.profile.name})"

    def _why_cut(self, b: _Boundary) -> str:
        ctx = b.contexts
        if b.boundary is BoundaryKind.SEQUENCE_START:
            return "the edit starts on its first frame (no fade in this profile)"
        if b.boundary is BoundaryKind.SEQUENCE_END:
            return "the edit ends on a cut (no fade in this profile)"
        if Context.JUMP_CUT in ctx:
            return "jump cut inside one take: kept as a cut"
        if Context.SECTION_CHANGE in ctx:
            return "section change: this profile marks sections with a cut"
        if Context.MATCH in ctx:
            return "matching shots: a match cut keeps the continuity"
        if Context.ACTION in ctx:
            return "movement continues across the cut: a cut on action"
        if Context.ANGLE_CHANGE in ctx:
            return "another angle of the same scene: a cut keeps the action continuous"
        return "no narrative, spatial or temporal change that a transition would clarify"


# --- pure helpers -----------------------------------------------------------------------------
def _angle(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _scene_changes(a: EdgeFact, b: EdgeFact) -> bool:
    if a.scene_id is not None and b.scene_id is not None:
        return a.scene_id != b.scene_id
    pairs = ((a.environment, b.environment), (a.indoor_outdoor, b.indoor_outdoor))
    return any(x is not None and y is not None and x != y for x, y in pairs)


def _velocity(e: EdgeFact | None) -> tuple[float, float] | None:
    if e is None or e.subject_vx is None or e.subject_vy is None:
        return None
    return e.subject_vx, e.subject_vy


def _moving_together(a: EdgeFact | None, b: EdgeFact | None, p: TransitionProfile) -> bool:
    va, vb = _velocity(a), _velocity(b)
    if va is None or vb is None:
        return False
    speed = p.compatibility.ghosting_speed
    if math.hypot(*va) <= speed or math.hypot(*vb) <= speed:
        return False
    da = math.degrees(math.atan2(-va[1], va[0]))
    db = math.degrees(math.atan2(-vb[1], vb[0]))
    return _angle(da, db) <= p.compatibility.direction_tolerance


def _ghosting(a: EdgeFact | None, b: EdgeFact | None, p: TransitionProfile) -> bool:
    va, vb = _velocity(a), _velocity(b)
    if va is None or vb is None:
        return False
    speed = p.compatibility.ghosting_speed
    if math.hypot(*va) <= speed or math.hypot(*vb) <= speed:
        return False
    return not _moving_together(a, b, p)


def _shifted(a: EdgeFact | None, b: EdgeFact | None, limit: float) -> bool:
    if a is None or b is None:
        return False
    if a.subject_cx is None or a.subject_cy is None or b.subject_cx is None or b.subject_cy is None:
        return False
    return math.hypot(a.subject_cx - b.subject_cx, a.subject_cy - b.subject_cy) > limit
