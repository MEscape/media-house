"""The compiler: plans in, one validated ``TimelineSpecification`` out. Pure and deterministic.

Order: compile units (per placement, then graphics / captions / soundtrack; an unchanged unit is
reused from the previous specification), plan tracks, resolve assets and dependencies, validate,
hash. The same inputs and config always give the same specification (``spec_hash``): nothing in
the hash comes from the clock or from a run.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Any

from media_house.modules.edit_plan_compiler.domain.digest import canon, digest
from media_house.modules.edit_plan_compiler.domain.facts import CompileInputs
from media_house.modules.edit_plan_compiler.domain.global_units import (
    captions_unit,
    graphics_unit,
    soundtrack_unit,
)
from media_house.modules.edit_plan_compiler.domain.model import (
    AssetUse,
    AudioAutomation,
    AudioItem,
    BrandAssetRef,
    ChangeSummary,
    ColorManagement,
    Compilation,
    CreatorIdentity,
    DependencyEntry,
    EditRef,
    EffectSpec,
    FontRef,
    InputRef,
    Issue,
    OverlayItem,
    SectionSpec,
    SubtitleSidecar,
    Timebase,
    TimelineSpecification,
    TransitionSpec,
    VideoItem,
    ZoneRef,
)
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.timing import fr, rat
from media_house.modules.edit_plan_compiler.domain.tracks import TrackPlanner, track_id
from media_house.modules.edit_plan_compiler.domain.units import Context, Unit, clip_unit
from media_house.modules.edit_plan_compiler.domain.validation import check_timeline
from media_house.modules.edit_plan_compiler.domain.values import (
    COMPILER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    AssetRelation,
    DependencyKind,
    DependencyState,
    IssueClass,
    ItemOrigin,
    ResultState,
    TrackRole,
)

LIMITATIONS: tuple[str, ...] = (
    "The media library has no project ownership: assets are checked for existence and file "
    "availability, not for membership of this project or of a shared library.",
    "A transition outside the hard-cut / dissolve / fade / dip family is a specification "
    "(kind, direction, intensity, duration, alignment): the timeline builder must support the "
    "vocabulary or report it. Graphic-family transitions are rendered overlays.",
    "Effects are executed by the VFX clip rendered by vfx_engine (baked); an effect without a "
    "usable render falls back to the plan's own fallback (original footage) and is reported.",
    "Frame-accurate cuts of variable-frame-rate recordings are exact in seconds; conforming the "
    "picture to a constant rate is the timeline builder's job.",
    "Levels, ducking and crossfades are instructions for the audio mix engine, not a mix.",
)

#: A missing result a pipeline task could produce: issue code -> (kind, owner, task to request).
_REQUESTS: dict[str, tuple[DependencyKind, str, str]] = {
    "graphic_not_rendered": (DependencyKind.RENDERED_GRAPHIC, "motion_graphics_engine", "graphics"),
    "caption_not_rendered": (DependencyKind.RENDERED_CAPTION, "caption_engine", "captions"),
    "vfx_not_rendered": (DependencyKind.RENDERED_CLIP, "vfx_engine", "vfx"),
    "framing_unresolved": (DependencyKind.DERIVATIVE, "reframing_intelligence", "reframing"),
}
_PLAN_OWNER = {
    "graphics": ("motion_graphics_engine", "graphics"),
    "captions": ("caption_engine", "captions"),
    "transitions": ("transition_engine", "transitions"),
    "vfx": ("vfx_engine", "vfx"),
    "sound_design": ("music_and_sound_design", "sound_design"),
    "visual_style": ("visual_style_engine", "visual_style"),
    "edit_plan": ("editing_intelligence", "edit_plan"),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class CompileMeta:
    compilation_version: int
    supersedes: str | None
    compiled_at: datetime
    inputs: tuple[InputRef, ...]
    sound_identity: str | None = None
    sound_identity_version: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Compiled:
    spec: TimelineSpecification
    #: CRITICAL findings (the specification is then never stored).
    critical: tuple[Issue, ...]


def cache_key(inputs: CompileInputs, config: CompilerConfig) -> dict[str, Any]:
    """Everything that can change the result, nothing that cannot (time, job, device)."""
    s = inputs.source
    return {
        "edit": None if s.edit is None else [s.edit.edit_plan_id, s.edit.plan_version],
        "source": digest(s),
        "style": inputs.style.fingerprint,
        "style_facts": digest(inputs.style),
        "graphics": digest(inputs.graphics),
        "captions": digest(inputs.captions),
        "transitions": digest(inputs.transitions),
        "vfx": digest(inputs.vfx),
        "sound": digest(inputs.sound),
        "assets": digest(dict(inputs.assets)),
        "reframing": digest(dict(inputs.reframing)),
        "undelivered": digest(inputs.undelivered),
        "config": digest(config.fingerprint()),
        "versions": {
            "compiler": COMPILER_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
        },
    }


def compile_timeline(
    inputs: CompileInputs,
    config: CompilerConfig,
    *,
    meta: CompileMeta,
    previous: TimelineSpecification | None = None,
) -> Compiled:
    config.check()
    source = inputs.source
    source.check()
    rate = config.timeline_frame_rate or source.frame_rate
    fps = fr(rate)
    ctx = Context(inputs, config, fps)
    reuse_from = previous if _comparable(previous, config, fps) else None

    units: list[Unit] = []
    for clip in sorted(
        source.clips, key=lambda c: (fr(c.timeline_start), not c.picture, c.layer, c.clip_id)
    ):
        units.append(clip_unit(ctx, clip, reuse_from))
    for build in (graphics_unit, captions_unit, soundtrack_unit):
        unit = build(ctx, reuse_from)
        if unit is not None:
            units.append(unit)

    video = tuple(v for u in units for v in u.video)
    overlays = tuple(o for u in units for o in u.overlays)
    audio = tuple(a for u in units for a in u.audio)
    effects = tuple(e for u in units for e in u.effects)
    transitions = tuple(t for u in units for t in u.transitions)
    automation = tuple(x for u in units for x in u.automation)
    issues = [i for u in units for i in u.issues]

    overlays, dropped = _drop_violations(inputs, config, overlays)
    issues += dropped

    duration = _duration(inputs, video, fps)
    timebase = Timebase(
        frame_rate=rate,
        frame_rate_assumed=source.frame_rate_assumed
        if config.timeline_frame_rate is None
        else False,
        sample_rate=config.sample_rate,
        width=inputs.style.width,
        height=inputs.style.height,
        duration=rat(duration),
        duration_frames=round(duration * fps),
        color=ColorManagement.of(config.color_profile),
    )
    issues += check_timeline(inputs, config, timebase, video, overlays, audio)
    issues = _unique(issues)

    video, overlays, audio, tracks = _place(video, overlays, audio)
    # A transition's neighbours are only known once every unit is built: link them first, so an
    # item's fingerprint is the same whether it was just built or reused.
    video = _link_transitions(video, transitions)
    video, overlays, audio, effects, transitions, automation = _fingerprinted(
        video, overlays, audio, effects, transitions, automation
    )

    assets = _assets(inputs, video, overlays, audio)
    dependencies = _dependencies(inputs, assets, issues)
    critical = tuple(i for i in issues if i.klass is IssueClass.CRITICAL)
    kept = tuple(i for i in issues if i.klass is not IssueClass.CRITICAL)
    key = cache_key(inputs, config)
    change = _change(previous, video, overlays, audio, effects, transitions, automation)
    compilation = Compilation(
        compilation_version=meta.compilation_version,
        supersedes=meta.supersedes,
        compiler_version=COMPILER_VERSION,
        vocabulary_version=VOCABULARY_VERSION,
        config_hash=digest(config.fingerprint()),
        dependency_hash=digest([canon(d) for d in dependencies]),
        input_hash=digest(key),
        spec_hash="",
        compiled_at=meta.compiled_at,
        units_total=len(units),
        units_reused=sum(u.reused for u in units),
        unit_digests=tuple(sorted((u.unit_id, u.digest) for u in units)),
        change=change,
    )
    spec = TimelineSpecification(
        timeline_id=_timeline_id(inputs),
        project_id=source.project_id,
        edit=None
        if source.edit is None
        else EditRef(edit_plan_id=source.edit.edit_plan_id, plan_version=source.edit.plan_version),
        anchor_asset_id=source.anchor_asset_id,
        creator=_creator(inputs, meta, video, overlays, transitions),
        timebase=timebase,
        tracks=tracks,
        assets=assets,
        sections=_sections(inputs, fps),
        video=video,
        overlays=overlays,
        effects=effects,
        transitions=transitions,
        audio=audio,
        automation=automation,
        subtitles=_subtitles(inputs),
        credits=inputs.sound.credits if inputs.sound else "",
        dependencies=dependencies,
        issues=kept,
        inputs=meta.inputs,
        compilation=compilation,
        state=_state(kept),
        limitations=LIMITATIONS,
    )
    return Compiled(spec=_with_hash(spec), critical=critical)


# --- steps ---------------------------------------------------------------------------------------


def _comparable(
    previous: TimelineSpecification | None, config: CompilerConfig, fps: Fraction
) -> TimelineSpecification | None:
    """A previous specification's units are reusable only for the same compiler, config, rate."""
    if previous is None:
        return None
    c = previous.compilation
    same = (
        c.compiler_version == COMPILER_VERSION
        and c.vocabulary_version == VOCABULARY_VERSION
        and previous.schema_version == SCHEMA_VERSION
        and c.config_hash == digest(config.fingerprint())
        and fr(previous.timebase.frame_rate) == fps
    )
    return previous if same else None


def _duration(inputs: CompileInputs, video: tuple[VideoItem, ...], fps: Fraction) -> Fraction:
    ends = [fr(v.timeline_end) for v in video]
    if inputs.graphics and inputs.graphics.timeline_end is not None:
        ends.append(fr(inputs.graphics.timeline_end))
    if not ends:
        return fr(inputs.source.duration)
    return max(ends)


def _drop_violations(
    inputs: CompileInputs, config: CompilerConfig, overlays: tuple[OverlayItem, ...]
) -> tuple[tuple[OverlayItem, ...], list[Issue]]:
    """With ``drop_identity_violations`` an overlay that broke a locked channel rule is left
    out, and the omission is reported. Without it nothing is removed (a conflict is raised)."""
    if not config.drop_identity_violations:
        return overlays, []
    bad = {
        d.item_id for d in inputs.style.drift if d.severity in {"error", "critical"} and d.item_id
    }
    kept = tuple(o for o in overlays if o.source_ref not in bad and o.item_id not in bad)
    dropped = [
        Issue(
            code="omitted_for_identity",
            klass=IssueClass.AUTO_RESOLVED,
            message=f"{o.kind} {o.source_ref} broke a locked channel rule",
            item_id=o.item_id,
            timeline_start=o.timeline_start,
            timeline_end=o.timeline_end,
            resolution="left out because drop_identity_violations is on",
            unit_id=o.unit_id,
        )
        for o in overlays
        if o.source_ref in bad or o.item_id in bad
    ]
    return kept, dropped


def _unique(issues: list[Issue]) -> list[Issue]:
    seen: dict[tuple[str, str | None, str], Issue] = {}
    for i in issues:
        seen.setdefault((i.code, i.item_id, i.message), i)
    return sorted(
        seen.values(),
        key=lambda i: (
            0 if i.timeline_start is None else 1,
            fr(i.timeline_start) if i.timeline_start else Fraction(0),
            i.code,
            i.item_id or "",
        ),
    )


def _place(
    video: tuple[VideoItem, ...],
    overlays: tuple[OverlayItem, ...],
    audio: tuple[AudioItem, ...],
) -> tuple[tuple[VideoItem, ...], tuple[OverlayItem, ...], tuple[AudioItem, ...], tuple[Any, ...]]:
    """Assign every item a track: the fixed layout, a new lane only where items overlap."""
    planner = TrackPlanner()
    lanes: dict[str, tuple[TrackRole, int]] = {}

    def role_of_video(v: VideoItem) -> TrackRole:
        return TrackRole.PRIMARY if v.layer == 0 else TrackRole.COVER

    def role_of_overlay(o: OverlayItem) -> TrackRole:
        return {
            ItemOrigin.GRAPHICS: TrackRole.GRAPHICS,
            ItemOrigin.CAPTIONS: TrackRole.CAPTIONS,
        }.get(o.origin, TrackRole.EFFECTS)

    entries: list[tuple[TrackRole, Fraction, Fraction, str]] = []
    entries += [
        (role_of_video(v), fr(v.timeline_start), fr(v.timeline_end), v.item_id) for v in video
    ]
    entries += [
        (role_of_overlay(o), fr(o.timeline_start), fr(o.timeline_end), o.item_id) for o in overlays
    ]
    entries += [(a.role, fr(a.timeline_start), fr(a.timeline_end), a.item_id) for a in audio]
    for role, start, end, item_id in sorted(entries, key=lambda e: (e[1], e[2], e[3])):
        lanes[item_id] = (role, planner.lane_for(role, start, end))
    tracks = planner.tracks()

    def tid(item_id: str) -> str:
        role, lane = lanes[item_id]
        return track_id(tracks, role, lane)

    return (
        tuple(replace(v, track_id=tid(v.item_id)) for v in video),
        tuple(replace(o, track_id=tid(o.item_id)) for o in overlays),
        tuple(replace(a, track_id=tid(a.item_id)) for a in audio),
        tracks,
    )


def _fingerprinted(
    video: tuple[VideoItem, ...],
    overlays: tuple[OverlayItem, ...],
    audio: tuple[AudioItem, ...],
    effects: tuple[EffectSpec, ...],
    transitions: tuple[TransitionSpec, ...],
    automation: tuple[AudioAutomation, ...],
) -> tuple[Any, ...]:
    def order[T](items: tuple[T, ...], key: Any) -> tuple[T, ...]:
        return tuple(sorted(items, key=key))

    return (
        order(
            tuple(replace(v, fingerprint=digest(replace(v, fingerprint=""))) for v in video),
            lambda v: (v.start_frame, v.layer, v.item_id),
        ),
        order(
            tuple(replace(o, fingerprint=digest(replace(o, fingerprint=""))) for o in overlays),
            lambda o: (o.start_frame, o.item_id),
        ),
        order(
            tuple(replace(a, fingerprint=digest(replace(a, fingerprint=""))) for a in audio),
            lambda a: (fr(a.timeline_start), a.item_id),
        ),
        order(effects, lambda e: (e.start_frame, e.effect_id)),
        order(transitions, lambda t: (t.cut_frame, t.transition_id)),
        order(automation, lambda x: (fr(x.timeline_start), x.automation_id)),
    )


def _link_transitions(
    video: tuple[VideoItem, ...], transitions: tuple[TransitionSpec, ...]
) -> tuple[VideoItem, ...]:
    into = {t.incoming_item_id: t.transition_id for t in transitions if t.incoming_item_id}
    out = {t.outgoing_item_id: t.transition_id for t in transitions if t.outgoing_item_id}
    return tuple(
        replace(v, transition_in=into.get(v.item_id), transition_out=out.get(v.item_id))
        for v in video
    )


def _assets(
    inputs: CompileInputs,
    video: tuple[VideoItem, ...],
    overlays: tuple[OverlayItem, ...],
    audio: tuple[AudioItem, ...],
) -> tuple[AssetUse, ...]:
    used: dict[str, list[str]] = {}
    relation: dict[str, AssetRelation] = {}

    def use(asset_id: str | None, item_id: str, rel: AssetRelation | None) -> None:
        if asset_id is None:
            return
        used.setdefault(asset_id, []).append(item_id)
        if rel is not None:
            relation.setdefault(asset_id, rel)

    for v in video:
        use(v.asset_id, v.item_id, AssetRelation.RENDERED if v.origin is ItemOrigin.VFX else None)
    for o in overlays:
        use(o.asset_id, o.item_id, AssetRelation.RENDERED)
    for a in audio:
        use(a.asset_id, a.item_id, AssetRelation.LIBRARY if a.origin is ItemOrigin.SOUND else None)
    found: list[AssetUse] = []
    for asset_id in sorted(used):
        facts = inputs.assets.get(asset_id)
        rel = relation.get(asset_id) or _relation_of(facts.operation if facts else None)
        found.append(
            AssetUse(
                asset_id=asset_id,
                original_asset_id=(
                    facts.original_asset_id if facts and facts.original_asset_id else asset_id
                ),
                relation=rel,
                media_type=facts.media_type if facts else "",
                duration=facts.duration if facts else None,
                frame_rate=facts.frame_rate if facts else None,
                frame_rate_variable=bool(facts and facts.frame_rate_variable),
                sample_rate=facts.sample_rate if facts else None,
                channels=facts.channels if facts else None,
                width=facts.width if facts else None,
                height=facts.height if facts else None,
                checksum=facts.checksum if facts else None,
                available=bool(facts and facts.exists and facts.file_available),
                used_by=tuple(sorted(set(used[asset_id]))),
                color=facts.color if facts else None,
            )
        )
    return tuple(found)


def _relation_of(operation: str | None) -> AssetRelation:
    return {
        "audio_improvement": AssetRelation.IMPROVED_AUDIO,
        "video_improvement": AssetRelation.IMPROVED_VIDEO,
        "proxy": AssetRelation.PROXY,
    }.get(operation or "", AssetRelation.ORIGINAL)


_KIND_OF_RELATION = {
    AssetRelation.ORIGINAL: DependencyKind.SOURCE_MEDIA,
    AssetRelation.IMPROVED_AUDIO: DependencyKind.DERIVATIVE,
    AssetRelation.IMPROVED_VIDEO: DependencyKind.DERIVATIVE,
    AssetRelation.PROXY: DependencyKind.DERIVATIVE,
    AssetRelation.RENDERED: DependencyKind.RENDERED_GRAPHIC,
    AssetRelation.LIBRARY: DependencyKind.SOUND,
}


def _dependencies(
    inputs: CompileInputs, assets: tuple[AssetUse, ...], issues: list[Issue]
) -> tuple[DependencyEntry, ...]:
    found: dict[str, DependencyEntry] = {}

    def add(entry: DependencyEntry) -> None:
        found.setdefault(entry.dependency_id, entry)

    for a in assets:
        add(
            DependencyEntry(
                dependency_id=f"{a.relation.value}:{a.asset_id}",
                kind=_KIND_OF_RELATION[a.relation],
                reference=a.asset_id,
                state=DependencyState.RESOLVED if a.available else DependencyState.MISSING,
                required_by=a.used_by,
                detail="" if a.available else "the asset or its file is not available",
            )
        )
    for i in issues:
        if i.code in _REQUESTS:
            kind, owner, task = _REQUESTS[i.code]
            ref = i.item_id or i.code
            add(
                DependencyEntry(
                    dependency_id=f"{kind.value}:{ref}",
                    kind=kind,
                    reference=ref,
                    state=DependencyState.REQUESTED,
                    required_by=(ref,),
                    detail=i.message,
                    owner=owner,
                    request=task,
                )
            )
        elif i.code == "sound_missing":
            ref = i.item_id or i.code
            add(
                DependencyEntry(
                    dependency_id=f"sound:{ref}",
                    kind=DependencyKind.SOUND,
                    reference=ref,
                    state=DependencyState.MISSING,
                    required_by=(ref,),
                    detail=i.message,
                    owner="music_and_sound_design",
                    request="manual_asset_acquisition",
                )
            )
    for kind_name, why in inputs.undelivered:
        owner, task = _PLAN_OWNER.get(kind_name, ("", kind_name))
        add(
            DependencyEntry(
                dependency_id=f"plan:{kind_name}",
                kind=DependencyKind.IDENTITY
                if kind_name == "visual_style"
                else DependencyKind.RENDERED_OVERLAY,
                reference=kind_name,
                state=DependencyState.REQUESTED,
                detail=why,
                owner=owner,
                request=task,
            )
        )
    for font in inputs.style.fonts:
        add(
            DependencyEntry(
                dependency_id=f"font:{font.key}",
                kind=DependencyKind.FONT,
                reference=font.key,
                state=DependencyState.RESOLVED if font.available else DependencyState.MISSING,
                detail=font.detail,
            )
        )
    for brand in inputs.style.brand_assets:
        add(
            DependencyEntry(
                dependency_id=f"brand_asset:{brand.slot}",
                kind=DependencyKind.BRAND_ASSET,
                reference=brand.asset_id or brand.slot,
                state=DependencyState.RESOLVED
                if brand.asset_id
                else (DependencyState.MISSING if brand.required else DependencyState.FALLBACK),
                detail=brand.purpose,
            )
        )
    add(
        DependencyEntry(
            dependency_id=f"identity:{inputs.style.identity_id}:{inputs.style.identity_version}",
            kind=DependencyKind.IDENTITY,
            reference=inputs.style.fingerprint,
            state=DependencyState.RESOLVED,
            detail=f"profile {inputs.style.profile}",
        )
    )
    return tuple(sorted(found.values(), key=lambda d: d.dependency_id))


def _creator(
    inputs: CompileInputs,
    meta: CompileMeta,
    video: tuple[VideoItem, ...],
    overlays: tuple[OverlayItem, ...],
    transitions: tuple[TransitionSpec, ...],
) -> CreatorIdentity:
    s = inputs.style
    presets = {("graphic_kind", o.kind) for o in overlays if o.origin is ItemOrigin.GRAPHICS}
    presets |= {("transition_preset", t.preset_id) for t in transitions if t.preset_id}
    presets |= {
        ("caption_look", o.detail) for o in overlays if o.origin is ItemOrigin.CAPTIONS and o.detail
    }
    return CreatorIdentity(
        identity_id=s.identity_id,
        identity_version=s.identity_version,
        style_document_id=s.document_id,
        style_fingerprint=s.fingerprint,
        format_profile=s.profile,
        output_width=s.width,
        output_height=s.height,
        sponsored=s.sponsored,
        sound_identity=meta.sound_identity,
        sound_identity_version=meta.sound_identity_version,
        rules=s.rules,
        fonts=tuple(
            FontRef(
                key=f.key, family=f.family, weight=f.weight, sha256=f.sha256, available=f.available
            )
            for f in s.fonts
        ),
        zones=tuple(
            ZoneRef(
                name=z.name, kind=z.kind, left=z.left, top=z.top, width=z.width, height=z.height
            )
            for z in s.zones
        ),
        brand_assets=tuple(
            BrandAssetRef(slot=b.slot, required=b.required, asset_id=b.asset_id)
            for b in s.brand_assets
        ),
        presets=tuple(sorted(presets)),
    )


def _sections(inputs: CompileInputs, fps: Fraction) -> tuple[SectionSpec, ...]:
    return tuple(
        SectionSpec(
            section_id=s.section_id,
            order=s.order,
            label=s.label,
            timeline_start=s.timeline_start,
            timeline_end=s.timeline_end,
            start_frame=round(fr(s.timeline_start) * fps),
            end_frame=round(fr(s.timeline_end) * fps),
        )
        for s in sorted(inputs.source.sections, key=lambda s: (s.order, s.section_id))
    )


def _subtitles(inputs: CompileInputs) -> SubtitleSidecar | None:
    c = inputs.captions
    if c is None or not (c.srt or c.vtt):
        return None
    return SubtitleSidecar(
        language=c.language, srt=c.srt, vtt=c.vtt, source_plan_id=c.stamp.plan_id
    )


def _timeline_id(inputs: CompileInputs) -> str:
    s = inputs.source
    return (
        "tl:"
        + digest((s.project_id, s.anchor_asset_id, s.edit.edit_plan_id if s.edit else None))[:16]
    )


def _state(issues: tuple[Issue, ...]) -> ResultState:
    bad = any(i.klass in {IssueClass.WARNING, IssueClass.CREATIVE_CONFLICT} for i in issues)
    return ResultState.PARTIAL if bad else ResultState.OK


def _change(
    previous: TimelineSpecification | None,
    video: tuple[VideoItem, ...],
    overlays: tuple[OverlayItem, ...],
    audio: tuple[AudioItem, ...],
    effects: tuple[EffectSpec, ...],
    transitions: tuple[TransitionSpec, ...],
    automation: tuple[AudioAutomation, ...],
) -> ChangeSummary:
    def prints(
        v: tuple[VideoItem, ...],
        o: tuple[OverlayItem, ...],
        a: tuple[AudioItem, ...],
        e: tuple[EffectSpec, ...],
        t: tuple[TransitionSpec, ...],
        x: tuple[AudioAutomation, ...],
    ) -> Mapping[str, str]:
        found = {i.item_id: i.fingerprint for i in v}
        found.update({i.item_id: i.fingerprint for i in o})
        found.update({i.item_id: i.fingerprint for i in a})
        found.update({i.effect_id: digest(i) for i in e})
        found.update({i.transition_id: digest(i) for i in t})
        found.update({i.automation_id: digest(i) for i in x})
        return found

    now = prints(video, overlays, audio, effects, transitions, automation)
    if previous is None:
        return ChangeSummary(added=tuple(sorted(now)))
    before = prints(
        previous.video,
        previous.overlays,
        previous.audio,
        previous.effects,
        previous.transitions,
        previous.automation,
    )
    return ChangeSummary(
        added=tuple(sorted(set(now) - set(before))),
        removed=tuple(sorted(set(before) - set(now))),
        changed=tuple(sorted(i for i in set(now) & set(before) if now[i] != before[i])),
        unchanged=sum(1 for i in set(now) & set(before) if now[i] == before[i]),
    )


def _with_hash(spec: TimelineSpecification) -> TimelineSpecification:
    """``spec_hash`` covers the content only: no time stamp, version number or change summary."""
    content = {
        f: canon(getattr(spec, f))
        for f in (
            "timeline_id", "project_id", "edit", "anchor_asset_id", "creator", "timebase",
            "tracks", "assets", "sections", "video", "overlays", "effects", "transitions",
            "audio", "automation", "subtitles", "credits", "dependencies", "issues", "state",
        )
    }  # fmt: skip
    return replace(spec, compilation=replace(spec.compilation, spec_hash=digest(content)))
