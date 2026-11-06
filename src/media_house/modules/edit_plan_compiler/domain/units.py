"""Compile units: the sections of a timeline that are compiled (or reused) independently.

One unit per edit placement (its picture or sound item, its effects, the transition INTO it and
the audio instructions of that transition), plus three global units: ``graphics``, ``captions``
and ``soundtrack``. A unit's digest covers every fact its items were computed from; when the
previous specification holds the same digest (same compiler, config and frame rate included) its
items are reused as they were. Track assignment is global and always re-run, so a reused item
only keeps its content.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING, Literal

from media_house.modules.edit_plan_compiler.domain.digest import digest
from media_house.modules.edit_plan_compiler.domain.facts import (
    AssetFacts,
    CompileInputs,
    EffectFact,
    FramingRef,
    ReframeFacts,
    SourceClip,
    TransitionFact,
    TreatmentFact,
)
from media_house.modules.edit_plan_compiler.domain.model import (
    AudioAutomation,
    AudioItem,
    CropKey,
    EffectMask,
    EffectSpec,
    Handles,
    Issue,
    OverlayItem,
    Reframe,
    Retime,
    RetimeSegment,
    TimelineSpecification,
    TransitionSpec,
    VideoItem,
)
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.timing import (
    fr,
    frame_at,
    frame_span,
    overlap,
    rat,
    sample_at,
)
from media_house.modules.edit_plan_compiler.domain.values import (
    Composite,
    DerivativePolicy,
    Execution,
    IssueClass,
    ItemOrigin,
    TrackRole,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Transition kinds a timeline application performs itself; every other kind of the plan's
#: graphic family needs its rendered overlay. A builder must support this whole vocabulary.
OVERLAY_FAMILY = "graphic"
RETIME_EFFECTS = frozenset({"slow_motion", "speed_ramp", "freeze_frame", "reverse"})
_RENDERED_STATES = frozenset({"rendered", "reused"})
_ACTIVE_EFFECT = frozenset({"planned", "rendered", "reused"})
_SOUND_CROSSFADE = frozenset({"crossfade", "j_cut", "l_cut"})

type Want = Literal["video", "audio"]
_IMPROVED = {"video": "video_improvement", "audio": "audio_improvement"}


@dataclass(slots=True)
class Unit:
    unit_id: str
    digest: str = ""
    reused: bool = False
    video: list[VideoItem] = field(default_factory=list)
    overlays: list[OverlayItem] = field(default_factory=list)
    audio: list[AudioItem] = field(default_factory=list)
    effects: list[EffectSpec] = field(default_factory=list)
    transitions: list[TransitionSpec] = field(default_factory=list)
    automation: list[AudioAutomation] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def issue(
        self,
        klass: IssueClass,
        code: str,
        message: str,
        *,
        item_id: str | None = None,
        start: Fraction | None = None,
        end: Fraction | None = None,
        evidence: str = "",
        resolution: str = "",
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                klass=klass,
                message=message,
                item_id=item_id,
                timeline_start=None if start is None else rat(start),
                timeline_end=None if end is None else rat(end),
                evidence=evidence,
                resolution=resolution,
                unit_id=self.unit_id,
            )
        )


@dataclass(frozen=True, slots=True)
class Chosen:
    """The file a clip uses and the recording it traces to."""

    asset_id: str
    original_id: str
    facts: AssetFacts | None


@dataclass(frozen=True, slots=True)
class SoundSource:
    """Where the audio item of one placement sits (computed from the edit alone)."""

    item_id: str
    clip_id: str
    asset_id: str
    role: TrackRole
    start: Fraction
    end: Fraction
    source_start: Fraction
    linked_picture: str | None


class Context:
    """The read-only world one compilation works in."""

    def __init__(self, inputs: CompileInputs, config: CompilerConfig, fps: Fraction) -> None:
        self.inputs = inputs
        self.config = config
        self.fps = fps
        self.sample_rate = config.sample_rate
        self.clips = {c.clip_id: c for c in inputs.source.clips}
        self.treatments: Mapping[str, TreatmentFact] = (
            {t.clip_id: t for t in inputs.vfx.treatments} if inputs.vfx else {}
        )
        self.transition_owner: dict[str, list[TransitionFact]] = {}
        if inputs.transitions:
            for t in inputs.transitions.transitions:
                owner = t.incoming_clip_id or t.outgoing_clip_id
                if owner:
                    self.transition_owner.setdefault(owner, []).append(t)
        self.sound = self._sound_sources()
        # Picture clips in timeline order: a transition looks at its neighbours' handles.
        self.sound_assets = {s.asset_id for s in self.sound}

    # --- lookups ------------------------------------------------------------------------------
    def facts(self, asset_id: str | None) -> AssetFacts | None:
        return None if asset_id is None else self.inputs.assets.get(asset_id)

    def span(self, start: Fraction, end: Fraction) -> tuple[int, int, int]:
        return frame_span(start, end, self.fps)

    def pick(self, asset_id: str, want: Want, unit: Unit, *, item_id: str) -> Chosen:
        """The file to use for a recording: the recording (or the asset the edit named), or its
        stored improved version when the config says so. Always traceable to the recording."""
        facts = self.facts(asset_id)
        if facts is None or not facts.exists:
            unit.issue(
                IssueClass.CRITICAL,
                "missing_source_asset",
                f"asset {asset_id} does not exist in the media library",
                item_id=item_id,
                evidence=asset_id,
            )
            return Chosen(asset_id, asset_id, None)
        original = facts.original_asset_id or asset_id
        chosen = Chosen(asset_id, original, facts)
        if self.config.derivative_policy is DerivativePolicy.PREFER_IMPROVED:
            for d in facts.derivatives:
                if d.operation != _IMPROVED[want]:
                    continue
                better = self.facts(d.asset_id)
                if better is None or not better.exists or not better.file_available:
                    unit.issue(
                        IssueClass.WARNING,
                        "improved_version_unusable",
                        f"the improved {want} {d.asset_id} of {asset_id} is not usable; "
                        "the recording is used",
                        item_id=item_id,
                        evidence=d.asset_id,
                    )
                    continue
                chosen = Chosen(d.asset_id, original, better)
                break
        if not chosen.facts or not chosen.facts.file_available:
            unit.issue(
                IssueClass.CRITICAL,
                "source_file_unavailable",
                f"the file of asset {chosen.asset_id} is not available",
                item_id=item_id,
                evidence=chosen.asset_id,
            )
        return chosen

    def _sound_sources(self) -> tuple[SoundSource, ...]:
        """One audio item per audio-track placement. Without any (no-speech path, or an edit
        that never separated its sound) the picture clips that play their own sound make them."""
        source = self.inputs.source
        found: list[SoundSource] = []
        sound = source.sound()
        if sound:
            for c in sound:
                start = fr(c.timeline_start)
                found.append(
                    SoundSource(
                        item_id=f"a:{c.clip_id}",
                        clip_id=c.clip_id,
                        asset_id=c.asset_id,
                        role=TrackRole.DIALOGUE if c.layer == 0 else TrackRole.PRODUCTION,
                        start=start,
                        end=start + fr(c.timeline_duration),
                        source_start=fr(c.source_start),
                        linked_picture=f"v:{c.linked}" if c.linked in self.clips else None,
                    )
                )
        else:
            for c in source.picture():
                if c.audio != "source_sync":
                    continue
                facts = self.facts(c.asset_id)
                if facts is not None and facts.inspected and not facts.has_audio:
                    continue
                start = fr(c.timeline_start)
                found.append(
                    SoundSource(
                        item_id=f"a:{c.clip_id}",
                        clip_id=c.clip_id,
                        asset_id=c.asset_id,
                        role=TrackRole.DIALOGUE if c.layer == 0 else TrackRole.PRODUCTION,
                        start=start,
                        end=start + fr(c.timeline_duration),
                        source_start=fr(c.source_start),
                        linked_picture=f"v:{c.clip_id}",
                    )
                )
        return tuple(sorted(found, key=lambda s: (s.start, s.end, s.item_id)))

    def sound_near(self, at: Fraction) -> tuple[SoundSource, ...]:
        reach = 2 / self.fps
        return tuple(
            s for s in self.sound if abs(s.end - at) <= reach or abs(s.start - at) <= reach
        )


# --- unit digests --------------------------------------------------------------------------------


def _facts_of(ctx: Context, *ids: str | None) -> list[AssetFacts | None]:
    wanted: dict[str, None] = {}
    for i in ids:
        if i:
            wanted[i] = None
            f = ctx.facts(i)
            if f is not None:
                for d in f.derivatives:
                    wanted[d.asset_id] = None
    return [ctx.facts(i) for i in wanted]


def _clip_digest(ctx: Context, clip: SourceClip) -> str:
    treatment = ctx.treatments.get(clip.clip_id)
    owned = ctx.transition_owner.get(clip.clip_id, [])
    neighbours: list[SourceClip] = []
    asset_ids: list[str | None] = [clip.asset_id, treatment.output_asset_id if treatment else None]
    for t in owned:
        asset_ids.append(t.overlay_asset_id)
        for other in (t.outgoing_clip_id, t.incoming_clip_id):
            if other and other != clip.clip_id and other in ctx.clips:
                neighbours.append(ctx.clips[other])
                asset_ids.append(ctx.clips[other].asset_id)
    reframes = [ctx.inputs.reframing.get(f.recommendation_id) for f in clip.framing]
    return digest(
        (
            clip,
            treatment,
            owned,
            neighbours,
            # a baked neighbour holds no footage past its own cut (see ``_handles``)
            [ctx.treatments.get(n.clip_id) for n in neighbours],
            reframes,
            _facts_of(ctx, *asset_ids),
            [ctx.sound_near(fr(t.audio_at)) for t in owned],
            ctx.fps,
            ctx.config.fingerprint(),
            ctx.inputs.source.frame_rate,
            _aspect(ctx),
            ctx.inputs.style.fingerprint,
        )
    )


def _aspect(ctx: Context) -> str:
    return f"{ctx.inputs.style.width}x{ctx.inputs.style.height}"


# --- the picture and sound of one placement ----------------------------------------------------


def clip_unit(ctx: Context, clip: SourceClip, previous: TimelineSpecification | None) -> Unit:
    unit = Unit(f"clip:{clip.clip_id}", _clip_digest(ctx, clip))
    if _reuse(unit, previous, ctx):
        return unit
    if clip.picture:
        _picture(ctx, unit, clip)
    for sound in ctx.sound:
        if sound.clip_id == clip.clip_id:
            _sound_item(ctx, unit, clip, sound)
    for transition in ctx.transition_owner.get(clip.clip_id, []):
        _transition(ctx, unit, transition)
    return unit


def _reuse(unit: Unit, previous: TimelineSpecification | None, ctx: Context) -> bool:
    if previous is None:
        return False
    if dict(previous.compilation.unit_digests).get(unit.unit_id) != unit.digest:
        return False
    unit.video = [i for i in previous.video if i.unit_id == unit.unit_id]
    unit.overlays = [i for i in previous.overlays if i.unit_id == unit.unit_id]
    unit.audio = [i for i in previous.audio if i.unit_id == unit.unit_id]
    unit.effects = [i for i in previous.effects if i.unit_id == unit.unit_id]
    unit.transitions = [i for i in previous.transitions if i.unit_id == unit.unit_id]
    unit.automation = [i for i in previous.automation if i.unit_id == unit.unit_id]
    unit.issues = [i for i in previous.issues if i.unit_id == unit.unit_id]
    unit.reused = True
    return True


def _picture(ctx: Context, unit: Unit, clip: SourceClip) -> None:
    item_id = f"v:{clip.clip_id}"
    t0 = fr(clip.timeline_start)
    t1 = t0 + fr(clip.timeline_duration)
    s0, s1 = fr(clip.source_start), fr(clip.source_end)
    chosen = ctx.pick(clip.asset_id, "video", unit, item_id=item_id)
    _check_range(ctx, unit, chosen, s0, s1, item_id, t0, t1)
    facts = chosen.facts
    if facts is not None and facts.media_type and not facts.has_video and facts.inspected:
        unit.issue(
            IssueClass.CRITICAL,
            "no_picture_stream",
            f"asset {chosen.asset_id} has no picture stream but is placed on the picture track",
            item_id=item_id,
            start=t0,
            end=t1,
        )
    sf, ef, _ = ctx.span(t0, t1)
    same_rate = ctx.fps == fr(ctx.inputs.source.frame_rate)
    if clip.start_frame is not None and same_rate and clip.start_frame != sf:
        unit.issue(
            IssueClass.WARNING,
            "frame_differs_from_edit",
            f"{clip.clip_id} starts at frame {sf}, the edit plan stated {clip.start_frame}",
            item_id=item_id,
            start=t0,
        )
    rate = None if facts is None else facts.frame_rate
    variable = bool(facts and facts.frame_rate_variable)
    asset_id, source_start, source_end, retime = chosen.asset_id, s0, s1, None
    effects: list[EffectSpec] = []
    origin = ItemOrigin.EDIT
    treatment = ctx.treatments.get(clip.clip_id)
    if treatment is not None and treatment.status != "original":
        baked = _baked_asset(ctx, unit, treatment, item_id, sf, ef, t0, t1)
        effects = _effects(ctx, unit, clip, treatment, item_id, baked is not None)
        if baked is not None:
            asset_id, origin = baked, ItemOrigin.VFX
            source_start, source_end = Fraction(0), t1 - t0
            retime = _retime(treatment, baked=True)
            rate, variable = rat(ctx.fps), False
        else:
            retime = _retime(treatment, baked=False)
    speed = (source_end - source_start) / (t1 - t0)
    unit.video.append(
        VideoItem(
            item_id=item_id,
            unit_id=unit.unit_id,
            clip_id=clip.clip_id,
            track_id="",
            origin=origin,
            asset_id=asset_id,
            original_asset_id=chosen.original_id,
            source_start=rat(source_start),
            source_end=rat(source_end),
            source_start_frame=(
                frame_at(source_start, fr(rate)) if rate is not None and not variable else None
            ),
            timeline_start=rat(t0),
            timeline_end=rat(t1),
            start_frame=sf,
            end_frame=ef,
            speed=rat(speed),
            layer=clip.layer,
            section_id=clip.section_id,
            beat_id=clip.beat_id,
            retime=retime,
            reframe=_reframe(ctx, unit, clip, item_id),
            effect_ids=tuple(e.effect_id for e in effects),
            transition_in=None,
            transition_out=None,
            source_frame_rate=rate,
            source_variable_rate=variable,
            original_start=rat(s0),
            original_end=rat(s1),
        )
    )
    unit.effects.extend(effects)
    _conflicts(unit, clip, effects, unit.video[-1])


def _check_range(
    ctx: Context,
    unit: Unit,
    chosen: Chosen,
    s0: Fraction,
    s1: Fraction,
    item_id: str,
    t0: Fraction,
    t1: Fraction,
) -> None:
    facts = chosen.facts
    if facts is None or facts.duration is None:
        return
    slack = Fraction(1) / ctx.fps
    if s1 > fr(facts.duration) + slack:
        unit.issue(
            IssueClass.CRITICAL,
            "source_range_beyond_media",
            f"source range ends at {float(s1):.3f}s but the file is "
            f"{float(fr(facts.duration)):.3f}s",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=chosen.asset_id,
        )


def _baked_asset(
    ctx: Context,
    unit: Unit,
    treatment: TreatmentFact,
    item_id: str,
    sf: int,
    ef: int,
    t0: Fraction,
    t1: Fraction,
) -> str | None:
    """The rendered VFX clip, when there is a usable one; else ``None`` (original footage)."""
    active = [e for e in treatment.effects if e.status in _ACTIVE_EFFECT]
    if not active and not treatment.segments:
        return None
    out = treatment.output_asset_id
    facts = ctx.facts(out)
    if (
        treatment.status in _RENDERED_STATES
        and out
        and facts
        and facts.exists
        and facts.file_available
    ):
        if treatment.output_frames and treatment.output_frames < ef - sf:
            unit.issue(
                IssueClass.CRITICAL,
                "vfx_clip_too_short",
                f"the rendered clip has {treatment.output_frames} frames, the timeline "
                f"needs {ef - sf}",
                item_id=item_id,
                start=t0,
                end=t1,
                evidence=out,
            )
        return out
    unit.issue(
        IssueClass.WARNING,
        "vfx_not_rendered",
        f"the effects of {treatment.clip_id} have no rendered clip ({treatment.status}); "
        "the plan's fallback (original footage) is used",
        item_id=item_id,
        start=t0,
        end=t1,
        evidence=out or "",
    )
    return None


def _effects(
    ctx: Context,
    unit: Unit,
    clip: SourceClip,
    treatment: TreatmentFact,
    item_id: str,
    baked: bool,
) -> list[EffectSpec]:
    found: list[EffectSpec] = []
    for e in sorted(treatment.effects, key=lambda x: (fr(x.timeline_start), x.effect_id)):
        found.append(_effect(unit, clip, e, item_id, baked))
    return found


def _effect(unit: Unit, clip: SourceClip, e: EffectFact, item_id: str, baked: bool) -> EffectSpec:
    if e.status in _ACTIVE_EFFECT and baked:
        execution, detail = Execution.BAKED, ""
    elif e.status in {"bypassed", "rejected"}:
        execution, detail = Execution.UNAVAILABLE, f"not applied by the plan ({e.status})"
    else:
        execution, detail = Execution.FALLBACK, f"{e.status}: {e.fallback}"
        if e.status in {"not_available", "error", "planned"}:
            unit.issue(
                IssueClass.WARNING,
                "effect_not_applied",
                f"effect {e.effect_type} on {clip.clip_id} is not applied ({e.status}); "
                f"fallback: {e.fallback}",
                item_id=e.effect_id,
                start=fr(e.timeline_start),
                end=fr(e.timeline_end),
            )
    return EffectSpec(
        effect_id=e.effect_id,
        unit_id=unit.unit_id,
        effect_type=e.effect_type,
        clip_id=clip.clip_id,
        item_id=item_id,
        preset=e.preset,
        mode=e.mode,
        parameters=e.parameters,
        intensity=e.intensity,
        timeline_start=e.timeline_start,
        timeline_end=e.timeline_end,
        start_frame=e.start_frame,
        end_frame=e.end_frame,
        execution=execution,
        fallback=e.fallback,
        masks=tuple(
            EffectMask(
                mask_id=m.mask_id,
                kind=m.kind,
                keyframes=m.keyframes,
                feather=m.feather,
                track_refs=m.track_refs,
            )
            for m in e.masks
        ),
        detail=detail,
    )


def _retime(treatment: TreatmentFact, *, baked: bool) -> Retime | None:
    if not any(s.kind != "normal" for s in treatment.segments):
        return None
    return Retime(
        segments=tuple(
            RetimeSegment(
                kind=s.kind,
                timeline_start=s.timeline_start,
                timeline_end=s.timeline_end,
                source_start=s.source_start,
                source_end=s.source_end,
                rate=s.rate,
                start_frame=s.start_frame,
                end_frame=s.end_frame,
            )
            for s in treatment.segments
        ),
        interpolation=treatment.interpolation,
        baked=baked,
        audio=treatment.audio,
    )


def _conflicts(unit: Unit, clip: SourceClip, effects: list[EffectSpec], item: VideoItem) -> None:
    active = [e for e in effects if e.execution in {Execution.BAKED, Execution.NATIVE}]
    retimes = [e for e in active if e.effect_type in RETIME_EFFECTS]
    for i, a in enumerate(retimes):
        for b in retimes[i + 1 :]:
            if overlap(
                fr(a.timeline_start), fr(a.timeline_end), fr(b.timeline_start), fr(b.timeline_end)
            ):
                unit.issue(
                    IssueClass.CREATIVE_CONFLICT,
                    "conflicting_time_effects",
                    f"{a.effect_type} and {b.effect_type} overlap on {clip.clip_id}",
                    item_id=b.effect_id,
                    start=max(fr(a.timeline_start), fr(b.timeline_start)),
                )
    if item.reframe:
        for e in active:
            if e.effect_type == "punch_in":
                unit.issue(
                    IssueClass.CREATIVE_CONFLICT,
                    "reframe_and_punch_in",
                    f"{clip.clip_id} has a reframing crop and a punch-in zoom: both change the "
                    "framing of the same picture",
                    item_id=e.effect_id,
                    start=fr(e.timeline_start),
                    end=fr(e.timeline_end),
                )


def _reframe(ctx: Context, unit: Unit, clip: SourceClip, item_id: str) -> tuple[Reframe, ...]:
    found: list[Reframe] = []
    aspect = ctx.inputs.style
    for ref in sorted(clip.framing, key=lambda f: (fr(f.source_start), f.recommendation_id)):
        facts = ctx.inputs.reframing.get(ref.recommendation_id)
        if not ref.executable:
            unit.issue(
                IssueClass.WARNING,
                "framing_not_executable",
                f"framing {ref.recommendation_id} ({ref.strategy}) cannot be applied as it "
                "stands; the original framing is kept",
                item_id=item_id,
            )
            continue
        if facts is None:
            unit.issue(
                IssueClass.WARNING,
                "framing_unresolved",
                f"the crop of framing {ref.recommendation_id} is not in the reframing analysis "
                f"{ref.analysis_id}; the original framing is kept",
                item_id=item_id,
                evidence=ref.recommendation_id,
            )
            continue
        if not _same_shape(ref.target, aspect.width, aspect.height):
            unit.issue(
                IssueClass.CREATIVE_CONFLICT,
                "framing_target_mismatch",
                f"framing {ref.recommendation_id} was made for {ref.target} but the timeline is "
                f"{aspect.width}x{aspect.height}",
                item_id=item_id,
            )
        found.append(_reframe_of(ref, facts))
    for i, a in enumerate(found):
        ra = next(f for f in clip.framing if f.recommendation_id == a.recommendation_id)
        for b in found[i + 1 :]:
            rb = next(f for f in clip.framing if f.recommendation_id == b.recommendation_id)
            if overlap(
                fr(ra.source_start), fr(ra.source_end), fr(rb.source_start), fr(rb.source_end)
            ):
                unit.issue(
                    IssueClass.CREATIVE_CONFLICT,
                    "conflicting_framing",
                    f"framings {a.recommendation_id} and {b.recommendation_id} cover the same "
                    f"part of {clip.clip_id}",
                    item_id=item_id,
                )
    return tuple(found)


def _reframe_of(ref: FramingRef, facts: ReframeFacts) -> Reframe:
    return Reframe(
        target=ref.target,
        recommendation_id=ref.recommendation_id,
        analysis_id=ref.analysis_id,
        strategy=facts.strategy,
        keys=tuple(
            CropKey(source_time=k.time, x=k.x, y=k.y, w=k.w, h=k.h, interpolation=k.interpolation)
            for k in facts.keyframes
        ),
    )


def _same_shape(target: str, width: int, height: int) -> bool:
    parts = target.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or int(parts[1]) == 0:
        return True  # an unknown shape name is not judged here
    return Fraction(int(parts[0]), int(parts[1])) == Fraction(width, height)


def _sound_item(ctx: Context, unit: Unit, clip: SourceClip, sound: SoundSource) -> None:
    chosen = ctx.pick(clip.asset_id, "audio", unit, item_id=sound.item_id)
    s0, s1 = fr(clip.source_start), fr(clip.source_end)
    t0, t1 = sound.start, sound.end
    _check_range(ctx, unit, chosen, s0, s1, sound.item_id, t0, t1)
    facts = chosen.facts
    if facts is not None and facts.inspected and not facts.has_audio:
        unit.issue(
            IssueClass.CRITICAL,
            "no_audio_stream",
            f"asset {chosen.asset_id} has no audio stream but is placed on the audio track",
            item_id=sound.item_id,
            start=t0,
            end=t1,
        )
    elif facts is not None and not facts.inspected:
        unit.issue(
            IssueClass.WARNING,
            "audio_presence_unverified",
            f"asset {chosen.asset_id} has no stored inspection: its audio stream is unverified",
            item_id=sound.item_id,
        )
    rate = ctx.sample_rate
    unit.audio.append(
        AudioItem(
            item_id=sound.item_id,
            unit_id=unit.unit_id,
            origin=ItemOrigin.EDIT,
            role=sound.role,
            track_id="",
            source_ref=clip.clip_id,
            asset_id=chosen.asset_id,
            original_asset_id=chosen.original_id,
            source_start=rat(s0),
            source_end=rat(s1),
            timeline_start=rat(t0),
            timeline_end=rat(t1),
            sample_rate=rate,
            start_sample=sample_at(t0, rate),
            end_sample=sample_at(t1, rate),
            fade_in=clip.fade_in,
            fade_out=clip.fade_out,
            speed=rat((s1 - s0) / (t1 - t0)),
            linked_item_id=sound.linked_picture,
        )
    )


# --- transitions ---------------------------------------------------------------------------------


def _transition(ctx: Context, unit: Unit, t: TransitionFact) -> None:
    out_id = f"v:{t.outgoing_clip_id}" if t.outgoing_clip_id else None
    in_id = f"v:{t.incoming_clip_id}" if t.incoming_clip_id else None
    kind, wanted, adjusted = t.kind, t.fallback_wanted, False
    handles = _handles(ctx, t)
    execution = Execution.NATIVE
    overlay_item: OverlayItem | None = None
    if t.family == OVERLAY_FAMILY:
        overlay_item = _transition_overlay(ctx, unit, t)
        execution = Execution.RENDERED if overlay_item else Execution.UNAVAILABLE
    problem = None
    shorter = min(
        (
            round(fr(c.timeline_duration) * ctx.fps)
            for c in (
                ctx.clips.get(t.outgoing_clip_id or ""),
                ctx.clips.get(t.incoming_clip_id or ""),
            )
            if c is not None
        ),
        default=None,
    )
    if execution is Execution.UNAVAILABLE:
        problem = "its rendered overlay is not available"
    elif shorter is not None and kind != "hard_cut" and t.duration_frames > shorter:
        problem = f"it lasts {t.duration_frames} frames, longer than the shorter clip ({shorter})"
    elif not handles.sufficient and kind != "hard_cut":
        problem = (
            f"the sources hold too little footage around the cut (needs {handles.required_out}/"
            f"{handles.required_in} frames, has {handles.available_out}/{handles.available_in})"
        )
    if problem is not None:
        required = t.status in {"requested", "brand", "overridden"}
        unit.issue(
            IssueClass.CREATIVE_CONFLICT if required else IssueClass.AUTO_RESOLVED,
            "transition_not_executable",
            f"transition {t.transition_id} ({t.kind}) cannot run: {problem}",
            item_id=t.transition_id,
            start=fr(t.timeline_start),
            end=fr(t.timeline_end),
            resolution="executed as a hard cut at the same frame (the plan's always-available "
            "default); the wanted kind is kept in `wanted_kind`",
        )
        wanted, kind, adjusted, execution = t.kind, "hard_cut", True, Execution.FALLBACK
        overlay_item = None
    if overlay_item is not None:
        unit.overlays.append(overlay_item)
    if adjusted:
        start = end = fr(t.cut_at)
    else:
        start, end = fr(t.timeline_start), fr(t.timeline_end)
    sf, ef, n = ctx.span(start, end)
    unit.transitions.append(
        TransitionSpec(
            transition_id=t.transition_id,
            unit_id=unit.unit_id,
            boundary=t.boundary,
            kind=kind,
            family="cut" if adjusted else t.family,
            preset_id=t.preset_id,
            alignment=t.alignment,
            direction=t.direction,
            intensity=0 if adjusted else t.intensity,
            cut_at=t.cut_at,
            cut_frame=frame_at(fr(t.cut_at), ctx.fps),
            timeline_start=rat(start),
            timeline_end=rat(end),
            start_frame=sf,
            end_frame=ef,
            duration_frames=n,
            outgoing_item_id=out_id,
            incoming_item_id=in_id,
            handles=handles,
            execution=execution,
            overlay_item_id=overlay_item.item_id if overlay_item else None,
            wanted_kind=wanted,
            adjusted_by_compiler=adjusted,
            reasons=t.reasons,
        )
    )
    _audio_transition(ctx, unit, t)


def _will_bake(ctx: Context, clip: SourceClip) -> bool:
    """Whether the clip will be placed as its rendered VFX file (the same test ``_baked_asset``
    makes, without its findings): such a file holds no footage beyond the clip's own range, so
    a transition cannot read handles from it."""
    treatment = ctx.treatments.get(clip.clip_id)
    if treatment is None or treatment.status == "original":
        return False
    if not any(e.status in _ACTIVE_EFFECT for e in treatment.effects) and not treatment.segments:
        return False
    out = treatment.output_asset_id
    facts = ctx.facts(out)
    if (
        treatment.status not in _RENDERED_STATES
        or not out
        or facts is None
        or not facts.exists
        or not facts.file_available
    ):
        return False
    sf, ef, _ = ctx.span(
        fr(clip.timeline_start), fr(clip.timeline_start) + fr(clip.timeline_duration)
    )
    return not (treatment.output_frames and treatment.output_frames < ef - sf)


def _handles(ctx: Context, t: TransitionFact) -> Handles:
    """The plan's handle needs against what it stated AND what the files hold now: the stricter
    of the two counts (a shortfall either side knows about is real; ``None`` is unknown)."""
    slack = ctx.config.handle_tolerance_frames
    stated_out, stated_in = t.available_out, t.available_in
    out_clip = ctx.clips.get(t.outgoing_clip_id or "")
    in_clip = ctx.clips.get(t.incoming_clip_id or "")
    found_out: int | None = None
    found_in: int | None = None
    if out_clip is not None:
        f = ctx.facts(out_clip.asset_id)
        if _will_bake(ctx, out_clip):
            found_out = 0  # the baked file ends exactly where the clip does
        elif f is not None and f.duration is not None:
            found_out = max(0, frame_at(fr(f.duration) - fr(out_clip.source_end), ctx.fps))
    if in_clip is not None:
        found_in = (
            0 if _will_bake(ctx, in_clip) else max(0, frame_at(fr(in_clip.source_start), ctx.fps))
        )
    avail_out = _stricter(stated_out, found_out)
    avail_in = _stricter(stated_in, found_in)
    ok_out = t.required_out == 0 or (avail_out is not None and avail_out + slack >= t.required_out)
    ok_in = t.required_in == 0 or (avail_in is not None and avail_in + slack >= t.required_in)
    return Handles(
        required_out=t.required_out,
        required_in=t.required_in,
        available_out=avail_out,
        available_in=avail_in,
        sufficient=ok_out and ok_in,
    )


def _stricter(a: int | None, b: int | None) -> int | None:
    known = [x for x in (a, b) if x is not None]
    return min(known) if known else None


def _transition_overlay(ctx: Context, unit: Unit, t: TransitionFact) -> OverlayItem | None:
    facts = ctx.facts(t.overlay_asset_id)
    if (
        t.overlay_state not in _RENDERED_STATES
        or not t.overlay_asset_id
        or t.overlay_start is None
        or t.overlay_duration is None
        or facts is None
        or not facts.exists
        or not facts.file_available
    ):
        return None
    start = fr(t.overlay_start)
    end = start + fr(t.overlay_duration)
    sf, ef, _ = ctx.span(start, end)
    return OverlayItem(
        item_id=f"o:transition:{t.transition_id}",
        unit_id=unit.unit_id,
        origin=ItemOrigin.TRANSITION,
        source_ref=t.transition_id,
        kind="transition_overlay",
        track_id="",
        asset_id=t.overlay_asset_id,
        asset_start_frame=0,
        timeline_start=rat(start),
        timeline_end=rat(end),
        start_frame=sf,
        end_frame=ef,
        composite=Composite.ALPHA_OVER,
        execution=Execution.RENDERED,
    )


def _audio_transition(ctx: Context, unit: Unit, t: TransitionFact) -> None:
    if t.audio_kind in {"none", ""}:
        return
    at = fr(t.audio_at)
    near = ctx.sound_near(at)
    out = next(
        (s for s in near if s.role is TrackRole.DIALOGUE and abs(s.end - at) <= 2 / ctx.fps), None
    )
    inc = next(
        (s for s in near if s.role is TrackRole.DIALOGUE and abs(s.start - at) <= 2 / ctx.fps), None
    )
    targets = tuple(s.item_id for s in (out, inc) if s is not None)
    if not targets:
        return
    kind = "crossfade" if t.audio_kind in _SOUND_CROSSFADE else t.audio_kind
    before, after = fr(t.audio_before), fr(t.audio_after)
    for side, sound, need in (("out", out, after), ("in", inc, before)):
        if sound is None or need == 0:
            continue
        facts = ctx.facts(sound.asset_id)
        clip = ctx.clips[sound.clip_id]
        if facts is None or facts.duration is None:
            continue
        room = fr(facts.duration) - fr(clip.source_end) if side == "out" else fr(clip.source_start)
        if room < need:
            unit.issue(
                IssueClass.AUTO_RESOLVED,
                "audio_handle_short",
                f"the {'outgoing' if side == 'out' else 'incoming'} sound of {t.transition_id} "
                f"has {float(room):.3f}s of handle, the crossfade wants {float(need):.3f}s",
                item_id=t.transition_id,
                start=at,
                resolution="the fade on that side is shortened to the handle the file holds",
            )
            if side == "out":
                after = max(Fraction(0), room)
            else:
                before = max(Fraction(0), room)
    unit.automation.append(
        AudioAutomation(
            automation_id=f"x:{kind}:{t.transition_id}",
            unit_id=unit.unit_id,
            kind=kind,
            timeline_start=rat(max(Fraction(0), at - before)),
            timeline_end=rat(at + after),
            target_item_ids=targets,
            curve=t.audio_curve,
            timeline_at=rat(at),
            before=rat(before),
            after=rat(after),
            sample_rate=t.audio_sample_rate,
            source_ref=t.transition_id,
        )
    )
