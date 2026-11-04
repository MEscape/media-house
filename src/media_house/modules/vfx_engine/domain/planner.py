"""Choosing effects: requests and footage facts in, one treatment per clip out.

Restraint is the rule. An effect is applied only when an editorial or personal request asks for
it, or when the footage itself gives the profile a reason (a burst for slow motion, a face for
privacy, an eligible shot for a look the profile was chosen for) - never "because it is
available". Every candidate passes the same gates and keeps its reasons:

    person's rejection -> profile (``apply``) -> capability -> intensity ceiling (profile, style)
    -> repetition / budget (emphasis effects, strongest first) -> feasibility / "do not destroy
    good media" guards -> PLANNED

A clip nothing passes for gets an explicit ``no_effect`` decision: the original footage is used.
"""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from itertools import pairwise

from media_house.modules.vfx_engine.domain.facts import (
    FULL_FRAME,
    BangFact,
    Box,
    CropFact,
    FlashFact,
    ShotFact,
    SourceFacts,
    StyleLimits,
    TrackFact,
    TrackSample,
    VfxClip,
    VfxRequest,
    VfxShots,
    fr,
    rat,
)
from media_house.modules.vfx_engine.domain.model import (
    Effect,
    Mask,
    MaskKeyframe,
    Param,
    QcIssue,
    TimeMap,
    Treatment,
)
from media_house.modules.vfx_engine.domain.profile import EffectDecision, VfxConfig, VfxProfile
from media_house.modules.vfx_engine.domain.timing import (
    box_at,
    frame_of,
    retime_map,
    reverse_map,
    snap,
)
from media_house.modules.vfx_engine.domain.values import (
    INTENSITY,
    TIME_EFFECTS,
    AudioIntent,
    Backend,
    ClipAudio,
    Decision,
    EffectStatus,
    EffectType,
    Interpolation,
    MaskKind,
    QcCategory,
    RequestKind,
    Severity,
    TreatmentStatus,
)

#: Effects whose repetition and frequency are budgeted (they draw the eye to a moment).
EMPHASIS: frozenset[EffectType] = frozenset({EffectType.PUNCH_IN, *TIME_EFFECTS})
#: Treatments applied to the whole clip as the profile's look (consistent across clips).
LOOKS: tuple[EffectType, ...] = (
    EffectType.LENS_DISTORTION,
    EffectType.GLOW,
    EffectType.LIGHT_RAYS,
    EffectType.MOTION_BLUR,
    EffectType.STYLIZE,
    EffectType.CHROMATIC_ABERRATION,
    EffectType.VIGNETTE,
    EffectType.FILM_DAMAGE,
    EffectType.FILM_GRAIN,
)
#: Render order inside one treatment (time map first, grain last, composites on top).
ORDER: tuple[EffectType, ...] = (
    EffectType.SLOW_MOTION,
    EffectType.SPEED_RAMP,
    EffectType.FREEZE_FRAME,
    EffectType.REVERSE,
    EffectType.PRIVACY_BLUR,
    EffectType.BLUR,
    EffectType.PUNCH_IN,
    *LOOKS,
    EffectType.COMPOSITE,
)
#: What needs a capability this machine may not have, and why.
NEEDS: dict[EffectType, tuple[Backend, str]] = {
    EffectType.BACKGROUND_BLUR: (Backend.SUBJECT_MATTER, "needs a video subject matte"),
    EffectType.BACKGROUND_REPLACEMENT: (Backend.SUBJECT_MATTER, "needs a video subject matte"),
    EffectType.SUBJECT_ISOLATION: (Backend.SUBJECT_MATTER, "needs a video subject matte"),
    EffectType.OBJECT_REMOVAL: (Backend.INPAINTER, "needs a video inpainting model"),
    EffectType.SCREEN_REPLACEMENT: (
        Backend.PLANAR_TRACKER,
        "needs a planar track of the screen (video intelligence tracks boxes only)",
    ),
    EffectType.LENS_FLARE: (
        Backend.NONE,
        "needs a lens-flare element library; a drawn flare over real footage would be fake",
    ),
}
_SAFER = {
    EffectType.OBJECT_REMOVAL: "conceal the region instead (privacy request: blur or mosaic)",
    EffectType.BACKGROUND_BLUR: "keep the original background (no fake rectangular depth)",
    EffectType.SCREEN_REPLACEMENT: "use the recorded screen as it is, or a cover clip",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanInputs:
    shots: VfxShots
    sources: Mapping[str, SourceFacts]
    style: StyleLimits | None
    #: Effects whose backend is usable on this machine.
    available: frozenset[EffectType]
    config: VfxConfig = field(default_factory=VfxConfig)


@dataclass(frozen=True, slots=True, kw_only=True)
class Planned:
    treatments: tuple[Treatment, ...]
    checks: tuple[QcIssue, ...]
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _Candidate:
    clip: VfxClip
    effect: EffectType
    at: Fraction  # timeline seconds
    priority: float
    reason: str
    request: VfxRequest | None = None
    peak: Fraction | None = None  # source seconds of the moment (burst peak)


def digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def plan_treatments(inputs: PlanInputs, profile: VfxProfile) -> Planned:
    shots = inputs.shots
    fps = fr(shots.frame_rate)
    ceiling = profile.selection.intensity_ceiling
    if inputs.style is not None:
        ceiling = min(ceiling, inputs.style.intensity_ceiling)
    decisions = {d.effect_key: d for d in inputs.config.decisions}
    clips = sorted(shots.clips, key=lambda c: (fr(c.timeline_start), c.layer, c.clip_id))
    checks: list[QcIssue] = []
    warnings: list[str] = []
    planner = _Planner(inputs, profile, ceiling, decisions, fps, checks, warnings)
    emphasis = planner.accept_emphasis(clips)
    treatments = tuple(planner.treat(clip, emphasis.get(clip.clip_id, [])) for clip in clips)
    return Planned(treatments=treatments, checks=tuple(checks), warnings=tuple(warnings))


class _Planner:
    def __init__(
        self,
        inputs: PlanInputs,
        profile: VfxProfile,
        ceiling: int,
        decisions: Mapping[str, EffectDecision],
        fps: Fraction,
        checks: list[QcIssue],
        warnings: list[str],
    ) -> None:
        self.inputs = inputs
        self.profile = profile
        self.ceiling = ceiling
        self.decisions = decisions
        self.fps = fps
        self.checks = checks
        self.warnings = warnings

    # --- helpers --------------------------------------------------------------------------------
    def prof(self, clip: VfxClip) -> VfxProfile:
        return self.inputs.config.for_section(self.profile, clip.section_id)

    def facts(self, clip: VfxClip) -> SourceFacts | None:
        return self.inputs.sources.get(clip.asset_id)

    def decision(self, clip: VfxClip, effect: EffectType) -> Decision | None:
        found = self.decisions.get(f"{clip.clip_id}:{effect.value}")
        return None if found is None else found.decision

    def style_bounds(self) -> tuple[str, ...]:
        style = self.inputs.style
        if style is None:
            return ()
        return (f"motion.intensity_ceiling={style.intensity_ceiling}",)

    def base(
        self,
        clip: VfxClip,
        effect: EffectType,
        status: EffectStatus,
        reasons: Sequence[str],
        *,
        t0: Fraction | None = None,
        t1: Fraction | None = None,
        request: VfxRequest | None = None,
        **extra: object,
    ) -> Effect:
        ts, te = fr(clip.timeline_start), fr(clip.timeline_end)
        a, b = (ts if t0 is None else t0), (te if t1 is None else t1)
        ss = fr(clip.source_start)
        backend = NEEDS[effect][0] if effect in NEEDS else Backend.FFMPEG
        decision = self.decision(clip, effect)
        found = self.decisions.get(f"{clip.clip_id}:{effect.value}")
        note = "" if found is None else found.note
        effect_obj = Effect(
            effect_id="fx:" + digest(clip.clip_id, effect.value, a, b),
            effect_key=f"{clip.clip_id}:{effect.value}",
            effect_type=effect,
            status=status,
            preset=self.profile.name,
            intensity=INTENSITY[effect],
            source_start=rat(ss + (a - ts)),
            source_end=rat(ss + (b - ts)),
            timeline_start=rat(a),
            timeline_end=rat(b),
            start_frame=frame_of(a, self.fps),
            end_frame=frame_of(b, self.fps),
            request_id=None if request is None else request.request_id,
            backend=backend,
            style_bounds=self.style_bounds() if INTENSITY[effect] else (),
            reasons=tuple(reasons),
            decision=decision,
            decision_note=note,
        )
        return replace(effect_obj, **extra)  # type: ignore[arg-type]

    def gate(
        self, clip: VfxClip, effect: EffectType, apply: str, explicit: bool
    ) -> tuple[EffectStatus, str] | None:
        """The first gate that stops a candidate, or ``None`` when it may proceed."""
        decision = self.decision(clip, effect)
        if decision is Decision.REJECTED:
            return EffectStatus.REJECTED, "rejected by a person"
        if apply == "never" and not explicit and decision is not Decision.ACCEPTED:
            return EffectStatus.REJECTED, f"the {self.profile.name} profile does not use it"
        if effect not in self.inputs.available:
            reason = NEEDS.get(effect, (Backend.NONE, "no backend can render it"))[1]
            safer = _SAFER.get(effect)
            return EffectStatus.NOT_AVAILABLE, reason + (f"; safer: {safer}" if safer else "")
        if INTENSITY[effect] > self.ceiling and decision is not Decision.ACCEPTED:
            if explicit:
                self.warnings.append(
                    f"{clip.clip_id}: {effect.value} exceeds the intensity ceiling "
                    f"{self.ceiling} but was asked for explicitly"
                )
                return None
            return (
                EffectStatus.REJECTED,
                f"intensity {INTENSITY[effect]} is above the ceiling {self.ceiling}",
            )
        return None

    # --- emphasis: strongest first, spaced, budgeted ---------------------------------------------
    def accept_emphasis(self, clips: Sequence[VfxClip]) -> dict[str, list[tuple[_Candidate, str]]]:
        """Emphasis candidates of every clip, judged globally: ``(candidate, '' | refusal)``."""
        candidates: list[_Candidate] = []
        for clip in clips:
            candidates += self.emphasis_candidates(clip)
        sel = self.profile.selection
        duration = max((fr(c.timeline_end) for c in clips), default=Fraction(0))
        budget = (
            0
            if sel.emphasis_per_minute <= 0
            else max(1, math.ceil(sel.emphasis_per_minute * float(duration) / 60))
        )
        accepted: list[_Candidate] = []
        out: dict[str, list[tuple[_Candidate, str]]] = {}
        busy: set[tuple[str, str]] = set()  # one time map and one punch-in per clip moment
        for c in sorted(candidates, key=lambda c: (-c.priority, c.at, c.clip.clip_id)):
            refusal = ""
            forced = self.decision(c.clip, c.effect) is Decision.ACCEPTED
            family = "time" if c.effect in TIME_EFFECTS else c.effect.value
            near = [
                a
                for a in accepted
                if (a.effect in TIME_EFFECTS) == (c.effect in TIME_EFFECTS)
                and (a.effect is c.effect or c.effect in TIME_EFFECTS)
                and abs(a.at - c.at) < Fraction(sel.min_gap).limit_denominator(1000)
            ]
            if (c.clip.clip_id, family) in busy and family == "time":
                refusal = "the clip already has a time effect"
            elif near and not forced:
                refusal = f"repeats {near[0].effect.value} within {sel.min_gap:g} s"
            elif len(accepted) >= budget and not forced:
                refusal = f"emphasis budget ({sel.emphasis_per_minute:g}/min) used up"
            if not refusal:
                accepted.append(c)
                busy.add((c.clip.clip_id, family))
            out.setdefault(c.clip.clip_id, []).append((c, refusal))
        return out

    def emphasis_candidates(self, clip: VfxClip) -> list[_Candidate]:
        prof = self.prof(clip)
        sel = prof.selection
        ts, te = fr(clip.timeline_start), fr(clip.timeline_end)
        found: list[_Candidate] = []
        for r in self.requests_in(clip):
            if not sel.honor_requests and r.kind is not RequestKind.EXPLICIT:
                continue
            if r.priority < sel.min_request_priority:
                continue
            at = fr(r.timeline_at)
            if r.kind is RequestKind.EMPHASIS:
                found.append(_Candidate(clip, EffectType.PUNCH_IN, at, r.priority, r.reason, r))
            elif r.kind is RequestKind.REVEAL:
                peak = self.nearest_flash(clip, at)
                if peak is None:
                    bang = self.nearest_bang(clip, at)
                    peak = None if bang is None else self.burst_peak(clip, bang)[0]
                kind = EffectType.SLOW_MOTION if prof.retime.apply != "never" else None
                kind = kind or EffectType.PUNCH_IN
                found.append(_Candidate(clip, kind, at, r.priority, r.reason, r, peak))
            elif r.kind is RequestKind.EXPLICIT and r.effect in EMPHASIS:
                found.append(_Candidate(clip, r.effect, at, 1.0, r.reason, r))
        burst = self.strongest_burst(clip)
        if burst is not None:
            peak, strength, reason = burst
            at = ts + peak - fr(clip.source_start)
            if prof.retime.apply == "auto":
                found.append(
                    _Candidate(clip, EffectType.SLOW_MOTION, at, strength, reason, peak=peak)
                )
            if prof.punch_in.apply == "auto":
                found.append(_Candidate(clip, EffectType.PUNCH_IN, at, strength * 0.9, reason))
        return [c for c in found if ts <= c.at < te]

    def strongest_burst(self, clip: VfxClip) -> tuple[Fraction, float, str] | None:
        """(source peak, strength, reason) of the clip's strongest burst: a picture FLASH, else
        (the flash detector misses local bursts on night footage) the strongest stored bang."""
        retime = self.prof(clip).retime
        flashes = [f for f in self.flashes_in(clip) if f.strength >= retime.min_flash_strength]
        if flashes:
            best = max(flashes, key=lambda f: (f.strength, -fr(f.source_time)))
            at = fr(best.source_time)
            return at, best.strength, f"burst at {float(at):.2f} s (strength {best.strength:.2f})"
        bangs = [b for b in self.bangs_in(clip) if b.strength >= retime.min_bang_strength]
        if not bangs:
            return None
        bang = max(bangs, key=lambda b: (b.strength, -fr(b.source_time)))
        peak, how = self.burst_peak(clip, bang)
        facts = self.facts(clip)
        clock = "" if facts is None or facts.bang_clock == "alignment" else ", clocks assumed equal"
        return (
            peak,
            bang.strength,
            (
                f"bang at {float(fr(bang.source_time)):.2f} s (audio impulse, strength"
                f" {bang.strength:.2f}{clock}); visual peak {how}"
            ),
        )

    def bangs_in(self, clip: VfxClip) -> list[BangFact]:
        facts = self.facts(clip)
        if facts is None:
            return []
        ss, se = fr(clip.source_start), fr(clip.source_end)
        return [b for b in facts.bangs if ss <= fr(b.source_time) < se]

    def nearest_bang(self, clip: VfxClip, at: Fraction) -> BangFact | None:
        source = fr(clip.source_start) + at - fr(clip.timeline_start)
        near = [b for b in self.bangs_in(clip) if abs(fr(b.source_time) - source) <= 1]
        return min(near, key=lambda b: abs(fr(b.source_time) - source), default=None)

    def burst_peak(self, clip: VfxClip, bang: BangFact) -> tuple[Fraction, str]:
        """The brightest sampled frame in a bounded window around the bang (light arrives before
        the sound), when the stored median luma rises there; else the bang time itself."""
        retime, facts = self.prof(clip).retime, self.facts(clip)
        heard = fr(bang.source_time)
        low = max(fr(clip.source_start), heard - Fraction(str(retime.peak_before)))
        high = min(fr(clip.source_end), heard + Fraction(str(retime.peak_after)))
        samples = [(fr(t), v) for t, v in (facts.luma if facts else ()) if low <= fr(t) <= high]
        if not samples:
            return heard, "= the bang (no stored brightness signal around it)"
        when, top = max(samples, key=lambda s: (s[1], -s[0]))
        base = min(v for _, v in samples)
        if top - base < retime.min_luma_rise:
            return heard, (
                f"= the bang (median brightness flat {base:.3f}-{top:.3f} from"
                f" {float(low):.2f} to {float(high):.2f} s)"
            )
        return when, f"brightest sampled frame at {float(when):.2f} s ({base:.3f} -> {top:.3f})"

    def requests_in(self, clip: VfxClip) -> list[VfxRequest]:
        """Requests at a time inside the clip; a moment covered by several layers goes to the
        lowest layer (the main picture)."""
        ts, te = fr(clip.timeline_start), fr(clip.timeline_end)
        out = []
        for r in self.inputs.shots.requests:
            at = fr(r.timeline_at)
            if not ts <= at < te:
                continue
            owners = [
                c
                for c in self.inputs.shots.clips
                if fr(c.timeline_start) <= at < fr(c.timeline_end)
            ]
            if min(owners, key=lambda c: (c.layer, c.clip_id)).clip_id == clip.clip_id:
                out.append(r)
        return out

    def flashes_in(self, clip: VfxClip) -> list[FlashFact]:
        facts = self.facts(clip)
        if facts is None:
            return []
        ss, se = fr(clip.source_start), fr(clip.source_end)
        return [f for f in facts.flashes if ss <= fr(f.source_time) < se]

    def nearest_flash(self, clip: VfxClip, at: Fraction) -> Fraction | None:
        source = fr(clip.source_start) + at - fr(clip.timeline_start)
        near = [f for f in self.flashes_in(clip) if abs(fr(f.source_time) - source) <= 1]
        if not near:
            return None
        return fr(min(near, key=lambda f: abs(fr(f.source_time) - source)).source_time)

    def shots_of(self, clip: VfxClip) -> list[ShotFact]:
        facts = self.facts(clip)
        if facts is None:
            return []
        ss, se = fr(clip.source_start), fr(clip.source_end)
        return [s for s in facts.shots if fr(s.source_start) < se and fr(s.source_end) > ss]

    # --- one clip -------------------------------------------------------------------------------
    def treat(self, clip: VfxClip, emphasis: list[tuple[_Candidate, str]]) -> Treatment:
        prof = self.prof(clip)
        effects: list[Effect] = []
        time_map: TimeMap | None = None
        issues: list[QcIssue] = []
        for c, refusal in sorted(emphasis, key=lambda x: (x[0].at, x[0].effect.value)):
            explicit = c.request is not None and c.request.kind is RequestKind.EXPLICIT
            apply = prof.punch_in.apply if c.effect is EffectType.PUNCH_IN else prof.retime.apply
            if c.request is not None and apply == "request":
                apply = "auto"
            stop = self.gate(clip, c.effect, apply, explicit)
            if stop is None and refusal:
                stop = EffectStatus.REJECTED, refusal
            if stop is not None:
                effects.append(
                    self.base(
                        clip,
                        c.effect,
                        stop[0],
                        [c.reason, stop[1]],
                        request=c.request,
                        t0=c.at,
                        t1=c.at,
                    )
                )
                continue
            if c.effect is EffectType.PUNCH_IN:
                effects.append(self.punch_in(clip, c, prof))
            elif time_map is None:
                made, time_map = self.retime(clip, c, prof)
                effects += made
        effects += self.privacy(clip, prof, issues)
        effects += self.looks(clip, prof)
        effects += self.matte_effects(clip, prof)
        effects += self.explicit_others(clip, prof)
        planned = [e for e in effects if e.status is EffectStatus.PLANNED]
        if not planned:
            effects.append(
                self.base(
                    clip,
                    EffectType.NO_EFFECT,
                    EffectStatus.PLANNED,
                    ["no request and no reason in the footage: the original is used as it is"]
                    if not effects
                    else ["nothing passed its gates: the original is used as it is"],
                    backend=Backend.NONE,
                )
            )
        effects.sort(
            key=lambda e: (
                ORDER.index(e.effect_type) if e.effect_type in ORDER else -1,
                fr(e.timeline_start),
            )
        )
        ts, te = fr(clip.timeline_start), fr(clip.timeline_end)
        status = TreatmentStatus.PLANNED if planned else TreatmentStatus.ORIGINAL
        self.checks.extend(issues)
        return Treatment(
            treatment_id="tr:" + digest(clip.clip_id, clip.asset_id, clip.source_start, ts),
            clip_id=clip.clip_id,
            asset_id=clip.asset_id,
            layer=clip.layer,
            section_id=clip.section_id,
            beat_id=clip.beat_id,
            source_start=clip.source_start,
            source_end=clip.source_end,
            timeline_start=clip.timeline_start,
            timeline_end=clip.timeline_end,
            start_frame=frame_of(ts, self.fps),
            end_frame=frame_of(te, self.fps),
            status=status,
            effects=tuple(effects),
            time_map=time_map,
            audio=self.audio_of(time_map, prof),
            checks=tuple(issues),
        )

    def audio_of(self, time_map: TimeMap | None, prof: VfxProfile) -> AudioIntent:
        return AudioIntent.SOURCE_SYNC if time_map is None else AudioIntent(prof.retime.audio)

    # --- punch-in ------------------------------------------------------------------------------
    def punch_in(self, clip: VfxClip, c: _Candidate, prof: VfxProfile) -> Effect:
        p = prof.punch_in
        shots = self.inputs.shots
        te = fr(clip.timeline_end)
        start = max(snap(c.at, self.fps), fr(clip.timeline_start))
        length = (
            fr(c.request.duration)
            if c.request is not None and c.request.duration is not None
            else Fraction(p.hold).limit_denominator(1000)
        )
        end = snap(start + length, self.fps)
        if te - end < Fraction(1, 2):  # never "un-punch" for a sliver before the cut
            end = te
        end = min(end, te)
        source_t = fr(clip.source_start) + start - fr(clip.timeline_start)
        facts = self.facts(clip)
        anchor, anchor_why, crop_h = self.anchor(clip, facts, source_t)
        reasons = [c.reason or "editorial emphasis", anchor_why]
        scale = p.scale
        if facts is not None:
            base_ratio = facts.height * crop_h / shots.output_height
            limit = base_ratio / p.min_effective_ratio
            if base_ratio < p.min_effective_ratio:
                return self.base(
                    clip,
                    EffectType.PUNCH_IN,
                    EffectStatus.BYPASSED,
                    [
                        *reasons,
                        f"the source is already enlarged ({base_ratio:.2f} source px per "
                        "output px): a punch-in would soften it further",
                    ],
                    t0=start,
                    t1=end,
                    request=c.request,
                )
            if scale > limit:
                reasons.append(f"scale limited to {limit:.2f} by the source resolution")
                scale = limit
        else:
            reasons.append("source size unknown: resolution guard not checked")
        if scale < 1.02:
            return self.base(
                clip,
                EffectType.PUNCH_IN,
                EffectStatus.BYPASSED,
                [*reasons, "too little resolution headroom for a visible punch-in"],
                t0=start,
                t1=end,
                request=c.request,
            )
        return self.base(
            clip,
            EffectType.PUNCH_IN,
            EffectStatus.PLANNED,
            reasons,
            t0=start,
            t1=end,
            request=c.request,
            mode="hard" if p.ease <= 0 else "push",
            parameters=(
                Param(name="scale", value=round(scale, 4)),
                Param(name="anchor_x", value=round(anchor[0], 4)),
                Param(name="anchor_y", value=round(anchor[1], 4)),
                Param(name="ease", value=p.ease),
            ),
            confidence=0.9 if anchor_why != "frame centre" else 0.7,
        )

    def anchor(
        self, clip: VfxClip, facts: SourceFacts | None, source_t: Fraction
    ) -> tuple[tuple[float, float], str, float]:
        """Zoom centre: reframing's window (so a later crop still holds the subject), else the
        main subject's track, else the frame centre. Also the crop height share it implies."""
        if facts is not None:
            crop = _crop_at(facts.crops, source_t)
            if crop is not None:
                return (
                    ((crop.x0 + crop.x1) / 2, (crop.y0 + crop.y1) / 2),
                    f"centred on reframing's window ({facts.reframing_id})",
                    crop.height,
                )
            main = {s.main_subject_track_id for s in self.shots_of(clip)} - {None}
            for track in facts.tracks:
                if track.track_id in main:
                    box = _sample_box(track.samples, source_t)
                    if box is not None:
                        return (
                            ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2),
                            f"centred on the main subject ({track.track_id})",
                            1.0,
                        )
        return (0.5, 0.5), "frame centre", 1.0

    # --- slow motion / freeze / reverse ---------------------------------------------------------
    def retime(
        self, clip: VfxClip, c: _Candidate, prof: VfxProfile
    ) -> tuple[list[Effect], TimeMap | None]:
        r = prof.retime
        ts, te = fr(clip.timeline_start), fr(clip.timeline_end)
        ss = fr(clip.source_start)
        peak = c.peak if c.peak is not None else ss + c.at - ts
        reasons = [c.reason or "editorial reveal"]
        speech = [
            s
            for s in clip.speech
            if fr(s.source_start) < peak + Fraction(r.post_roll).limit_denominator(1000)
            and fr(s.source_end) > peak - Fraction(r.pre_roll).limit_denominator(1000)
        ]
        if speech and r.protect_speech and clip.audio is ClipAudio.SOURCE_SYNC:
            return [
                self.base(
                    clip,
                    c.effect,
                    EffectStatus.REJECTED,
                    [*reasons, "speech in the span: retiming would break lip sync"],
                    t0=c.at,
                    t1=c.at,
                    request=c.request,
                )
            ], None
        if te - ts < Fraction(1):
            return [
                self.base(
                    clip,
                    c.effect,
                    EffectStatus.BYPASSED,
                    [*reasons, "the clip is shorter than 1 s: no room for a time effect"],
                    t0=c.at,
                    t1=c.at,
                    request=c.request,
                )
            ], None
        interpolation = Interpolation(r.interpolation)
        if interpolation is Interpolation.MOTION and not r.allow_8bit_interpolation:
            interpolation = Interpolation.BLEND
            reasons.append("motion interpolation is 8-bit only: frame blending (12-bit) is used")
        audio = AudioIntent(r.audio)
        if c.effect is EffectType.REVERSE:
            time_map = reverse_map(
                timeline_start=ts,
                timeline_end=te,
                source_start=ss,
                at=peak,
                length=Fraction(r.reverse_max).limit_denominator(1000),
                fps=self.fps,
                audio=audio,
            )
        else:
            slow = c.effect is not EffectType.FREEZE_FRAME
            freeze = Fraction(r.freeze).limit_denominator(1000)
            if c.effect is EffectType.FREEZE_FRAME and freeze <= 0:
                freeze = Fraction(1, 2)
            time_map = retime_map(
                timeline_start=ts,
                timeline_end=te,
                source_start=ss,
                peak=peak,
                fps=self.fps,
                rate=Fraction(r.slow_rate).limit_denominator(1000) if slow else Fraction(1),
                pre_roll=Fraction(r.pre_roll).limit_denominator(1000),
                post_roll=Fraction(r.post_roll).limit_denominator(1000),
                freeze=freeze,
                ramp=c.effect is EffectType.SPEED_RAMP,
                interpolation=interpolation,
                audio=audio,
            )
        effects: list[Effect] = []
        by_kind: dict[str, list[Fraction]] = {}
        for s in time_map.segments:
            if s.kind.value != "normal":
                by_kind.setdefault(s.kind.value, []).extend(
                    [fr(s.timeline_start), fr(s.timeline_end)]
                )
        kinds = {
            "slow": EffectType.SPEED_RAMP
            if c.effect is EffectType.SPEED_RAMP
            else EffectType.SLOW_MOTION,
            "freeze": EffectType.FREEZE_FRAME,
            "reverse": EffectType.REVERSE,
        }
        audio_note = f"audio intent: {audio.value} (processed by the audio stage, not here)"
        for kind, times in by_kind.items():
            effect = kinds[kind]
            params = [Param(name="rate", value=r.slow_rate)] if kind == "slow" else []
            if kind == "freeze":
                params = [Param(name="hold_seconds", value=float(max(times) - min(times)))]
            effects.append(
                self.base(
                    clip,
                    effect,
                    EffectStatus.PLANNED,
                    [*reasons, audio_note, time_map.note],
                    t0=min(times),
                    t1=max(times),
                    request=c.request,
                    mode=time_map.interpolation.value,
                    parameters=tuple(params),
                    confidence=0.8 if c.peak is not None else 0.6,
                )
            )
        if not effects:
            return [
                self.base(
                    clip,
                    c.effect,
                    EffectStatus.BYPASSED,
                    [*reasons, "the moment is too close to the clip end for a time effect"],
                    t0=c.at,
                    t1=c.at,
                    request=c.request,
                )
            ], None
        return effects, time_map

    # --- privacy -------------------------------------------------------------------------------
    def privacy(self, clip: VfxClip, prof: VfxProfile, issues: list[QcIssue]) -> list[Effect]:
        p = prof.privacy
        requests = [r for r in self.requests_in(clip) if r.kind is RequestKind.PRIVACY]
        if p.apply != "auto" and not requests:
            return []
        if self.decision(clip, EffectType.PRIVACY_BLUR) is Decision.REJECTED:
            return [
                self.base(
                    clip, EffectType.PRIVACY_BLUR, EffectStatus.REJECTED, ["rejected by a person"]
                )
            ]
        facts = self.facts(clip)
        wanted_ids = {t for r in requests for t in r.track_ids}
        kinds = {
            k for k, on in (("face", p.faces), ("person", p.persons), ("vehicle", p.vehicles)) if on
        }
        if facts is None or not facts.analysed:
            detail = "no picture analysis: nothing could be checked for faces or plates"
            ts, te = fr(clip.timeline_start), fr(clip.timeline_end)
            issue = QcIssue(
                code="privacy_unverified",
                severity=Severity.ERROR if p.unverified == "full_frame" else Severity.WARNING,
                category=QcCategory.TECHNICAL,
                message=detail,
                item_id=clip.clip_id,
                timeline_start=rat(ts),
                timeline_end=rat(te),
            )
            issues.append(issue)
            if p.unverified != "full_frame":
                return [
                    self.base(
                        clip,
                        EffectType.PRIVACY_BLUR,
                        EffectStatus.BYPASSED,
                        [detail],
                        warnings=(detail,),
                    )
                ]
            return [
                self.privacy_effect(
                    clip,
                    prof,
                    [_full_mask(clip, "unverified")],
                    0.0,
                    [detail, "full-frame concealment (configured)"],
                )
            ]
        main = {s.main_subject_track_id for s in self.shots_of(clip)} - {None}
        ss, se = fr(clip.source_start), fr(clip.source_end)
        tracks = [
            t
            for t in facts.tracks
            if t.samples
            and fr(t.samples[0].source_time) < se
            and fr(t.samples[-1].source_time) >= ss
            and (t.track_id in wanted_ids or (not wanted_ids and t.kind in kinds))
            and not (p.exclude_main_subject and t.track_id in main and t.track_id not in wanted_ids)
        ]
        if not tracks:
            return []
        masks: list[Mask] = []
        reasons = [
            f"conceal {len(tracks)} tracked region(s) "
            f"({', '.join(sorted({t.kind for t in tracks}))})"
        ]
        coverage_ok = True
        for track in tracks:
            mask, gaps, covered = track_mask(track, clip, prof, facts.analysis_id)
            if gaps:
                text = ", ".join(f"{float(a):.2f}-{float(b):.2f} s" for a, b in gaps)
                if p.fallback == "drop":
                    issues.append(
                        QcIssue(
                            code="privacy_track_lost",
                            severity=Severity.ERROR,
                            category=QcCategory.TECHNICAL,
                            message=f"track {track.track_id} lost over {text}: do not use these "
                            "source spans (configured fallback: drop)",
                            item_id=clip.clip_id,
                            evidence=f"max gap {p.max_gap} s",
                        )
                    )
                else:
                    reasons.append(
                        f"track {track.track_id} lost over {text}: whole picture "
                        "concealed there (fallback)"
                    )
            coverage_ok &= covered
            masks.append(mask)
        if not coverage_ok:
            issues.append(
                QcIssue(
                    code="privacy_coverage",
                    severity=Severity.ERROR,
                    category=QcCategory.TECHNICAL,
                    message="a mask does not contain every tracked box",
                    item_id=clip.clip_id,
                )
            )
        confidence = min(t.confidence for t in tracks)
        return [self.privacy_effect(clip, prof, masks, confidence, reasons)]

    def privacy_effect(
        self,
        clip: VfxClip,
        prof: VfxProfile,
        masks: list[Mask],
        confidence: float,
        reasons: list[str],
    ) -> Effect:
        p = prof.privacy
        # the masks carry the exact times; the effect spans the clip (a mask is off outside its
        # keyframes, and under a time map source and timeline are not linear)
        return self.base(
            clip,
            EffectType.PRIVACY_BLUR,
            EffectStatus.PLANNED,
            [*reasons, "concealment (blurred / mosaic), not removal: the original is untouched"],
            mode=p.method,
            masks=tuple(masks),
            confidence=confidence,
            parameters=(
                Param(name="strength", value=p.strength),
                Param(name="feather", value=p.feather),
            ),
        )

    # --- looks ---------------------------------------------------------------------------------
    def looks(self, clip: VfxClip, prof: VfxProfile) -> list[Effect]:
        explicit = {
            r.effect
            for r in self.requests_in(clip)
            if r.kind is RequestKind.EXPLICIT and r.effect in LOOKS
        }
        shots = self.shots_of(clip)
        out: list[Effect] = []
        for effect in LOOKS:
            apply = _look_apply(prof, effect)
            requested = effect in explicit
            if apply == "never" and not requested:
                continue
            if apply == "request" and not requested:
                continue
            stop = self.gate(clip, effect, "auto", requested)
            if stop is not None:
                out.append(self.base(clip, effect, stop[0], [stop[1]]))
                continue
            guard = _look_guard(effect, prof, shots)
            forced = self.decision(clip, effect) is Decision.ACCEPTED or requested
            if guard and not forced:
                out.append(self.base(clip, effect, EffectStatus.BYPASSED, [guard]))
                continue
            reasons = ["asked for explicitly" if requested else f"{prof.name} look"]
            warnings: tuple[str, ...] = ()
            if not shots:
                warnings = ("no picture analysis: not checked against the footage",)
            out.append(
                self.base(
                    clip,
                    effect,
                    EffectStatus.PLANNED,
                    reasons,
                    warnings=warnings,
                    parameters=_look_params(effect, prof),
                    mode=_look_mode(effect, prof),
                    style_bounds=self.style_bounds() + self.tint_bounds(effect, prof),
                )
            )
        return out

    def tint_bounds(self, effect: EffectType, prof: VfxProfile) -> tuple[str, ...]:
        if effect is not EffectType.GLOW or not prof.glow.tint or self.inputs.style is None:
            return ()
        colours = dict(self.inputs.style.palette)
        if prof.glow.tint not in colours:
            self.warnings.append(f"glow tint {prof.glow.tint!r} is not in the style palette")
            return ()
        return (f"palette.{prof.glow.tint}={colours[prof.glow.tint]}",)

    # --- capabilities that need a matte / model / planar track ---------------------------------
    def matte_effects(self, clip: VfxClip, prof: VfxProfile) -> list[Effect]:
        apply = prof.background.blur
        if apply == "never":
            return []
        shots = self.shots_of(clip)
        if apply == "auto" and not any(s.main_subject_track_id for s in shots):
            return []
        if apply == "request":
            return []
        stop = self.gate(clip, EffectType.BACKGROUND_BLUR, "auto", False)
        if stop is not None:
            return [self.base(clip, EffectType.BACKGROUND_BLUR, stop[0], [stop[1]])]
        # shortcut: no matting backend ships; when one does, plan the matte dependency here
        return [
            self.base(
                clip,
                EffectType.BACKGROUND_BLUR,
                EffectStatus.NOT_AVAILABLE,
                ["a subject matte backend is registered but not wired for planning yet"],
            )
        ]

    def explicit_others(self, clip: VfxClip, prof: VfxProfile) -> list[Effect]:
        """Explicit requests for effects outside emphasis, looks and privacy."""
        out: list[Effect] = []
        handled = EMPHASIS | set(LOOKS) | {EffectType.PRIVACY_BLUR}
        for r in self.requests_in(clip):
            if r.kind is not RequestKind.EXPLICIT or r.effect is None or r.effect in handled:
                continue
            at = fr(r.timeline_at)
            stop = self.gate(clip, r.effect, "auto", True)
            if stop is not None:
                out.append(
                    self.base(clip, r.effect, stop[0], [r.reason, stop[1]], t0=at, t1=at, request=r)
                )
                continue
            if r.effect is EffectType.BLUR:
                b = prof.blur
                out.append(
                    self.base(
                        clip,
                        EffectType.BLUR,
                        EffectStatus.PLANNED,
                        [r.reason or "asked for"],
                        request=r,
                        mode=b.kind,
                        parameters=(
                            Param(name="sigma", value=b.sigma),
                            Param(name="angle", value=b.angle),
                        ),
                    )
                )
            elif r.effect is EffectType.COMPOSITE:
                out.append(self.composite(clip, r, prof))
            else:
                out.append(
                    self.base(
                        clip,
                        r.effect,
                        EffectStatus.NOT_AVAILABLE,
                        ["no renderer for this effect"],
                        request=r,
                    )
                )
        return out

    def composite(self, clip: VfxClip, r: VfxRequest, prof: VfxProfile) -> Effect:
        """Picture-in-picture of the footage on the next layer over this clip's span."""
        at = fr(r.timeline_at)
        over = [
            c
            for c in self.inputs.shots.clips
            if c.layer > clip.layer and fr(c.timeline_start) <= at < fr(c.timeline_end)
        ]
        if not over:
            return self.base(
                clip,
                EffectType.COMPOSITE,
                EffectStatus.REJECTED,
                [r.reason, "no footage on a higher layer at that moment"],
                t0=at,
                t1=at,
                request=r,
            )
        layer = min(over, key=lambda c: (c.layer, c.clip_id))
        c = prof.composite
        t0 = max(fr(clip.timeline_start), fr(layer.timeline_start))
        t1 = min(fr(clip.timeline_end), fr(layer.timeline_end))
        return self.base(
            clip,
            EffectType.COMPOSITE,
            EffectStatus.PLANNED,
            [r.reason or "asked for", f"layer {layer.clip_id} as picture-in-picture"],
            t0=snap(t0, self.fps),
            t1=snap(t1, self.fps),
            request=r,
            mode=c.blend_mode,
            parameters=(
                Param(name="scale", value=c.pip_scale),
                Param(name="margin", value=c.margin),
                Param(name="opacity", value=c.opacity),
                Param(name="corner", value=float(_CORNER_INDEX[c.corner])),
            ),
            warnings=(f"overlay:{layer.clip_id}",),
        )


_CORNER_INDEX = {"top_left": 0, "top_right": 1, "bottom_left": 2, "bottom_right": 3}


def _look_apply(prof: VfxProfile, effect: EffectType) -> str:
    groups = {
        EffectType.GLOW: prof.glow.apply,
        EffectType.LIGHT_RAYS: prof.light_rays.apply,
        EffectType.VIGNETTE: prof.vignette.apply,
        EffectType.FILM_GRAIN: prof.grain.apply,
        EffectType.FILM_DAMAGE: prof.film_damage.apply,
        EffectType.CHROMATIC_ABERRATION: prof.chromatic.apply,
        EffectType.LENS_DISTORTION: prof.lens.apply,
        EffectType.MOTION_BLUR: prof.motion_blur.apply,
        EffectType.STYLIZE: prof.stylize.apply,
    }
    return groups[effect]


def _worst(shots: list[ShotFact], name: str, pick: str) -> float | None:
    values: list[float] = [getattr(s, name) for s in shots if getattr(s, name) is not None]
    if not values:
        return None
    return max(values) if pick == "max" else min(values)


def _look_guard(effect: EffectType, prof: VfxProfile, shots: list[ShotFact]) -> str:
    """Why a look would harm this footage ('' = no reason against it)."""
    if effect is EffectType.GLOW:
        clipped = _worst(shots, "clipped", "max")
        luma = _worst(shots, "luma_p50", "min")
        if clipped is not None and clipped > prof.glow.max_clipped:
            return (
                f"highlights already clip ({clipped:.1%} of pixels): bloom would only widen "
                "blown cores (do not destroy good media)"
            )
        if clipped and luma is not None and luma < prof.glow.dark_luma:
            return (
                f"night picture (median luma {luma:.2f}) with clipped lights: bursts already "
                "bloom in camera; more would only widen blown cores"
            )
    if effect is EffectType.VIGNETTE:
        luma = _worst(shots, "luma_p50", "min")
        if luma is not None and luma < prof.vignette.min_luma:
            return (
                f"the picture is already dark (median luma {luma:.2f}): a vignette would only "
                "hide edge detail"
            )
    if effect is EffectType.FILM_GRAIN:
        noise = _worst(shots, "noise_sigma", "max")
        if noise is not None and noise > prof.grain.max_noise:
            return f"the footage is already noisy (sigma {noise:.3f}): grain would add to it"
        luma = _worst(shots, "luma_p50", "min")
        if luma is not None and luma < prof.grain.dark_luma:
            return (
                f"night picture (median luma {luma:.2f}): grain would read as noise in the blacks"
            )
    if effect is EffectType.MOTION_BLUR:
        speed = _worst(shots, "camera_speed", "max")
        if speed is None or speed < prof.motion_blur.min_camera_speed:
            return "no fast camera move: motion blur would only soften the picture"
    return ""


def _look_params(effect: EffectType, prof: VfxProfile) -> tuple[Param, ...]:
    def ps(**values: float) -> tuple[Param, ...]:
        return tuple(Param(name=k, value=float(v)) for k, v in sorted(values.items()))

    match effect:
        case EffectType.GLOW:
            g = prof.glow
            return ps(threshold=g.threshold, radius=g.radius, strength=g.strength)
        case EffectType.LIGHT_RAYS:
            lr = prof.light_rays
            return ps(threshold=lr.threshold, length=lr.length, strength=lr.strength)
        case EffectType.VIGNETTE:
            return ps(strength=prof.vignette.strength, radius=prof.vignette.radius)
        case EffectType.FILM_GRAIN:
            return ps(amount=prof.grain.amount)
        case EffectType.FILM_DAMAGE:
            return ps(flicker=prof.film_damage.flicker, scratches=prof.film_damage.scratches)
        case EffectType.CHROMATIC_ABERRATION:
            return ps(shift=prof.chromatic.shift)
        case EffectType.LENS_DISTORTION:
            return ps(k1=prof.lens.k1, k2=prof.lens.k2)
        case EffectType.MOTION_BLUR:
            return ps(frames=prof.motion_blur.frames)
        case EffectType.STYLIZE:
            return ps(levels=prof.stylize.levels)
        case _:
            return ()


def _look_mode(effect: EffectType, prof: VfxProfile) -> str:
    if effect is EffectType.STYLIZE:
        return prof.stylize.kind
    if effect is EffectType.GLOW:
        return prof.glow.tint
    return ""


def _crop_at(crops: tuple[CropFact, ...], t: Fraction) -> Box | None:
    for c in crops:
        if fr(c.source_start) <= t < fr(c.source_end) and c.keyframes:
            keys = [k for k in c.keyframes if fr(k[0]) <= t]
            return (keys[-1] if keys else c.keyframes[0])[1]
    return None


def _sample_box(samples: tuple[TrackSample, ...], t: Fraction) -> Box | None:
    if not samples:
        return None
    return min(samples, key=lambda s: abs(fr(s.source_time) - t)).box


def _full_mask(clip: VfxClip, why: str) -> Mask:
    return Mask(
        mask_id="mask:" + digest(clip.clip_id, "full", why),
        kind=MaskKind.FULL_FRAME,
        keyframes=(MaskKeyframe(source_time=clip.source_start, box=FULL_FRAME),),
    )


def _grow(box: Box, mx: float, my: float) -> Box:
    return Box(
        x0=max(0.0, box.x0 - mx),
        y0=max(0.0, box.y0 - my),
        x1=min(1.0, box.x1 + mx),
        y1=min(1.0, box.y1 + my),
    )


def track_mask(
    track: TrackFact, clip: VfxClip, prof: VfxProfile, analysis_id: str | None
) -> tuple[Mask, list[tuple[Fraction, Fraction]], bool]:
    """A tracked, padded, feathered mask over the clip span: ``(mask, fallback gaps, covered)``.

    Each sample's box grows by the margin plus the distance the box moves in half a sampling
    interval (so the interpolated box still contains the subject between samples). Samples below
    the trusted confidence count as missing. A gap longer than ``max_gap`` cannot be trusted:
    there the configured fallback (whole picture) applies. ``covered`` proves every trusted
    sample box lies inside the mask.
    """
    p = prof.privacy
    ss, se = fr(clip.source_start), fr(clip.source_end)
    pad = Fraction(p.pad).limit_denominator(1000)
    max_gap = Fraction(p.max_gap).limit_denominator(1000)
    trusted = [s for s in track.samples if s.confidence >= p.min_confidence]
    gaps: list[tuple[Fraction, Fraction]] = []
    if not trusted:
        span = (
            max(ss, fr(track.samples[0].source_time)),
            min(se, fr(track.samples[-1].source_time)),
        )
        gaps.append((span[0], span[1] + pad))
        mask = Mask(
            mask_id="mask:" + digest(clip.clip_id, track.track_id),
            kind=MaskKind.TRACKED,
            keyframes=(MaskKeyframe(source_time=rat(span[0]), box=FULL_FRAME),),
            track_refs=(f"{analysis_id}/{track.track_id}",),
            confidence=0.0,
            fallback_spans=tuple((rat(a), rat(b)) for a, b in gaps),
        )
        return mask, gaps, True
    keys: list[MaskKeyframe] = []
    for i, s in enumerate(trusted):
        neighbours = [trusted[j] for j in (i - 1, i + 1) if 0 <= j < len(trusted)]
        drift_x = drift_y = 0.0
        for n in neighbours:
            dt = abs(fr(n.source_time) - fr(s.source_time))
            if 0 < dt <= max_gap:
                drift_x = max(drift_x, abs((n.box.x0 + n.box.x1) - (s.box.x0 + s.box.x1)) / 2)
                drift_y = max(drift_y, abs((n.box.y0 + n.box.y1) - (s.box.y0 + s.box.y1)) / 2)
        mx = s.box.width * p.margin + drift_x / 2
        my = s.box.height * p.margin + drift_y / 2
        keys.append(MaskKeyframe(source_time=s.source_time, box=_grow(s.box, mx, my)))
    for a, b in pairwise(trusted):
        if fr(b.source_time) - fr(a.source_time) > max_gap:
            gaps.append((fr(a.source_time), fr(b.source_time)))
    first, last = fr(trusted[0].source_time), fr(trusted[-1].source_time)
    lead = [MaskKeyframe(source_time=rat(max(Fraction(0), first - pad)), box=keys[0].box)]
    tail = [MaskKeyframe(source_time=rat(last + pad), box=keys[-1].box)]
    inside = [k for k in (*lead, *keys, *tail) if ss - pad <= fr(k.source_time) <= se + pad]
    by_time = {fr(k.source_time): k for k in inside or [keys[0]]}
    keyframes = tuple(by_time[t] for t in sorted(by_time))
    mask = Mask(
        mask_id="mask:" + digest(clip.clip_id, track.track_id),
        kind=MaskKind.TRACKED,
        keyframes=keyframes,
        feather=p.feather,
        track_refs=(f"{analysis_id}/{track.track_id}",),
        confidence=track.confidence,
        fallback_spans=tuple((rat(a), rat(b)) for a, b in gaps),
    )
    covered = all(
        (box := box_at(mask, fr(s.source_time))) is not None and box.contains(s.box)
        for s in trusted
        if ss <= fr(s.source_time) < se
    )
    return mask, gaps, covered
