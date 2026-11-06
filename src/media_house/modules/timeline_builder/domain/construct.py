"""Construction: the specification's facts become timeline elements. Pure mapping, no findings.

Every function here only translates: a fact in, an element out, with the same exact times and
frames, the fixed processing order of a picture clip, and the id of the specification item it came
from. What is wrong, missing or unsupported is judged afterwards by ``validation`` on the finished
elements (so a reused element is judged exactly like a new one).
"""

import re
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.timeline_builder.domain.digest import digest
from media_house.modules.timeline_builder.domain.facts import (
    AudioFact,
    AutomationFact,
    CropKeyFact,
    EffectFact,
    LiveSources,
    OverlayFact,
    SpecFacts,
    SubtitleFact,
    TrackFact,
    TransitionFact,
    VideoFact,
)
from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    BuiltTrack,
    BuiltTransition,
    ChainStep,
    CropKeyframe,
    CropPlan,
    EffectStep,
    HandleCheck,
    MaskPlan,
    MixInstruction,
    SourceMedia,
    SourceUse,
    SubtitleCue,
    SubtitleTrack,
    TimeMap,
    TimeSegment,
    Transform,
    VideoClip,
)
from media_house.modules.timeline_builder.domain.profile import (
    CUT_KINDS,
    DIP_KINDS,
    DISSOLVE_KINDS,
    FADE_KINDS,
    BuilderConfig,
    Capabilities,
)
from media_house.modules.timeline_builder.domain.timing import (
    fr,
    frame_at,
    rat,
    time_of_frame,
)
from media_house.modules.timeline_builder.domain.values import (
    BlendMode,
    ChainStage,
    ClipKind,
    ClipState,
    Execution,
    Provenance,
    Role,
    TrackKind,
    TransitionMode,
)

#: What a person sees a track under. All lanes of one role share the group.
GROUP_OF_ROLE: dict[Role, str] = {
    Role.PRIMARY: "Video · Primary footage",
    Role.COVER: "Video · Cover footage",
    Role.GRAPHICS: "Video · Graphics",
    Role.CAPTIONS: "Video · Captions",
    Role.EFFECTS: "Video · Effects and transition overlays",
    Role.DIALOGUE: "Audio · Dialogue",
    Role.PRODUCTION: "Audio · Production sound",
    Role.MUSIC: "Audio · Music",
    Role.AMBIENCE: "Audio · Ambience",
    Role.SFX: "Audio · Sound effects",
}


@dataclass(slots=True)
class Ctx:
    """Read-only lookups over one specification, built once per build."""

    spec: SpecFacts
    live: LiveSources
    config: BuilderConfig
    caps: Capabilities
    fps: Fraction
    context_key: str
    tracks: dict[str, TrackFact] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    effects_of: dict[str, list[EffectFact]] = field(default_factory=dict)
    transitions: dict[str, TransitionFact] = field(default_factory=dict)
    transition_of_overlay: dict[str, TransitionFact] = field(default_factory=dict)
    automation_of: dict[str, list[str]] = field(default_factory=dict)
    audio_of_video: dict[str, str] = field(default_factory=dict)
    cues: tuple[SubtitleCue, ...] = ()

    @classmethod
    def of(
        cls, spec: SpecFacts, live: LiveSources, config: BuilderConfig, caps: Capabilities
    ) -> "Ctx":
        tb = spec.timebase
        fps = fr(tb.frame_rate)
        ctx = cls(
            spec=spec,
            live=live,
            config=config,
            caps=caps,
            fps=fps,
            context_key=digest(
                {
                    "fps": str(fps),
                    "sample_rate": tb.sample_rate,
                    "size": [tb.width, tb.height],
                    "fit": config.fit_mode,
                    "channels": config.audio_channels,
                    "color": tb.color,
                    "caps": [caps.transitions, caps.effects, caps.reverse, caps.speed_ramps],
                }
            ),
        )
        ctx.tracks = {t.track_id: t for t in spec.tracks}
        ctx.sections = {s.section_id: s.label for s in spec.sections}
        for e in spec.effects:
            ctx.effects_of.setdefault(e.item_id, []).append(e)
        ctx.transitions = {t.transition_id: t for t in spec.transitions}
        for t in spec.transitions:
            if t.overlay_item_id:
                ctx.transition_of_overlay[t.overlay_item_id] = t
        for a in spec.automation:
            for item in a.target_item_ids:
                ctx.automation_of.setdefault(item, []).append(a.automation_id)
        for au in spec.audio:
            if au.linked_item_id and au.linked_item_id not in ctx.audio_of_video:
                ctx.audio_of_video[au.linked_item_id] = au.item_id
        ctx.cues = parse_srt(spec.subtitles.srt, fps) if spec.subtitles else ()
        return ctx

    def name_of(self, asset_id: str | None) -> str:
        found = self.live.get(asset_id)
        return found.name if found and found.name else (asset_id or "")

    def section(self, section_id: str | None) -> str:
        return self.sections.get(section_id or "", "")


# --- text helpers ---------------------------------------------------------------------------------


def timecode(t: Fraction) -> str:
    """``HH:MM:SS.mmm`` (floor), for names a person reads."""
    total_ms = int(t * 1000)
    h, rest = divmod(total_ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str, fps: Fraction) -> tuple[SubtitleCue, ...]:
    """Cues of an SRT text, frames computed once from the exact millisecond times."""
    cues: list[SubtitleCue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        for i, line in enumerate(lines):
            found = _SRT_TIME.search(line)
            if found is None:
                continue
            h0, m0, s0, ms0, h1, m1, s1, ms1 = (int(g) for g in found.groups())
            start = Fraction(((h0 * 60 + m0) * 60 + s0) * 1000 + ms0, 1000)
            end = Fraction(((h1 * 60 + m1) * 60 + s1) * 1000 + ms1, 1000)
            cues.append(
                SubtitleCue(
                    index=len(cues) + 1,
                    start_frame=frame_at(start, fps),
                    end_frame=frame_at(end, fps),
                    text="\n".join(lines[i + 1 :]),
                )
            )
            break
    return tuple(cues)


def subtitle_track(ctx: Ctx, sub: SubtitleFact | None) -> SubtitleTrack | None:
    if sub is None:
        return None
    return SubtitleTrack(
        language=sub.language,
        cues=ctx.cues,
        srt=sub.srt,
        vtt=sub.vtt,
        source_plan_id=sub.source_plan_id,
    )


# --- source media ---------------------------------------------------------------------------------


def source_media(ctx: Ctx, used_by: dict[str, list[str]]) -> tuple[SourceMedia, ...]:
    """One entry per file, however many clip instances play it."""
    found: list[SourceMedia] = []
    for a in sorted(ctx.spec.assets, key=lambda a: a.asset_id):
        live = ctx.live.get(a.asset_id)
        color = a.color
        found.append(
            SourceMedia(
                source_id=a.asset_id,
                original_asset_id=a.original_asset_id,
                relation=a.relation,
                media_type=a.media_type,
                name=ctx.name_of(a.asset_id),
                duration=a.duration,
                frame_rate=a.frame_rate,
                frame_rate_variable=a.frame_rate_variable,
                sample_rate=a.sample_rate,
                channels=a.channels,
                width=a.width,
                height=a.height,
                checksum=a.checksum,
                color_space=color.space if color else None,
                color_primaries=color.primaries if color else None,
                color_transfer=color.transfer if color else None,
                color_range=color.range if color else None,
                available=a.available and (live.available if live else True),
                proxy_asset_id=live.proxy_asset_id if live else None,
                used_by=tuple(sorted(set(used_by.get(a.asset_id, ())))),
            )
        )
    return tuple(found)


# --- picture clips --------------------------------------------------------------------------------


def video_digest(ctx: Ctx, v: VideoFact) -> str:
    return digest(
        {
            "item": v,
            "track": ctx.tracks.get(v.track_id),
            "effects": sorted(ctx.effects_of.get(v.item_id, []), key=lambda e: e.effect_id),
            "section": ctx.section(v.section_id),
            "audio": ctx.audio_of_video.get(v.item_id),
            "asset": ctx.spec.asset(v.asset_id),
            "live": ctx.live.get(v.asset_id),
            "context": ctx.context_key,
        }
    )


def video_clip(ctx: Ctx, v: VideoFact, source_digest: str) -> VideoClip:
    track = ctx.tracks[v.track_id]
    role = Role(track.role)
    if v.origin == "vfx":
        kind = ClipKind.VFX
    else:
        kind = ClipKind.COVER if role is Role.COVER else ClipKind.PRIMARY
    time_map = _time_map(v)
    crops = _crops(ctx, v)
    effects = tuple(
        _effect(ctx, e) for e in sorted(ctx.effects_of.get(v.item_id, []), key=_effect_key)
    )
    transform = Transform(fit=ctx.config.fit_mode)
    return VideoClip(
        clip_id=v.item_id,
        track_id=v.track_id,
        kind=kind,
        name=_video_name(ctx, v, kind),
        source=SourceUse(
            source_id=v.asset_id,
            original_asset_id=v.original_asset_id,
            source_in=v.source_start,
            source_out=v.source_end,
            source_in_frame=v.source_start_frame,
            original_in=v.original_start,
            original_out=v.original_end,
        ),
        timeline_start=v.timeline_start,
        timeline_end=v.timeline_end,
        start_frame=v.start_frame,
        end_frame=v.end_frame,
        speed=v.speed,
        time_map=time_map,
        transform=transform,
        crops=crops,
        effects=effects,
        chain=_chain(time_map, crops, effects),
        opacity=1.0,
        blend=BlendMode(track.composite),
        transition_in=v.transition_in,
        transition_out=v.transition_out,
        audio_link=ctx.audio_of_video.get(v.item_id),
        section_id=v.section_id,
        beat_id=v.beat_id,
        state=ClipState.RESOLVED,
        provenance=Provenance.AI,
        origin_ref=v.item_id,
        source_digest=source_digest,
    )


def _effect_key(e: EffectFact) -> tuple[int, int, str]:
    return (e.start_frame, e.end_frame, e.effect_id)


def _effect(ctx: Ctx, e: EffectFact) -> EffectStep:
    execution = Execution(e.execution)
    native = execution is Execution.NATIVE
    return EffectStep(
        effect_id=e.effect_id,
        effect_type=e.effect_type,
        preset=e.preset,
        mode=e.mode,
        parameters=e.parameters,
        intensity=e.intensity,
        timeline_start=e.timeline_start,
        timeline_end=e.timeline_end,
        start_frame=e.start_frame,
        end_frame=e.end_frame,
        execution=execution,
        applied=native and ctx.caps.allows_effect(e.effect_type),
        fallback=e.fallback,
        masks=tuple(
            MaskPlan(
                mask_id=m.mask_id,
                kind=m.kind,
                keyframes=m.keyframes,
                feather=m.feather,
                track_refs=m.track_refs,
            )
            for m in e.masks
        ),
        detail=e.detail,
    )


def _time_map(v: VideoFact) -> TimeMap | None:
    if v.retime is None:
        return None
    return TimeMap(
        segments=tuple(
            TimeSegment(
                kind=s.kind,
                timeline_start=s.timeline_start,
                timeline_end=s.timeline_end,
                source_start=s.source_start,
                source_end=s.source_end,
                rate=s.rate,
                start_frame=s.start_frame,
                end_frame=s.end_frame,
            )
            for s in v.retime.segments
        ),
        interpolation=v.retime.interpolation,
        applied=not v.retime.baked,
        audio=v.retime.audio,
    )


def _crops(ctx: Ctx, v: VideoFact) -> tuple[CropPlan, ...]:
    plans: list[CropPlan] = []
    for r in sorted(v.reframe, key=lambda r: (r.recommendation_id, r.analysis_id)):
        plans.append(
            CropPlan(
                crop_id=f"crop:{v.item_id}:{r.recommendation_id}",
                target=r.target,
                recommendation_id=r.recommendation_id,
                analysis_id=r.analysis_id,
                strategy=r.strategy,
                keys=tuple(
                    _crop_key(ctx, v, k) for k in sorted(r.keys, key=lambda k: fr(k.source_time))
                ),
            )
        )
    return tuple(plans)


def _crop_key(ctx: Ctx, v: VideoFact, k: CropKeyFact) -> CropKeyframe:
    at = timeline_time_of(v, fr(k.source_time))
    return CropKeyframe(
        source_time=k.source_time,
        timeline_time=None if at is None else rat(at),
        timeline_frame=None if at is None else frame_at(at, ctx.fps),
        x=k.x,
        y=k.y,
        w=k.w,
        h=k.h,
        interpolation=k.interpolation,
    )


def timeline_time_of(v: VideoFact, source_time: Fraction) -> Fraction | None:
    """Where a moment of the recording falls on the timeline, or ``None`` when it does not
    reach the timeline (before time 0, or the clip plays it never)."""
    if v.retime is not None:
        for s in v.retime.segments:
            s0, s1 = fr(s.source_start), fr(s.source_end)
            if fr(s.rate) != 0 and min(s0, s1) <= source_time <= max(s0, s1):
                span = abs(source_time - s0)
                return fr(s.timeline_start) + span / abs(fr(s.rate))
    speed = fr(v.speed)
    if speed == 0:
        return None
    origin = fr(v.original_start if v.original_start is not None else v.source_start)
    at = fr(v.timeline_start) + (source_time - origin) / speed
    return at if at >= 0 else None


def _chain(
    time_map: TimeMap | None, crops: tuple[CropPlan, ...], effects: tuple[EffectStep, ...]
) -> tuple[ChainStep, ...]:
    """The fixed order: retime, fit, crop, effects (by time), composite. Never reordered."""
    steps: list[tuple[ChainStage, str, bool]] = []
    if time_map is not None:
        steps.append((ChainStage.TIME_MAP, "", time_map.applied))
    steps.append((ChainStage.FIT, "", True))
    steps += [(ChainStage.CROP, c.crop_id, True) for c in crops]
    steps += [(ChainStage.EFFECT, e.effect_id, e.applied) for e in effects]
    steps.append((ChainStage.COMPOSITE, "", True))
    return tuple(
        ChainStep(order=i, stage=stage, ref=ref, applied=applied)
        for i, (stage, ref, applied) in enumerate(steps, start=1)
    )


def _video_name(ctx: Ctx, v: VideoFact, kind: ClipKind) -> str:
    section = ctx.section(v.section_id)
    where = f"{ctx.name_of(v.asset_id)} @ {timecode(fr(v.source_start))}"
    label = {ClipKind.COVER: "Cover", ClipKind.VFX: "VFX"}.get(kind, "")
    return " · ".join(p for p in (section, label, where) if p)


# --- overlays -------------------------------------------------------------------------------------


def overlay_digest(ctx: Ctx, o: OverlayFact) -> str:
    return digest(
        {
            "item": o,
            "track": ctx.tracks.get(o.track_id),
            "section": ctx.section(o.section_id),
            "asset": ctx.spec.asset(o.asset_id),
            "live": ctx.live.get(o.asset_id),
            "transition": ctx.transition_of_overlay.get(o.item_id),
            "cue": _cue_for(ctx, o),
            "context": ctx.context_key,
        }
    )


def overlay_clip(ctx: Ctx, o: OverlayFact, source_digest: str) -> VideoClip:
    kind = {
        "graphics": ClipKind.GRAPHIC,
        "captions": ClipKind.CAPTION,
        "transition": ClipKind.TRANSITION_OVERLAY,
    }.get(o.origin, ClipKind.GRAPHIC)
    asset = ctx.spec.asset(o.asset_id)
    live = ctx.live.get(o.asset_id)
    usable = (
        o.asset_id is not None
        and o.execution == Execution.RENDERED.value
        and asset is not None
        and asset.available
        and (live is None or (live.exists and live.available))
    )
    first = time_of_frame(o.asset_start_frame, ctx.fps)
    length = fr(o.timeline_end) - fr(o.timeline_start)
    source = (
        SourceUse(
            source_id=o.asset_id,
            original_asset_id=asset.original_asset_id if asset else o.asset_id,
            source_in=first,
            source_out=rat(fr(first) + length),
            source_in_frame=o.asset_start_frame,
            asset_start_frame=o.asset_start_frame,
        )
        if o.asset_id is not None
        else None
    )
    transform = Transform(fit=ctx.config.fit_mode)
    return VideoClip(
        clip_id=o.item_id,
        track_id=o.track_id,
        kind=kind,
        name=_overlay_name(ctx, o, kind),
        source=source,
        timeline_start=o.timeline_start,
        timeline_end=o.timeline_end,
        start_frame=o.start_frame,
        end_frame=o.end_frame,
        transform=transform,
        chain=_chain(None, (), ()),
        blend=BlendMode(o.composite),
        section_id=o.section_id,
        zone=o.zone,
        identity=_overlay_identity(ctx, o, kind),
        state=ClipState.RESOLVED if usable else ClipState.UNRESOLVED,
        provenance=Provenance.AI,
        origin_ref=o.item_id,
        source_digest=source_digest,
        detail=o.detail if kind is not ClipKind.CAPTION else "",
    )


def _overlay_identity(ctx: Ctx, o: OverlayFact, kind: ClipKind) -> tuple[tuple[str, str], ...]:
    if kind is ClipKind.GRAPHIC:
        return (("graphic_kind", o.kind),)
    if kind is ClipKind.CAPTION:
        return (("caption_look", o.detail),) if o.detail else ()
    t = ctx.transition_of_overlay.get(o.item_id)
    return (("transition_preset", t.preset_id),) if t and t.preset_id else ()


def _cue_for(ctx: Ctx, o: OverlayFact) -> str:
    if o.origin != "captions":
        return ""
    for cue in ctx.cues:
        if o.start_frame <= cue.start_frame < o.end_frame or cue.start_frame <= o.start_frame < (
            cue.end_frame
        ):
            return cue.text
    return ""


def _overlay_name(ctx: Ctx, o: OverlayFact, kind: ClipKind) -> str:
    if kind is ClipKind.CAPTION:
        text = " ".join(_cue_for(ctx, o).split())
        return f"Caption · {text[:40]}" if text else f"Caption · {o.source_ref}"
    if kind is ClipKind.TRANSITION_OVERLAY:
        t = ctx.transition_of_overlay.get(o.item_id)
        return f"Transition overlay · {t.kind if t else o.source_ref}"
    section = ctx.section(o.section_id)
    return " · ".join(p for p in (section, f"Graphic {o.kind}", o.source_ref) if p)


# --- audio clips ----------------------------------------------------------------------------------


def audio_digest(ctx: Ctx, a: AudioFact) -> str:
    return digest(
        {
            "item": a,
            "track": ctx.tracks.get(a.track_id),
            "mix": sorted(ctx.automation_of.get(a.item_id, [])),
            "asset": ctx.spec.asset(a.asset_id),
            "live": ctx.live.get(a.asset_id),
            "context": ctx.context_key,
        }
    )


def audio_clip(ctx: Ctx, a: AudioFact, source_digest: str, section_of: dict[str, str]) -> AudioClip:
    return AudioClip(
        clip_id=a.item_id,
        track_id=a.track_id,
        role=Role(a.role),
        name=f"{a.role.capitalize()} · {ctx.name_of(a.asset_id)} @ {timecode(fr(a.source_start))}",
        source=SourceUse(
            source_id=a.asset_id,
            original_asset_id=a.original_asset_id,
            source_in=a.source_start,
            source_out=a.source_end,
            source_in_frame=None,
        ),
        timeline_start=a.timeline_start,
        timeline_end=a.timeline_end,
        sample_rate=a.sample_rate,
        start_sample=a.start_sample,
        end_sample=a.end_sample,
        gain_db=a.gain_db,
        fade_in=a.fade_in,
        fade_out=a.fade_out,
        channels=a.channels,
        speed=a.speed,
        video_link=a.linked_item_id,
        mix_ids=tuple(sorted(ctx.automation_of.get(a.item_id, []))),
        section_id=section_of.get(a.item_id),
        provenance=Provenance.AI,
        origin_ref=a.item_id,
        source_digest=source_digest,
        note=a.note,
    )


def mix_instruction(a: AutomationFact) -> MixInstruction:
    return MixInstruction(
        mix_id=a.automation_id,
        kind=a.kind,
        timeline_start=a.timeline_start,
        timeline_end=a.timeline_end,
        clip_ids=a.target_item_ids,
        key=a.key,
        depth_db=a.depth_db,
        attack_seconds=a.attack_seconds,
        release_seconds=a.release_seconds,
        curve=a.curve,
        timeline_at=a.timeline_at,
        before=a.before,
        after=a.after,
        sample_rate=a.sample_rate,
        origin_ref=a.source_ref,
        source_digest=digest(a),
    )


# --- transitions ----------------------------------------------------------------------------------


def transition_mode(t: TransitionFact, caps: Capabilities) -> tuple[TransitionMode, bool]:
    """``(mode, supported)``. A kind outside the vocabulary is kept as ``unsupported``: the
    builder never swaps it for another look."""
    execution = Execution(t.execution)
    if execution is Execution.UNAVAILABLE:
        return TransitionMode.UNSUPPORTED, False
    if execution is Execution.RENDERED:
        return TransitionMode.OVERLAY, True
    if execution is Execution.FALLBACK or t.adjusted_by_compiler or t.kind in CUT_KINDS:
        return TransitionMode.CUT, True
    if t.kind in DISSOLVE_KINDS:
        mode = TransitionMode.DISSOLVE
    elif t.kind in FADE_KINDS:
        mode = TransitionMode.FADE
    elif t.kind in DIP_KINDS:
        mode = TransitionMode.DIP
    else:
        return TransitionMode.UNSUPPORTED, False
    return mode, caps.allows_transition(t.kind)


def transition_digest(ctx: Ctx, t: TransitionFact) -> str:
    return digest(
        {
            "item": t,
            "ends": [
                (v.item_id, v.source_start, v.source_end, v.asset_id)
                for v in ctx.spec.video
                if v.item_id in (t.outgoing_item_id, t.incoming_item_id)
            ],
            "mix": [m for m in ctx.spec.automation if m.source_ref == t.transition_id],
            "context": ctx.context_key,
        }
    )


def built_transition(
    ctx: Ctx, t: TransitionFact, source_digest: str, track_of: dict[str, str]
) -> BuiltTransition:
    mode, supported = transition_mode(t, ctx.caps)
    reads = mode in {TransitionMode.DISSOLVE, TransitionMode.FADE, TransitionMode.DIP}
    tail = max(0, t.end_frame - t.cut_frame) if reads else 0
    head = max(0, t.cut_frame - t.start_frame) if reads else 0
    track = track_of.get(t.outgoing_item_id or "") or track_of.get(t.incoming_item_id or "") or ""
    mix = next(
        (m.automation_id for m in ctx.spec.automation if m.source_ref == t.transition_id), None
    )
    h = t.handles
    return BuiltTransition(
        transition_id=t.transition_id,
        track_id=track,
        kind=t.kind,
        mode=mode,
        family=t.family,
        preset_id=t.preset_id,
        alignment=t.alignment,
        direction=t.direction,
        intensity=t.intensity,
        cut_frame=t.cut_frame,
        start_frame=t.start_frame,
        end_frame=t.end_frame,
        timeline_start=t.timeline_start,
        timeline_end=t.timeline_end,
        duration_frames=t.duration_frames,
        from_clip=t.outgoing_item_id,
        to_clip=t.incoming_item_id,
        tail_frames=tail,
        head_frames=head,
        handles=HandleCheck(
            required_out=h.required_out,
            required_in=h.required_in,
            available_out=h.available_out,
            available_in=h.available_in,
            sufficient=h.sufficient,
        ),
        execution=Execution(t.execution),
        overlay_clip=t.overlay_item_id,
        mix_id=mix,
        wanted_kind=t.wanted_kind,
        adjusted_by_compiler=t.adjusted_by_compiler,
        supported=supported,
        reasons=t.reasons,
        provenance=Provenance.AI,
        origin_ref=t.transition_id,
        source_digest=source_digest,
    )


# --- tracks ---------------------------------------------------------------------------------------


def built_track(t: TrackFact) -> BuiltTrack:
    role = Role(t.role)
    name = f"{t.track_id} {t.label}"
    return BuiltTrack(
        track_id=t.track_id,
        kind=TrackKind(t.kind),
        role=role,
        index=t.index,
        lane=t.lane,
        name=name if t.lane <= 1 else f"{name} (lane {t.lane})",
        group=GROUP_OF_ROLE[role],
        blend=BlendMode(t.composite),
    )
