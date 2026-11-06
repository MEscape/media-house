"""Anti-corruption layer: the stored Layer 4 plans and the style, as the compiler's own facts.

This is the ONLY place that knows both shapes. The compiler's domain never sees a plan type of
another module, so a change in a plan's wording stays here. Nothing is computed: each function is
a field-by-field reading, and a field a plan does not state stays unstated.
"""

from media_house.modules.caption_engine.application.contracts import CaptionPlan
from media_house.modules.edit_plan_compiler.domain.facts import (
    BrandAssetFact,
    CaptionFact,
    CaptionsFacts,
    CrossfadeFact,
    DriftFact,
    DuckFact,
    EditRef,
    EffectFact,
    FontFact,
    GraphicFact,
    GraphicsFacts,
    MaskFact,
    MissingSoundFact,
    MusicSegmentFact,
    OriginalAudioFact,
    PlanStamp,
    SfxFact,
    SoundFacts,
    StyleFacts,
    TimeSegmentFact,
    TransitionFact,
    TransitionsFacts,
    TreatmentFact,
    VfxFacts,
    ZoneFact,
)
from media_house.modules.motion_graphics_engine.application.contracts import GraphicsPlan
from media_house.modules.music_and_sound_design.application.contracts import SoundDesignPlan
from media_house.modules.transition_engine.application.contracts import TransitionPlan
from media_house.modules.vfx_engine.application.contracts import VfxPlan
from media_house.modules.visual_style_engine.application.contracts import (
    ElementKind,
    FontFiles,
    PlacementUse,
    ResolvedStyle,
    StyleRef,
    StyleUsage,
)
from media_house.shared.errors import Err


def _edit(ref: object | None) -> EditRef | None:
    if ref is None:
        return None
    return EditRef(
        edit_plan_id=str(ref.edit_plan_id),  # type: ignore[attr-defined]
        plan_version=int(ref.plan_version),  # type: ignore[attr-defined]
    )


def _stamp(
    plan: GraphicsPlan | CaptionPlan | TransitionPlan | VfxPlan | SoundDesignPlan,
    document_id: str | None,
) -> PlanStamp:
    style = getattr(plan, "style", None)
    fingerprint = style.fingerprint if style is not None and not isinstance(style, str) else None
    return PlanStamp(
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        document_id=document_id,
        style_fingerprint=fingerprint,
        state=plan.state.value,
        edit=_edit(plan.edit),
    )


# --- style ---------------------------------------------------------------------------------------


def style_facts(
    style: ResolvedStyle,
    ref: StyleRef | None,
    fonts: FontFiles,
    drift: tuple[DriftFact, ...],
) -> StyleFacts:
    font_facts = []
    for face in sorted(style.faces, key=lambda f: f.key):
        found = fonts.locate(face)
        font_facts.append(
            FontFact(
                key=face.key,
                family=face.family,
                weight=face.weight,
                sha256=face.sha256,
                available=not isinstance(found, Err),
                detail=str(found.error.user_message) if isinstance(found, Err) else "",
            )
        )
    return StyleFacts(
        document_id=ref.document_id if ref else None,
        fingerprint=style.fingerprint,
        identity_id=style.identity_id,
        identity_version=style.identity_version,
        profile=style.profile,
        width=style.output.width,
        height=style.output.height,
        sponsored=style.sponsored,
        zones=tuple(
            ZoneFact(
                name=z.name,
                kind=z.kind.value,
                left=z.left,
                top=z.top,
                width=z.width,
                height=z.height,
            )
            for z in style.zones
        ),
        fonts=tuple(font_facts),
        brand_assets=tuple(
            BrandAssetFact(slot=b.slot, purpose=b.purpose, required=b.required, asset_id=b.asset_id)
            for b in style.brand_assets
        ),
        rules=tuple(
            sorted({r.path for r in style.token_rules} | {r.code for r in style.brand_rules})
        ),
        drift=drift,
        intensity_ceiling=style.tokens.motion.intensity_ceiling,
    )


#: A graphic kind -> the style element kind its zone must accept (``None``: not checked here).
_ELEMENT = {
    "lower_third": ElementKind.LOWER_THIRD,
    "title": ElementKind.TITLE,
    "section_title": ElementKind.TITLE,
    "callout": ElementKind.CALLOUT,
    "price_tag": ElementKind.PRICE_TAG,
    "rank_card": ElementKind.RANK_CARD,
    "product_sticker": ElementKind.STICKER,
    "cta": ElementKind.CTA,
    "sponsor_label": ElementKind.SPONSOR_LABEL,
    "logo_sting": ElementKind.LOGO,
    "caption": ElementKind.CAPTION,
}


def placement_usage(
    style: ResolvedStyle,
    graphics: GraphicsPlan | None,
    captions: CaptionPlan | None,
) -> StyleUsage:
    """What the plans placed, as the style's own drift validator wants it. The compiler never
    judges a zone or a token itself: the owner of the identity does."""
    placements: list[PlacementUse] = []
    if graphics is not None:
        for g in graphics.graphics:
            comp = g.composition
            kind = _ELEMENT.get(g.request.kind.value)
            if comp is None or kind is None or not comp.zone or comp.full_frame:
                continue
            placements.append(
                PlacementUse(
                    element_id=g.graphic_id,
                    kind=kind,
                    zone=comp.zone,
                    left=comp.rect.left,
                    top=comp.rect.top,
                    width=comp.rect.width,
                    height=comp.rect.height,
                )
            )
    if captions is not None:
        for s in captions.segments:
            if s.render.status.value in {"not_requested", "not_available", "failed"}:
                continue
            placements.append(
                PlacementUse(
                    element_id=s.segment_id,
                    kind=ElementKind.CAPTION,
                    zone=s.layout.zone,
                    left=s.layout.left,
                    top=s.layout.top,
                    width=s.layout.width,
                    height=s.layout.height,
                )
            )
    return StyleUsage(
        module="edit_plan_compiler",
        fingerprint=style.fingerprint,
        width=style.output.width,
        height=style.output.height,
        placements=tuple(placements),
    )


# --- graphics ------------------------------------------------------------------------------------


def graphics_facts(plan: GraphicsPlan, document_id: str | None = None) -> GraphicsFacts:
    found = []
    for g in plan.graphics:
        req, frames, render = g.request, g.frames, g.render
        start = (
            frames.timeline_start
            if frames and frames.timeline_start is not None
            else req.timing.timeline_start
        )
        found.append(
            GraphicFact(
                graphic_id=g.graphic_id,
                kind=req.kind.value,
                status=g.status.value,
                timeline_start=start,
                duration=frames.duration if frames else req.timing.local_duration,
                start_frame=frames.start_frame if frames else None,
                end_frame=frames.end_frame if frames else None,
                asset_id=render.asset_id if render else None,
                asset_start_frame=render.asset_start_frame if render else 0,
                has_alpha=render.alpha_min < 255 if render else True,
                width=render.width if render else None,
                height=render.height if render else None,
                zone=(
                    g.composition.zone
                    if g.composition and g.composition.zone
                    else req.placement.zone
                )
                or "",
                section_id=req.section_id,
                origin=req.origin.value,
                render_fingerprint=render.render_fingerprint if render else "",
                fingerprint=g.fingerprint,
                image_asset_ids=req.content.image_asset_ids,
                reason=req.reason,
            )
        )
    return GraphicsFacts(
        stamp=_stamp(plan, document_id),
        graphics=tuple(found),
        timeline_end=plan.timeline_end_with_bookends,
    )


# --- captions ------------------------------------------------------------------------------------


def captions_facts(plan: CaptionPlan, document_id: str | None = None) -> CaptionsFacts:
    segments = tuple(
        CaptionFact(
            segment_id=s.segment_id,
            clip_ids=s.clip_ids,
            section_id=s.section_id,
            text=s.text,
            timeline_start=s.timeline_start,
            timeline_end=s.timeline_end,
            start_frame=s.start_frame,
            end_frame=s.end_frame,
            render_status=s.render.status.value,
            asset_id=s.render.asset_id,
            asset_start_frame=s.render.asset_start_frame,
            zone=s.layout.zone,
            look=s.layout.look,
            word_count=len(s.words),
            emphasised=sum(w.emphasis.value != "normal" for w in s.words),
            fingerprint=s.segment_id,
        )
        for s in plan.segments
    )
    subtitles = plan.subtitles
    return CaptionsFacts(
        stamp=_stamp(plan, document_id),
        mode=plan.mode.value,
        segments=segments,
        language=subtitles.language if subtitles else None,
        srt=subtitles.srt if subtitles else "",
        vtt=subtitles.vtt if subtitles else "",
    )


# --- transitions ---------------------------------------------------------------------------------


def transitions_facts(plan: TransitionPlan, document_id: str | None = None) -> TransitionsFacts:
    found = []
    for t in plan.transitions:
        ov = t.overlay
        found.append(
            TransitionFact(
                transition_id=t.transition_id,
                boundary=t.boundary.value,
                outgoing_clip_id=t.outgoing_clip_id,
                incoming_clip_id=t.incoming_clip_id,
                kind=t.kind.value,
                family=t.family.value,
                preset_id=t.preset_id,
                alignment=t.alignment.value,
                direction=t.direction,
                intensity=t.intensity,
                status=t.status.value,
                cut_at=t.cut_at,
                cut_frame=t.cut_frame,
                timeline_start=t.timeline_start,
                timeline_end=t.timeline_end,
                start_frame=t.start_frame,
                end_frame=t.end_frame,
                duration_frames=t.duration_frames,
                required_out=t.handles.required_out,
                required_in=t.handles.required_in,
                available_out=t.handles.available_out,
                available_in=t.handles.available_in,
                fallback_wanted=t.fallback.wanted.value if t.fallback else None,
                fallback_used=t.fallback.used.value if t.fallback else None,
                fallback_reason=t.fallback.reason if t.fallback else "",
                overlay_asset_id=ov.asset_id if ov else None,
                overlay_state=ov.render_state.value if ov else "not_requested",
                overlay_start=ov.timeline_start if ov else None,
                overlay_duration=ov.local_duration if ov else None,
                audio_kind=t.audio.kind.value,
                audio_curve=t.audio.curve.value,
                audio_at=t.audio.timeline_at,
                audio_before=t.audio.before,
                audio_after=t.audio.after,
                audio_sample_rate=t.audio.sample_rate,
                reasons=t.reasons,
            )
        )
    return TransitionsFacts(stamp=_stamp(plan, document_id), transitions=tuple(found))


# --- vfx -----------------------------------------------------------------------------------------


def vfx_facts(plan: VfxPlan, document_id: str | None = None) -> VfxFacts:
    treatments = []
    for t in plan.treatments:
        effects = tuple(
            EffectFact(
                effect_id=e.effect_id,
                effect_type=e.effect_type.value,
                status=e.status.value,
                preset=e.preset,
                mode=e.mode,
                parameters=tuple((p.name, p.value) for p in e.parameters),
                intensity=e.intensity,
                timeline_start=e.timeline_start,
                timeline_end=e.timeline_end,
                start_frame=e.start_frame,
                end_frame=e.end_frame,
                backend=e.backend.value,
                fallback=e.fallback,
                masks=tuple(
                    MaskFact(
                        mask_id=m.mask_id,
                        kind=m.kind.value,
                        keyframes=tuple(
                            (k.source_time, k.box.x0, k.box.y0, k.box.x1, k.box.y1)
                            for k in m.keyframes
                        ),
                        feather=m.feather,
                        track_refs=m.track_refs,
                    )
                    for m in e.masks
                ),
            )
            for e in t.effects
        )
        tm = t.time_map
        treatments.append(
            TreatmentFact(
                treatment_id=t.treatment_id,
                clip_id=t.clip_id,
                status=t.status.value,
                effects=effects,
                segments=tuple(
                    TimeSegmentFact(
                        kind=s.kind.value,
                        timeline_start=s.timeline_start,
                        timeline_end=s.timeline_end,
                        source_start=s.source_start,
                        source_end=s.source_end,
                        rate=s.rate,
                        start_frame=s.start_frame,
                        end_frame=s.end_frame,
                        audio=s.audio.value,
                    )
                    for s in (tm.segments if tm else ())
                ),
                interpolation=tm.interpolation.value if tm else "",
                audio=t.audio.value,
                output_asset_id=t.output.asset_id if t.output else None,
                output_frames=t.output.frames if t.output else 0,
                render_fingerprint=t.render_fingerprint,
            )
        )
    return VfxFacts(stamp=_stamp(plan, document_id), treatments=tuple(treatments))


# --- sound design --------------------------------------------------------------------------------


def sound_facts(plan: SoundDesignPlan, document_id: str | None = None) -> SoundFacts:
    music = tuple(
        MusicSegmentFact(
            cue_id=cue.cue_id,
            segment_id=seg.segment_id,
            asset_id=cue.asset.asset_id,
            title=cue.asset.title,
            role=cue.role.value,
            timeline_start=seg.timeline_start,
            timeline_end=seg.timeline_end,
            source_start=seg.source_start,
            source_end=seg.source_end,
            gain_db=seg.gain_db,
            fade_in=seg.fade_in,
            fade_out=seg.fade_out,
            licence_state=cue.asset.licence_state.value,
        )
        for cue in plan.music
        if cue.asset is not None
        for seg in cue.segments
    )
    return SoundFacts(
        stamp=_stamp(plan, document_id),
        identity=plan.identity,
        identity_version=plan.identity_version,
        music=music,
        sfx=tuple(
            SfxFact(
                item_id=s.item_id,
                asset_id=s.asset.asset_id,
                title=s.asset.title,
                purpose=s.purpose,
                timeline_start=s.timeline_start,
                timeline_end=s.timeline_end,
                source_start=s.source_start,
                source_end=s.source_end,
                gain_db=s.gain_db,
                licence_state=s.asset.licence_state.value,
            )
            for s in plan.sfx
        ),
        ducking=tuple(
            DuckFact(
                duck_id=d.duck_id,
                cue_id=d.cue_id,
                key=d.key.value,
                timeline_start=d.timeline_start,
                timeline_end=d.timeline_end,
                depth_db=d.depth_db,
                attack_seconds=d.attack_seconds,
                release_seconds=d.release_seconds,
            )
            for d in plan.ducking
        ),
        crossfades=tuple(
            CrossfadeFact(
                crossfade_id=x.crossfade_id,
                kind=x.kind.value,
                timeline_at=x.timeline_at,
                length=x.length,
                outgoing_cue_id=x.outgoing_cue_id,
                incoming_cue_id=x.incoming_cue_id,
            )
            for x in plan.crossfades
        ),
        original_audio=tuple(
            OriginalAudioFact(
                clip_id=o.clip_id,
                asset_id=o.asset_id,
                timeline_start=o.timeline_start,
                timeline_end=o.timeline_end,
                mode=o.mode.value,
                music_over_it=o.music_over_it,
            )
            for o in plan.original_audio
        ),
        missing=tuple(
            MissingSoundFact(
                recommendation_id=m.recommendation_id,
                kind=m.kind.value,
                purpose=m.purpose,
                priority=m.priority.value,
                timeline_start=m.timeline_start,
            )
            for m in plan.missing_assets
        ),
        credits=plan.credit_lines,
        plan_hash=plan.plan_hash,
    )


def asset_ids_of(
    graphics: GraphicsFacts | None,
    captions: CaptionsFacts | None,
    transitions: TransitionsFacts | None,
    vfx: VfxFacts | None,
    sound: SoundFacts | None,
) -> list[str]:
    """Every library asset the Layer 4 plans place (the compiler verifies each one)."""
    ids: list[str | None] = []
    if graphics:
        ids += [g.asset_id for g in graphics.graphics]
    if captions:
        ids += [c.asset_id for c in captions.segments]
    if transitions:
        ids += [t.overlay_asset_id for t in transitions.transitions]
    if vfx:
        ids += [t.output_asset_id for t in vfx.treatments]
    if sound:
        ids += [m.asset_id for m in sound.music] + [s.asset_id for s in sound.sfx]
    return sorted({i for i in ids if i})


__all__ = [
    "asset_ids_of",
    "captions_facts",
    "graphics_facts",
    "placement_usage",
    "sound_facts",
    "style_facts",
    "transitions_facts",
    "vfx_facts",
]
