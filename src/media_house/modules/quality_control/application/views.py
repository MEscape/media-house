"""The one place that knows both the other modules' models and quality control's own facts.

The domain imports no other module, so a built timeline, a mix result, a continuity analysis, a
story plan and a Resolve binding are read here into the facts the checks work on.
"""

from fractions import Fraction

from media_house.modules.audio_mix_engine.application.contracts import DELIVERIES, MixResult
from media_house.modules.continuity_intelligence.application.contracts import ContinuityAnalysis
from media_house.modules.edit_plan_compiler.application.contracts import TimelineSpecification
from media_house.modules.quality_control.domain import facts as f
from media_house.modules.resolve_adapter.application.contracts import SyncState
from media_house.modules.story_architecture.application.contracts import StoryPlan
from media_house.modules.timeline_builder.application.contracts import (
    AudioClip,
    BuiltTimeline,
    VideoClip,
)


def _fraction(value: object) -> Fraction | None:
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if numerator is None or denominator is None:
        return None
    return Fraction(int(numerator), int(denominator))


def _seconds(value: object) -> Fraction:
    return _fraction(value) or Fraction(0)


def _video(clip: VideoClip, role: str) -> f.ClipFacts:
    source = clip.source
    original_in = _fraction(source.original_in) if source and source.original_in else None
    original_out = _fraction(source.original_out) if source and source.original_out else None
    if source and original_in is None:
        original_in, original_out = _fraction(source.source_in), _fraction(source.source_out)
    freezes = tuple(
        (seg.start_frame, seg.end_frame)
        for seg in (clip.time_map.segments if clip.time_map else ())
        if seg.rate.numerator == 0
    )
    transform = clip.transform
    return f.ClipFacts(
        clip_id=clip.clip_id,
        track_id=clip.track_id,
        role=role,
        kind=clip.kind.value,
        name=clip.name,
        start_frame=clip.start_frame,
        end_frame=clip.end_frame,
        source_id=source.source_id if source else None,
        original_asset_id=source.original_asset_id if source else None,
        original_in=original_in,
        original_out=original_out,
        speed=float(_fraction(clip.speed) or 1),
        section_id=clip.section_id,
        beat_id=clip.beat_id,
        unresolved=clip.state.value == "unresolved",
        enabled=clip.enabled,
        human=clip.provenance.value.startswith("human"),
        zone=clip.zone,
        opacity=clip.opacity,
        scale_x=transform.scale_x if transform else 1.0,
        scale_y=transform.scale_y if transform else 1.0,
        position_x=transform.position_x if transform else 0.0,
        position_y=transform.position_y if transform else 0.0,
        freezes=freezes,
        design=clip.identity,
    )


def _sound(clip: AudioClip) -> f.SoundFacts:
    return f.SoundFacts(
        clip_id=clip.clip_id,
        track_id=clip.track_id,
        role=clip.role.value,
        start=_seconds(clip.timeline_start),
        end=_seconds(clip.timeline_end),
        gain_db=clip.gain_db,
        fade_in=_seconds(clip.fade_in),
        fade_out=_seconds(clip.fade_out),
        section_id=clip.section_id,
        unresolved=clip.state.value == "unresolved",
        enabled=clip.enabled,
        source_id=clip.source.source_id if clip.source else None,
    )


def timeline_facts(
    timeline: BuiltTimeline, *, specification: TimelineSpecification | None = None
) -> f.TimelineFacts:
    settings = timeline.settings
    roles = {t.track_id: t.role.value for t in timeline.tracks}
    cues = tuple(
        f.CueFacts(c.index, c.start_frame, c.end_frame, c.text)
        for c in (timeline.subtitles.cues if timeline.subtitles else ())
    )
    identity = timeline.identity
    items: tuple[str, ...] | None = None
    if specification is not None:
        items = (
            *(v.item_id for v in specification.video),
            *(o.item_id for o in specification.overlays),
            *(a.item_id for a in specification.audio),
            *(t.transition_id for t in specification.transitions),
        )
    return f.TimelineFacts(
        timeline_id=timeline.timeline_id,
        version=timeline.build.build_version,
        content_hash=timeline.build.build_hash,
        anchor_asset_id=timeline.anchor_asset_id,
        project_id=timeline.project_id,
        settings=f.SettingsFacts(
            width=settings.width,
            height=settings.height,
            fps=_fraction(settings.frame_rate) or Fraction(25),
            fps_assumed=settings.frame_rate_assumed,
            duration_frames=settings.duration_frames,
            fit_mode=settings.fit_mode.value,
            sample_rate=settings.sample_rate,
        ),
        tracks=tuple(
            f.TrackFacts(t.track_id, t.kind.value, t.role.value, t.name, t.enabled)
            for t in timeline.tracks
        ),
        clips=tuple(_video(c, roles.get(c.track_id, "")) for c in timeline.video_clips()),
        sounds=tuple(_sound(c) for c in timeline.audio_clips()),
        sources=tuple(
            f.SourceFacts(
                source_id=s.source_id,
                original_asset_id=s.original_asset_id,
                name=s.name,
                media_type=s.media_type,
                available=s.available,
                duration=_fraction(s.duration),
                width=s.width,
                height=s.height,
                frame_rate=_fraction(s.frame_rate),
                used_by=s.used_by,
            )
            for s in timeline.sources
        ),
        transitions=tuple(
            f.TransitionFacts(
                transition_id=t.transition_id,
                mode=t.mode.value,
                kind=t.kind,
                cut_frame=t.cut_frame,
                start_frame=t.start_frame,
                end_frame=t.end_frame,
                from_clip=t.from_clip,
                to_clip=t.to_clip,
                supported=t.supported,
                handles_sufficient=t.handles.sufficient,
                execution=t.execution.value,
            )
            for t in timeline.transitions
        ),
        sections=tuple(
            f.SectionFacts(s.section_id, s.name, s.start_frame, s.end_frame, s.clip_ids)
            for s in sorted(timeline.sections, key=lambda s: s.order)
        ),
        cues=cues,
        identity=f.IdentityFacts(
            format_profile=identity.format_profile,
            zones=tuple(
                f.ZoneFacts(z.name, z.kind, z.left, z.top, z.width, z.height)
                for z in identity.zones
            ),
            fonts=tuple((font.family, font.available) for font in identity.fonts),
            brand_slots=tuple(
                (b.slot, b.required, b.asset_id is not None) for b in identity.brand_assets
            ),
        ),
        dependencies=tuple(
            f.DependencyFacts(
                d.dependency_id,
                d.kind.value,
                d.reference,
                d.state.value == "resolved",
                d.required_by,
            )
            for d in timeline.dependencies
        ),
        builder_issues=tuple(
            f.BuilderIssueFacts(
                i.code, i.severity.value, i.message, i.element_id, i.start_frame, i.end_frame
            )
            for i in timeline.issues
        ),
        render_checks_pending=tuple(k.value for k in timeline.validation.not_run),
        deleted_by_human=tuple(t.element_id for t in timeline.removed_by_human),
        specification_items=items,
    )


def mix_facts(result: MixResult) -> f.MixFacts:
    delivery = next((d for d in DELIVERIES if d.key == result.delivery), None)
    rate = result.mix.measurement.sample_rate if result.mix else 0
    rate = rate or 48000
    return f.MixFacts(
        mix_result_id=f"{result.mix_job_id}",
        delivery=result.delivery,
        integrated_target_lufs=delivery.integrated_lufs if delivery else None,
        tolerance_lu=delivery.tolerance_lu if delivery else None,
        true_peak_ceiling_dbtp=delivery.true_peak_dbtp if delivery else None,
        loudness_range_limit_lu=delivery.max_loudness_range_lu if delivery else None,
        state=result.state.value,
        issues=tuple(
            f.MixIssueFacts(
                code=i.code,
                severity=i.severity.value,
                certainty=i.certainty.value,
                message=i.message,
                element_id=i.element_id,
                start_seconds=Fraction(i.start_sample, rate)
                if i.start_sample is not None
                else None,
                end_seconds=Fraction(i.end_sample, rate) if i.end_sample is not None else None,
            )
            for i in result.issues
        ),
    )


def continuity_facts(analysis: ContinuityAnalysis) -> f.ContinuityFacts:
    return f.ContinuityFacts(
        analysis_id=analysis.continuity_analysis_id,
        issues=tuple(
            f.ContinuityIssueFacts(
                issue_id=i.issue_id,
                kind=i.type.value,
                level=i.level.value,
                at_seconds=_fraction(i.timeline_at),
                explanation=i.explanation,
                why_it_matters=i.why_it_matters,
                detectability=i.detectability_confidence,
                style_likelihood=i.intentional_style_likelihood,
                sections=i.section_ids,
                recommended=i.recommended.value if i.recommended else "",
                surfaced=i.surfaced,
            )
            for i in analysis.issues
        ),
    )


def story_facts(plan: StoryPlan) -> f.StoryFacts:
    return f.StoryFacts(
        plan_id=plan.story_plan_id,
        sections=tuple(
            f.StorySectionFacts(s.section_id, s.label, s.order, s.reorder_locked is not None)
            for s in plan.sections
        ),
        links=tuple(
            f.StoryLinkFacts(
                link.kind.value, link.setup_section, link.payoff_section, link.state.value
            )
            for link in plan.setup_payoff_links
        ),
    )


def resolve_facts(
    state: SyncState | None,
    timeline: BuiltTimeline,
    *,
    live_changes: int | None = None,
    live_note: str = "",
    live_checked: bool = False,
) -> f.ResolveFacts | None:
    if state is None:
        return None
    return f.ResolveFacts(
        bound=True,
        bound_version=state.build_version,
        bound_hash_matches=state.build_hash == timeline.build.build_hash,
        open_conflicts=tuple(c.element_id for c in state.conflicts),
        unverified_elements=0,
        live_checked=live_checked,
        live_changes=live_changes,
        live_note=live_note,
    )
