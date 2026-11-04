"""Invariants of a plan (broken = CRITICAL: never stored) and the judgement of render QC facts
(contracts sec. 6: ERROR = this treatment is unusable, its fallback applies)."""

from collections.abc import Mapping, Sequence
from fractions import Fraction

from media_house.modules.vfx_engine.domain.facts import Box, TrackFact, fr
from media_house.modules.vfx_engine.domain.model import (
    Mask,
    QcIssue,
    RenderQc,
    Treatment,
    VfxPlan,
)
from media_house.modules.vfx_engine.domain.profile import Qc
from media_house.modules.vfx_engine.domain.timing import FrameView, box_at, map_problems
from media_house.modules.vfx_engine.domain.values import (
    RENDER_PIX_FMT,
    TIME_EFFECTS,
    EffectStatus,
    EffectType,
    QcCategory,
    Severity,
    TreatmentStatus,
)


def plan_problems(plan: VfxPlan) -> list[str]:
    """Everything that makes the plan self-contradictory (empty = valid)."""
    found: list[str] = []
    fps = fr(plan.frame_rate)
    if fps <= 0:
        found.append("frame rate must be positive")
    ids = [t.treatment_id for t in plan.treatments]
    if len(ids) != len(set(ids)):
        found.append("duplicate treatment ids")
    for t in plan.treatments:
        found += [f"{t.clip_id}: {p}" for p in _treatment_problems(t, fps)]
    return found


def _treatment_problems(t: Treatment, fps: Fraction) -> list[str]:
    found: list[str] = []
    ts, te = fr(t.timeline_start), fr(t.timeline_end)
    if fr(t.source_end) <= fr(t.source_start) or te <= ts:
        found.append("empty or inverted span")
    if t.start_frame != round(ts * fps) or t.end_frame != round(te * fps):
        found.append("frames are not round(t x fps) of the span")
    if t.time_map is not None:
        found += map_problems(t.time_map, ts, te)
        if fr(t.time_map.source_used_end) > fr(t.source_end):
            found.append("the time map shows source beyond the clip")
    planned_time = any(
        e.effect_type in TIME_EFFECTS
        and e.status is not EffectStatus.REJECTED
        and e.status in {EffectStatus.PLANNED, EffectStatus.RENDERED, EffectStatus.REUSED}
        for e in t.effects
    )
    if planned_time and t.time_map is None:
        found.append("a time effect without a time map")
    if not t.effects:
        found.append("no effect decision (an untouched clip needs an explicit no_effect)")
    for e in t.effects:
        a, b = fr(e.timeline_start), fr(e.timeline_end)
        if a < ts or b > te or b < a:
            found.append(f"{e.effect_id}: span outside the clip")
        if (
            e.status is EffectStatus.PLANNED
            and e.effect_type is not EffectType.NO_EFFECT
            and b == a
        ):
            found.append(f"{e.effect_id}: a planned effect needs a span")
        for m in e.masks:
            times = [fr(k.source_time) for k in m.keyframes]
            if not times or times != sorted(times):
                found.append(f"{m.mask_id}: keyframes missing or out of order")
            for k in m.keyframes:
                bx = k.box
                if not (0 <= bx.x0 < bx.x1 <= 1 and 0 <= bx.y0 < bx.y1 <= 1):
                    found.append(f"{m.mask_id}: box outside the picture")
    if t.status in {TreatmentStatus.RENDERED, TreatmentStatus.REUSED} and t.output is None:
        found.append("rendered without an output asset")
    return found


def judge_render(t: Treatment, qc: RenderQc, limits: Qc) -> tuple[QcIssue, ...]:
    """Render QC facts -> issues. ERROR makes the treatment unusable (fallback applies)."""
    issues: list[QcIssue] = []

    def issue(code: str, severity: Severity, message: str, evidence: str = "") -> None:
        issues.append(
            QcIssue(
                code=code,
                severity=severity,
                category=QcCategory.TECHNICAL,
                message=message,
                item_id=t.treatment_id,
                timeline_start=t.timeline_start,
                timeline_end=t.timeline_end,
                evidence=evidence,
            )
        )

    if qc.frames_actual != qc.frames_expected:
        issue(
            "frame_count",
            Severity.ERROR,
            "the render does not have the planned number of frames",
            f"{qc.frames_actual} != {qc.frames_expected}",
        )
    if qc.pix_fmt != RENDER_PIX_FMT or qc.bit_depth < 10:
        issue("pixel_format", Severity.ERROR, "the render is not 10-bit 4:2:2", qc.pix_fmt)
    if qc.low_precision_steps:
        issue(
            "precision",
            Severity.WARNING,
            "the filter graph converted below 10 bits",
            ", ".join(qc.low_precision_steps),
        )
    if not qc.source_unchanged:
        issue("source_changed", Severity.CRITICAL, "the source file changed during rendering")
    if qc.new_spikes > limits.max_new_spikes:
        issue(
            "temporal_spikes",
            Severity.WARNING,
            "the render has frame-to-frame jumps the source does not have (flicker)",
            f"{qc.new_spikes} new spikes",
        )
    if qc.clipped_source is not None and qc.clipped_output is not None:
        rise = qc.clipped_output - qc.clipped_source
        if rise > limits.max_clipping_increase:
            issue(
                "clipping_increase",
                Severity.WARNING,
                "the effect clips more of the picture than the source",
                f"{qc.clipped_source:.3%} -> {qc.clipped_output:.3%}",
            )
    privacy = any(e.effect_type is EffectType.PRIVACY_BLUR for e in t.active())
    if privacy:
        if qc.coverage is not None and qc.coverage < 1.0:
            issue(
                "privacy_coverage",
                Severity.ERROR,
                "a concealment mask misses part of a tracked region",
                f"coverage {qc.coverage:.3f}",
            )
        if qc.concealment is not None and qc.concealment < limits.min_concealment:
            issue(
                "privacy_leak",
                Severity.ERROR,
                "detail remains visible inside a concealed region",
                f"removed {qc.concealment:.2f} < {limits.min_concealment:.2f}",
            )
    return tuple(issues)


def _privacy_tracks(t: Treatment, tracks: Mapping[str, TrackFact]) -> list[tuple[Mask, TrackFact]]:
    out = []
    for e in t.active():
        if e.effect_type is not EffectType.PRIVACY_BLUR:
            continue
        for m in e.masks:
            for ref in m.track_refs:
                track = tracks.get(ref.rsplit("/", 1)[-1])
                if track is not None:
                    out.append((m, track))
    return out


def mask_coverage(
    t: Treatment, tracks: Mapping[str, TrackFact], min_confidence: float
) -> float | None:
    """Share of the trusted track samples inside the clip that their mask contains."""
    checked = inside = 0
    ss, se = fr(t.source_start), fr(t.source_end)
    for mask, track in _privacy_tracks(t, tracks):
        for s in track.samples:
            if s.confidence < min_confidence or not ss <= fr(s.source_time) < se:
                continue
            checked += 1
            box = box_at(mask, fr(s.source_time))
            inside += box is not None and box.contains(s.box)
    return None if checked == 0 else inside / checked


def protected_boxes(
    t: Treatment,
    views: Sequence[FrameView],
    tracks: Mapping[str, TrackFact],
    min_confidence: float,
    max_gap: Fraction,
) -> tuple[tuple[Box, ...], ...]:
    """Per output frame, the exact (interpolated) tracked boxes that must be unrecognisable."""
    pairs = _privacy_tracks(t, tracks)
    out: list[tuple[Box, ...]] = []
    for view in views:
        boxes: list[Box] = []
        for _mask, track in pairs:
            trusted = [s for s in track.samples if s.confidence >= min_confidence]
            before = [s for s in trusted if fr(s.source_time) <= view.source_time]
            after = [s for s in trusted if fr(s.source_time) >= view.source_time]
            if not before or not after:
                continue
            a, b = before[-1], after[0]
            ta, tb = fr(a.source_time), fr(b.source_time)
            if tb - ta > max_gap:
                continue
            w = 0.0 if tb == ta else float((view.source_time - ta) / (tb - ta))
            boxes.append(
                Box(
                    x0=a.box.x0 + (b.box.x0 - a.box.x0) * w,
                    y0=a.box.y0 + (b.box.y0 - a.box.y0) * w,
                    x1=a.box.x1 + (b.box.x1 - a.box.x1) * w,
                    y1=a.box.y1 + (b.box.y1 - a.box.y1) * w,
                )
            )
        out.append(tuple(boxes))
    return tuple(out)
