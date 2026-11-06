"""The three whole-timeline units: graphics, captions and the soundtrack."""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.digest import digest
from media_house.modules.edit_plan_compiler.domain.facts import (
    AssetFacts,
    CaptionFact,
    GraphicFact,
)
from media_house.modules.edit_plan_compiler.domain.model import (
    AudioAutomation,
    AudioItem,
    OverlayItem,
    TimelineSpecification,
)
from media_house.modules.edit_plan_compiler.domain.timing import fr, rat, sample_at
from media_house.modules.edit_plan_compiler.domain.units import Context, Unit, _facts_of, _reuse
from media_house.modules.edit_plan_compiler.domain.values import (
    Composite,
    Execution,
    IssueClass,
    ItemOrigin,
    TrackRole,
)

_RENDERED = frozenset({"rendered", "reused"})


def _usable(facts: AssetFacts | None) -> bool:
    return facts is not None and facts.exists and facts.file_available


def _beyond(ctx: Context, facts: AssetFacts, first: int, frames: int) -> bool:
    """The file is shorter than the part of it the item plays (frames at the timeline rate)."""
    if facts.duration is None:
        return False
    return first + frames > round(fr(facts.duration) * ctx.fps) + 1


def _rendered_required(ctx: Context) -> IssueClass:
    return IssueClass.CRITICAL if ctx.config.require_rendered_overlays else IssueClass.WARNING


# --- graphics ------------------------------------------------------------------------------------


def graphics_unit(ctx: Context, previous: TimelineSpecification | None) -> Unit | None:
    plan = ctx.inputs.graphics
    if plan is None:
        return None
    ids = [g.asset_id for g in plan.graphics]
    unit = Unit("graphics", digest((plan, _facts_of(ctx, *ids), ctx.fps, ctx.config.fingerprint())))
    if _reuse(unit, previous, ctx):
        return unit
    order = sorted(
        plan.graphics,
        key=lambda g: (fr(g.timeline_start) if g.timeline_start else Fraction(-1), g.graphic_id),
    )
    for g in order:
        _graphic(ctx, unit, g)
    return unit


def _graphic(ctx: Context, unit: Unit, g: GraphicFact) -> None:
    item_id = f"o:graphic:{g.graphic_id}"
    if g.status != "ok" or g.asset_id is None or g.timeline_start is None:
        why = (
            "it was not placed on the timeline"
            if g.timeline_start is None and g.status == "ok"
            else f"status {g.status}"
        )
        unit.issue(
            _rendered_required(ctx),
            "graphic_not_rendered",
            f"graphic {g.graphic_id} ({g.kind}) is left out: {why}",
            item_id=g.graphic_id,
            start=None if g.timeline_start is None else fr(g.timeline_start),
            evidence=g.reason,
        )
        return
    facts = ctx.facts(g.asset_id)
    t0 = fr(g.timeline_start)
    t1 = t0 + fr(g.duration)
    if not _usable(facts):
        unit.issue(
            IssueClass.CRITICAL,
            "rendered_file_missing",
            f"the rendered file {g.asset_id} of graphic {g.graphic_id} is not available",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=g.asset_id,
        )
    sf, ef, n = ctx.span(t0, t1)
    if facts is not None and _beyond(ctx, facts, g.asset_start_frame, n):
        unit.issue(
            IssueClass.CRITICAL,
            "overlay_beyond_render",
            f"graphic {g.graphic_id} needs {n} frames from frame {g.asset_start_frame} but the "
            "rendered file is shorter",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=g.asset_id,
        )
    unit.overlays.append(
        OverlayItem(
            item_id=item_id,
            unit_id=unit.unit_id,
            origin=ItemOrigin.GRAPHICS,
            source_ref=g.graphic_id,
            kind=g.kind,
            track_id="",
            asset_id=g.asset_id,
            asset_start_frame=g.asset_start_frame,
            timeline_start=rat(t0),
            timeline_end=rat(t1),
            start_frame=sf,
            end_frame=ef,
            composite=Composite.ALPHA_OVER if g.has_alpha else Composite.NORMAL,
            execution=Execution.RENDERED,
            zone=g.zone,
            section_id=g.section_id,
            render_fingerprint=g.render_fingerprint,
        )
    )


# --- captions ------------------------------------------------------------------------------------


def captions_unit(ctx: Context, previous: TimelineSpecification | None) -> Unit | None:
    plan = ctx.inputs.captions
    if plan is None:
        return None
    ids = [c.asset_id for c in plan.segments]
    unit = Unit("captions", digest((plan, _facts_of(ctx, *ids), ctx.fps, ctx.config.fingerprint())))
    if _reuse(unit, previous, ctx) or plan.mode == "disabled":
        return unit
    for c in sorted(plan.segments, key=lambda c: (fr(c.timeline_start), c.segment_id)):
        _caption(ctx, unit, c)
    return unit


def _caption(ctx: Context, unit: Unit, c: CaptionFact) -> None:
    item_id = f"o:caption:{c.segment_id}"
    t0, t1 = fr(c.timeline_start), fr(c.timeline_end)
    if t1 <= t0:
        unit.issue(
            IssueClass.CRITICAL,
            "invalid_caption_timing",
            f"caption {c.segment_id} ends at or before it starts",
            item_id=item_id,
            start=t0,
        )
        return
    unknown = [i for i in c.clip_ids if i not in ctx.clips]
    if unknown:
        unit.issue(
            IssueClass.WARNING,
            "caption_unknown_clip",
            f"caption {c.segment_id} names clips the edit does not have: {', '.join(unknown)}",
            item_id=item_id,
            start=t0,
        )
    if c.render_status not in _RENDERED or c.asset_id is None:
        unit.issue(
            _rendered_required(ctx),
            "caption_not_rendered",
            f"caption {c.segment_id} has no rendered group ({c.render_status}); its text stays "
            "in the subtitle file",
            item_id=c.segment_id,
            start=t0,
            end=t1,
        )
        return
    facts = ctx.facts(c.asset_id)
    if not _usable(facts):
        unit.issue(
            IssueClass.CRITICAL,
            "rendered_file_missing",
            f"the rendered file {c.asset_id} of caption {c.segment_id} is not available",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=c.asset_id,
        )
    sf, ef, n = ctx.span(t0, t1)
    if facts is not None and _beyond(ctx, facts, c.asset_start_frame, n):
        unit.issue(
            IssueClass.CRITICAL,
            "overlay_beyond_render",
            f"caption {c.segment_id} needs {n} frames from frame {c.asset_start_frame} but the "
            "rendered file is shorter",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=c.asset_id,
        )
    unit.overlays.append(
        OverlayItem(
            item_id=item_id,
            unit_id=unit.unit_id,
            origin=ItemOrigin.CAPTIONS,
            source_ref=c.segment_id,
            kind="caption",
            track_id="",
            asset_id=c.asset_id,
            asset_start_frame=c.asset_start_frame,
            timeline_start=rat(t0),
            timeline_end=rat(t1),
            start_frame=sf,
            end_frame=ef,
            composite=Composite.ALPHA_OVER,
            execution=Execution.RENDERED,
            zone=c.zone,
            section_id=c.section_id,
            detail=c.look,
        )
    )


# --- the soundtrack ------------------------------------------------------------------------------


def soundtrack_unit(ctx: Context, previous: TimelineSpecification | None) -> Unit | None:
    plan = ctx.inputs.sound
    if plan is None:
        return None
    ids = [m.asset_id for m in plan.music] + [s.asset_id for s in plan.sfx]
    unit = Unit(
        "soundtrack",
        digest((plan, _facts_of(ctx, *ids), ctx.sound, ctx.fps, ctx.config.fingerprint())),
    )
    if _reuse(unit, previous, ctx):
        return unit
    cue_items: dict[str, list[str]] = {}
    for m in sorted(plan.music, key=lambda m: (fr(m.timeline_start), m.segment_id)):
        item = _sound(
            ctx,
            unit,
            item_id=f"a:music:{m.segment_id}",
            role=TrackRole.MUSIC,
            ref=m.segment_id,
            asset_id=m.asset_id,
            t0=fr(m.timeline_start),
            t1=fr(m.timeline_end),
            s0=fr(m.source_start),
            s1=fr(m.source_end),
            gain=m.gain_db,
            fade_in=m.fade_in,
            fade_out=m.fade_out,
            licence=m.licence_state,
        )
        cue_items.setdefault(m.cue_id, []).append(item)
    for s in sorted(plan.sfx, key=lambda s: (fr(s.timeline_start), s.item_id)):
        _sound(
            ctx,
            unit,
            item_id=f"a:sfx:{s.item_id}",
            role=TrackRole.SFX,
            ref=s.item_id,
            asset_id=s.asset_id,
            t0=fr(s.timeline_start),
            t1=fr(s.timeline_end),
            s0=fr(s.source_start),
            s1=fr(s.source_end),
            gain=s.gain_db,
            fade_in=None,
            fade_out=None,
            licence=s.licence_state,
        )
    for d in plan.ducking:
        targets = tuple(cue_items.get(d.cue_id, ()))
        if not targets:
            continue
        unit.automation.append(
            AudioAutomation(
                automation_id=f"x:duck:{d.duck_id}",
                unit_id=unit.unit_id,
                kind="duck",
                timeline_start=d.timeline_start,
                timeline_end=d.timeline_end,
                target_item_ids=targets,
                key=d.key,
                depth_db=d.depth_db,
                attack_seconds=d.attack_seconds,
                release_seconds=d.release_seconds,
                source_ref=d.duck_id,
            )
        )
    for x in plan.crossfades:
        targets = (
            *cue_items.get(x.outgoing_cue_id, []),
            *cue_items.get(x.incoming_cue_id or "", []),
        )
        if not targets:
            continue
        start = fr(x.timeline_at)
        unit.automation.append(
            AudioAutomation(
                automation_id=f"x:music_{x.kind}:{x.crossfade_id}",
                unit_id=unit.unit_id,
                kind=f"music_{x.kind}",
                timeline_start=rat(start),
                timeline_end=rat(start + fr(x.length)),
                target_item_ids=targets,
                timeline_at=x.timeline_at,
                after=x.length,
                sample_rate=ctx.sample_rate,
                source_ref=x.crossfade_id,
            )
        )
    for miss in plan.missing:
        unit.issue(
            IssueClass.WARNING,
            "sound_missing",
            f"the sound library has no {miss.kind} for: {miss.purpose} ({miss.priority})",
            item_id=miss.recommendation_id,
            start=fr(miss.timeline_start),
        )
    _original_audio(ctx, unit)
    return unit


def _sound(
    ctx: Context,
    unit: Unit,
    *,
    item_id: str,
    role: TrackRole,
    ref: str,
    asset_id: str,
    t0: Fraction,
    t1: Fraction,
    s0: Fraction,
    s1: Fraction,
    gain: float,
    fade_in: Rational | None,
    fade_out: Rational | None,
    licence: str,
) -> str:
    facts = ctx.facts(asset_id)
    if facts is None or not facts.exists:
        unit.issue(
            IssueClass.CRITICAL,
            "missing_source_asset",
            f"sound asset {asset_id} does not exist in the media library",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=asset_id,
        )
    elif not facts.file_available:
        unit.issue(
            IssueClass.CRITICAL,
            "source_file_unavailable",
            f"the file of sound asset {asset_id} is not available",
            item_id=item_id,
            evidence=asset_id,
        )
    elif facts.duration is not None and s1 > fr(facts.duration) + Fraction(1, 1000):
        unit.issue(
            IssueClass.CRITICAL,
            "source_range_beyond_media",
            f"sound range ends at {float(s1):.3f}s but the file is "
            f"{float(fr(facts.duration)):.3f}s",
            item_id=item_id,
            start=t0,
            end=t1,
            evidence=asset_id,
        )
    if licence == "not_commercial":
        unit.issue(
            IssueClass.WARNING,
            "sound_licence_not_commercial",
            f"sound asset {asset_id} is licensed for non-commercial use only",
            item_id=item_id,
            evidence=asset_id,
        )
    rate = ctx.sample_rate
    zero = Rational(0, 1)
    unit.audio.append(
        AudioItem(
            item_id=item_id,
            unit_id=unit.unit_id,
            origin=ItemOrigin.SOUND,
            role=role,
            track_id="",
            source_ref=ref,
            asset_id=asset_id,
            original_asset_id=(
                facts.original_asset_id if facts and facts.original_asset_id else asset_id
            ),
            source_start=rat(s0),
            source_end=rat(s1),
            timeline_start=rat(t0),
            timeline_end=rat(t1),
            sample_rate=rate,
            start_sample=sample_at(t0, rate),
            end_sample=sample_at(t1, rate),
            gain_db=gain,
            fade_in=fade_in or zero,
            fade_out=fade_out or zero,
            speed=rat((s1 - s0) / (t1 - t0)) if t1 > t0 else Rational(1, 1),
        )
    )
    return item_id


def _original_audio(ctx: Context, unit: Unit) -> None:
    """The sound plan states what happens to each clip's own sound; it must agree with the edit."""
    plan = ctx.inputs.sound
    if plan is None:
        return
    playing = {s.clip_id for s in ctx.sound}
    for o in plan.original_audio:
        has = o.clip_id in playing
        if o.mode == "muted_by_edit" and has:
            unit.issue(
                IssueClass.CREATIVE_CONFLICT,
                "original_audio_conflict",
                f"the sound plan says the edit mutes {o.clip_id}, but the edit plays its sound",
                item_id=o.clip_id,
                start=fr(o.timeline_start),
                end=fr(o.timeline_end),
            )
        elif o.mode in {"dominant", "under_music", "speech"} and not has:
            unit.issue(
                IssueClass.CREATIVE_CONFLICT,
                "original_audio_conflict",
                f"the sound plan keeps the sound of {o.clip_id} ({o.mode}), but the edit plays "
                "none for it",
                item_id=o.clip_id,
                start=fr(o.timeline_start),
                end=fr(o.timeline_end),
            )
