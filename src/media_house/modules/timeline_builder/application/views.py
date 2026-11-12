"""The anti-corruption mapping from the compiler's ``TimelineSpecification`` to the builder's own
``SpecFacts``. The only place that knows both; the builder's domain never sees the compiler's types.
"""

from media_house.modules.edit_plan_compiler.application.contracts import (
    AssetUse,
    AudioAutomation,
    AudioItem,
    CreatorIdentity,
    DependencyEntry,
    EffectSpec,
    Issue,
    OverlayItem,
    SectionSpec,
    SubtitleSidecar,
    Timebase,
    TimelineSpecification,
    Track,
    TransitionSpec,
    VideoItem,
)
from media_house.modules.timeline_builder.domain import facts as f


def spec_facts(spec: TimelineSpecification) -> f.SpecFacts:
    return f.SpecFacts(
        timeline_id=spec.timeline_id,
        project_id=spec.project_id,
        edit=None
        if spec.edit is None
        else f.EditFact(edit_plan_id=spec.edit.edit_plan_id, plan_version=spec.edit.plan_version),
        anchor_asset_id=spec.anchor_asset_id,
        schema_version=spec.schema_version,
        state=spec.state.value,
        creator=_identity(spec.creator),
        timebase=_timebase(spec.timebase),
        tracks=tuple(_track(t) for t in spec.tracks),
        assets=tuple(_asset(a) for a in spec.assets),
        sections=tuple(_section(s) for s in spec.sections),
        video=tuple(_video(v) for v in spec.video),
        overlays=tuple(_overlay(o) for o in spec.overlays),
        effects=tuple(_effect(e) for e in spec.effects),
        transitions=tuple(_transition(t) for t in spec.transitions),
        audio=tuple(_audio(a) for a in spec.audio),
        automation=tuple(_automation(a) for a in spec.automation),
        subtitles=_subtitles(spec.subtitles),
        credits=spec.credits,
        dependencies=tuple(_dependency(d) for d in spec.dependencies),
        issues=tuple(_issue(i) for i in spec.issues),
        compilation=f.CompilationFact(
            compilation_version=spec.compilation.compilation_version,
            compiler_version=spec.compilation.compiler_version,
            vocabulary_version=spec.compilation.vocabulary_version,
            config_hash=spec.compilation.config_hash,
            spec_hash=spec.compilation.spec_hash,
        ),
        limitations=spec.limitations,
    )


def asset_ids(spec: TimelineSpecification) -> tuple[str, ...]:
    """Every file the specification points at, plus the recordings it traces to."""
    ids = {a.asset_id for a in spec.assets}
    ids |= {a.original_asset_id for a in spec.assets}
    return tuple(sorted(ids))


def _identity(c: CreatorIdentity) -> f.IdentityFact:
    return f.IdentityFact(
        identity_id=c.identity_id,
        identity_version=c.identity_version,
        style_document_id=c.style_document_id,
        style_fingerprint=c.style_fingerprint,
        format_profile=c.format_profile,
        output_width=c.output_width,
        output_height=c.output_height,
        sponsored=c.sponsored,
        sound_identity=c.sound_identity,
        sound_identity_version=c.sound_identity_version,
        rules=c.rules,
        fonts=tuple(
            f.FontFact(
                key=x.key, family=x.family, weight=x.weight, sha256=x.sha256, available=x.available
            )
            for x in c.fonts
        ),
        zones=tuple(
            f.ZoneFact(
                name=z.name, kind=z.kind, left=z.left, top=z.top, width=z.width, height=z.height
            )
            for z in c.zones
        ),
        brand_assets=tuple(
            f.BrandAssetFact(slot=b.slot, required=b.required, asset_id=b.asset_id)
            for b in c.brand_assets
        ),
        presets=c.presets,
    )


def _timebase(t: Timebase) -> f.TimebaseFact:
    return f.TimebaseFact(
        frame_rate=t.frame_rate,
        frame_rate_assumed=t.frame_rate_assumed,
        sample_rate=t.sample_rate,
        width=t.width,
        height=t.height,
        duration=t.duration,
        duration_frames=t.duration_frames,
        color=f.ColorManagementFact(
            profile=t.color.profile,
            space=t.color.space,
            primaries=t.color.primaries,
            transfer=t.color.transfer,
            range=t.color.range,
        ),
    )


def _track(t: Track) -> f.TrackFact:
    return f.TrackFact(
        track_id=t.track_id,
        kind=t.kind.value,
        role=t.role.value,
        index=t.index,
        lane=t.lane,
        composite=t.composite.value,
        label=t.label,
    )


def _asset(a: AssetUse) -> f.AssetFact:
    return f.AssetFact(
        asset_id=a.asset_id,
        original_asset_id=a.original_asset_id,
        relation=a.relation.value,
        media_type=a.media_type,
        duration=a.duration,
        frame_rate=a.frame_rate,
        frame_rate_variable=a.frame_rate_variable,
        sample_rate=a.sample_rate,
        channels=a.channels,
        width=a.width,
        height=a.height,
        checksum=a.checksum,
        available=a.available,
        used_by=a.used_by,
        color=None
        if a.color is None
        else f.ColorFact(
            space=a.color.space,
            primaries=a.color.primaries,
            transfer=a.color.transfer,
            range=a.color.range,
        ),
    )


def _section(s: SectionSpec) -> f.SectionFact:
    return f.SectionFact(
        section_id=s.section_id,
        order=s.order,
        label=s.label,
        timeline_start=s.timeline_start,
        timeline_end=s.timeline_end,
        start_frame=s.start_frame,
        end_frame=s.end_frame,
    )


def _video(v: VideoItem) -> f.VideoFact:
    return f.VideoFact(
        item_id=v.item_id,
        clip_id=v.clip_id,
        track_id=v.track_id,
        origin=v.origin.value,
        asset_id=v.asset_id,
        original_asset_id=v.original_asset_id,
        source_start=v.source_start,
        source_end=v.source_end,
        source_start_frame=v.source_start_frame,
        timeline_start=v.timeline_start,
        timeline_end=v.timeline_end,
        start_frame=v.start_frame,
        end_frame=v.end_frame,
        speed=v.speed,
        layer=v.layer,
        section_id=v.section_id,
        beat_id=v.beat_id,
        retime=None
        if v.retime is None
        else f.RetimeFact(
            segments=tuple(
                f.RetimeSegmentFact(
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
            baked=v.retime.baked,
            audio=v.retime.audio,
        ),
        reframe=tuple(
            f.ReframeFact(
                target=r.target,
                recommendation_id=r.recommendation_id,
                analysis_id=r.analysis_id,
                strategy=r.strategy,
                keys=tuple(
                    f.CropKeyFact(
                        source_time=k.source_time,
                        x=k.x,
                        y=k.y,
                        w=k.w,
                        h=k.h,
                        interpolation=k.interpolation,
                    )
                    for k in r.keys
                ),
            )
            for r in v.reframe
        ),
        blur=tuple(
            f.BlurKeyFact(source_time=k.source_time, size=k.size, interpolation=k.interpolation)
            for k in v.blur
        ),
        effect_ids=v.effect_ids,
        transition_in=v.transition_in,
        transition_out=v.transition_out,
        source_frame_rate=v.source_frame_rate,
        source_variable_rate=v.source_variable_rate,
        original_start=v.original_start,
        original_end=v.original_end,
    )


def _overlay(o: OverlayItem) -> f.OverlayFact:
    return f.OverlayFact(
        item_id=o.item_id,
        origin=o.origin.value,
        source_ref=o.source_ref,
        kind=o.kind,
        track_id=o.track_id,
        asset_id=o.asset_id,
        asset_start_frame=o.asset_start_frame,
        timeline_start=o.timeline_start,
        timeline_end=o.timeline_end,
        start_frame=o.start_frame,
        end_frame=o.end_frame,
        composite=o.composite.value,
        execution=o.execution.value,
        zone=o.zone,
        section_id=o.section_id,
        detail=o.detail,
    )


def _effect(e: EffectSpec) -> f.EffectFact:
    return f.EffectFact(
        effect_id=e.effect_id,
        effect_type=e.effect_type,
        clip_id=e.clip_id,
        item_id=e.item_id,
        preset=e.preset,
        mode=e.mode,
        parameters=e.parameters,
        intensity=e.intensity,
        timeline_start=e.timeline_start,
        timeline_end=e.timeline_end,
        start_frame=e.start_frame,
        end_frame=e.end_frame,
        execution=e.execution.value,
        fallback=e.fallback,
        masks=tuple(
            f.MaskFact(
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


def _transition(t: TransitionSpec) -> f.TransitionFact:
    return f.TransitionFact(
        transition_id=t.transition_id,
        boundary=t.boundary,
        kind=t.kind,
        family=t.family,
        preset_id=t.preset_id,
        alignment=t.alignment,
        direction=t.direction,
        intensity=t.intensity,
        cut_at=t.cut_at,
        cut_frame=t.cut_frame,
        timeline_start=t.timeline_start,
        timeline_end=t.timeline_end,
        start_frame=t.start_frame,
        end_frame=t.end_frame,
        duration_frames=t.duration_frames,
        outgoing_item_id=t.outgoing_item_id,
        incoming_item_id=t.incoming_item_id,
        handles=f.HandlesFact(
            required_out=t.handles.required_out,
            required_in=t.handles.required_in,
            available_out=t.handles.available_out,
            available_in=t.handles.available_in,
            sufficient=t.handles.sufficient,
        ),
        execution=t.execution.value,
        overlay_item_id=t.overlay_item_id,
        wanted_kind=t.wanted_kind,
        adjusted_by_compiler=t.adjusted_by_compiler,
        reasons=t.reasons,
    )


def _audio(a: AudioItem) -> f.AudioFact:
    return f.AudioFact(
        item_id=a.item_id,
        origin=a.origin.value,
        role=a.role.value,
        track_id=a.track_id,
        source_ref=a.source_ref,
        asset_id=a.asset_id,
        original_asset_id=a.original_asset_id,
        source_start=a.source_start,
        source_end=a.source_end,
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
        linked_item_id=a.linked_item_id,
        note=a.note,
    )


def _automation(a: AudioAutomation) -> f.AutomationFact:
    return f.AutomationFact(
        automation_id=a.automation_id,
        kind=a.kind,
        timeline_start=a.timeline_start,
        timeline_end=a.timeline_end,
        target_item_ids=a.target_item_ids,
        key=a.key,
        depth_db=a.depth_db,
        attack_seconds=a.attack_seconds,
        release_seconds=a.release_seconds,
        curve=a.curve,
        timeline_at=a.timeline_at,
        before=a.before,
        after=a.after,
        sample_rate=a.sample_rate,
        source_ref=a.source_ref,
    )


def _subtitles(s: SubtitleSidecar | None) -> f.SubtitleFact | None:
    if s is None:
        return None
    return f.SubtitleFact(
        language=s.language, srt=s.srt, vtt=s.vtt, source_plan_id=s.source_plan_id
    )


def _dependency(d: DependencyEntry) -> f.DependencyFact:
    return f.DependencyFact(
        dependency_id=d.dependency_id,
        kind=d.kind.value,
        reference=d.reference,
        state=d.state.value,
        required_by=d.required_by,
        detail=d.detail,
        owner=d.owner,
        request=d.request,
    )


def _issue(i: Issue) -> f.IssueFact:
    return f.IssueFact(
        code=i.code,
        klass=i.klass.value,
        message=i.message,
        item_id=i.item_id,
        timeline_start=i.timeline_start,
        timeline_end=i.timeline_end,
        evidence=i.evidence,
        resolution=i.resolution,
    )
