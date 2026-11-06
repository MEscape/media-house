"""Whole-timeline checks that need the assembled items (per-item checks live in the units).

Classes: CRITICAL (blocks), WARNING (executable, look at it), CREATIVE_CONFLICT (two instructions
contradict; both kept). A deterministic technical fix is done where it is made (units) and
reported as AUTO_RESOLVED. Nothing here changes an item.
"""

from fractions import Fraction
from itertools import pairwise

from media_house.modules.edit_plan_compiler.domain.facts import CompileInputs, EditRef, PlanStamp
from media_house.modules.edit_plan_compiler.domain.model import (
    AudioItem,
    Issue,
    OverlayItem,
    Timebase,
    VideoItem,
)
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.timing import fr, overlap, rat
from media_house.modules.edit_plan_compiler.domain.values import IssueClass, ItemOrigin, TrackRole


def _issue(
    klass: IssueClass,
    code: str,
    message: str,
    *,
    item_id: str | None = None,
    start: Fraction | None = None,
    end: Fraction | None = None,
    evidence: str = "",
) -> Issue:
    return Issue(
        code=code,
        klass=klass,
        message=message,
        item_id=item_id,
        timeline_start=None if start is None else rat(start),
        timeline_end=None if end is None else rat(end),
        evidence=evidence,
    )


def check_timeline(
    inputs: CompileInputs,
    config: CompilerConfig,
    timebase: Timebase,
    video: tuple[VideoItem, ...],
    overlays: tuple[OverlayItem, ...],
    audio: tuple[AudioItem, ...],
) -> list[Issue]:
    fps = fr(timebase.frame_rate)
    found: list[Issue] = []
    found += _plans(inputs, config)
    found += _primary(video, fps, config)
    found += _sync(inputs, fps)
    found += _bridges(inputs, video, audio, fps)
    found += _overlays(overlays)
    found += _beyond(timebase, overlays, audio)
    found += _source_rates(video, fps)
    found += _identity(inputs)
    found += _duplicates(video, overlays, audio)
    return found


def _stamps(inputs: CompileInputs) -> list[tuple[str, PlanStamp]]:
    blocks = (
        ("graphics", inputs.graphics),
        ("captions", inputs.captions),
        ("transitions", inputs.transitions),
        ("vfx", inputs.vfx),
        ("sound_design", inputs.sound),
    )
    return [(name, b.stamp) for name, b in blocks if b is not None]


def _edit_name(edit: EditRef | None) -> str:
    return f"edit {edit.edit_plan_id} v{edit.plan_version}" if edit else "no edit"


def _plans(inputs: CompileInputs, config: CompilerConfig) -> list[Issue]:
    found: list[Issue] = []
    source = inputs.source
    for kind, why in inputs.undelivered:
        found.append(
            _issue(
                IssueClass.CRITICAL,
                "upstream_not_delivered",
                f"the {kind} plan was requested for this timeline but did not deliver ({why}); "
                "a failed upstream result is never compiled as if it had succeeded",
                evidence=kind,
            )
        )
    style = inputs.style
    for kind, stamp in _stamps(inputs):
        edit = stamp.edit
        mine = source.edit
        if (edit is None) != (mine is None) or (
            edit is not None
            and mine is not None
            and (edit.edit_plan_id, edit.plan_version) != (mine.edit_plan_id, mine.plan_version)
        ):
            found.append(
                _issue(
                    IssueClass.CRITICAL,
                    "plan_for_another_edit",
                    f"the {kind} plan {stamp.plan_id} was made for {_edit_name(edit)}, "
                    f"this timeline is for {_edit_name(mine)}",
                    evidence=stamp.plan_id,
                )
            )
        if stamp.style_fingerprint is not None and stamp.style_fingerprint != style.fingerprint:
            found.append(
                _issue(
                    IssueClass.WARNING if config.allow_style_mismatch else IssueClass.CRITICAL,
                    "style_mismatch",
                    f"the {kind} plan was made with another channel style "
                    f"({stamp.style_fingerprint[:12]}), the timeline uses "
                    f"{style.fingerprint[:12]}: mixing them would break the channel's "
                    "visual consistency",
                    evidence=stamp.plan_id,
                )
            )
    return found


def _primary(video: tuple[VideoItem, ...], fps: Fraction, config: CompilerConfig) -> list[Issue]:
    main = sorted((v for v in video if v.layer == 0), key=lambda v: (v.start_frame, v.item_id))
    found: list[Issue] = []
    if main and main[0].start_frame != 0:
        found.append(
            _issue(
                IssueClass.WARNING,
                "timeline_starts_late",
                f"the main picture starts at frame {main[0].start_frame}, not at 0",
                item_id=main[0].item_id,
                start=fr(main[0].timeline_start),
            )
        )
    for a, b in pairwise(main):
        if b.start_frame < a.end_frame:
            found.append(
                _issue(
                    IssueClass.CRITICAL,
                    "overlapping_primary_clips",
                    f"{a.clip_id} and {b.clip_id} overlap on the main picture track by "
                    f"{a.end_frame - b.start_frame} frames",
                    item_id=b.item_id,
                    start=fr(b.timeline_start),
                    end=fr(a.timeline_end),
                )
            )
        elif b.start_frame - a.end_frame > config.gap_tolerance_frames:
            found.append(
                _issue(
                    IssueClass.WARNING,
                    "gap_on_primary",
                    f"{b.start_frame - a.end_frame} empty frames between {a.clip_id} and "
                    f"{b.clip_id} on the main picture track",
                    item_id=b.item_id,
                    start=fr(a.timeline_end),
                    end=fr(b.timeline_start),
                )
            )
    return found


def _sync(inputs: CompileInputs, fps: Fraction) -> list[Issue]:
    """A sound placement and the picture it is linked to keep ONE offset between timeline and
    recording (J/L cuts trim and extend, they never slide the sound)."""
    clips = {c.clip_id: c for c in inputs.source.clips}
    found: list[Issue] = []
    for c in inputs.source.sound():
        other = clips.get(c.linked or "")
        if other is None or other.asset_id != c.asset_id:
            continue
        a = fr(c.timeline_start) - fr(c.source_start)
        b = fr(other.timeline_start) - fr(other.source_start)
        if abs(a - b) > 1 / fps:
            found.append(
                _issue(
                    IssueClass.CRITICAL,
                    "audio_video_out_of_sync",
                    f"the sound of {c.clip_id} sits {float(a - b):+.3f}s off its picture "
                    f"{other.clip_id}",
                    item_id=f"a:{c.clip_id}",
                    start=fr(c.timeline_start),
                )
            )
    return found


def _bridges(
    inputs: CompileInputs,
    video: tuple[VideoItem, ...],
    audio: tuple[AudioItem, ...],
    fps: Fraction,
) -> list[Issue]:
    edges_v = {f for v in video if v.layer == 0 for f in (v.start_frame, v.end_frame)}
    edges_a = {
        f
        for a in audio
        if a.role is TrackRole.DIALOGUE
        for f in (round(fr(a.timeline_start) * fps), round(fr(a.timeline_end) * fps))
    }
    found: list[Issue] = []
    for b in inputs.source.bridges:
        pf, af = round(fr(b.picture_at) * fps), round(fr(b.audio_at) * fps)
        near_v = any(abs(pf - e) <= 1 for e in edges_v)
        near_a = any(abs(af - e) <= 1 for e in edges_a)
        if not (near_v and near_a):
            found.append(
                _issue(
                    IssueClass.WARNING,
                    "bridge_not_realised",
                    f"the {b.kind} {b.bridge_id} is declared (sound at "
                    f"{float(fr(b.audio_at)):.3f}s, picture at {float(fr(b.picture_at)):.3f}s) "
                    "but the placements do not change there",
                    item_id=b.bridge_id,
                    start=fr(b.picture_at),
                )
            )
    return found


def _overlays(overlays: tuple[OverlayItem, ...]) -> list[Issue]:
    found: list[Issue] = []
    ordered = sorted(overlays, key=lambda o: (o.start_frame, o.item_id))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.start_frame >= a.end_frame:
                break
            if a.origin is ItemOrigin.CAPTIONS and b.origin is ItemOrigin.CAPTIONS:
                found.append(
                    _issue(
                        IssueClass.WARNING,
                        "caption_overlap",
                        f"captions {a.source_ref} and {b.source_ref} are on screen together",
                        item_id=b.item_id,
                        start=fr(b.timeline_start),
                        end=fr(a.timeline_end),
                    )
                )
            elif (
                a.zone
                and a.zone == b.zone
                and ItemOrigin.TRANSITION not in (a.origin, b.origin)
                and overlap(
                    fr(a.timeline_start),
                    fr(a.timeline_end),
                    fr(b.timeline_start),
                    fr(b.timeline_end),
                )
            ):
                found.append(
                    _issue(
                        IssueClass.CREATIVE_CONFLICT,
                        "zone_overlap",
                        f"{a.kind} {a.source_ref} and {b.kind} {b.source_ref} claim the same "
                        f"layout zone ({a.zone}) at the same time",
                        item_id=b.item_id,
                        start=fr(b.timeline_start),
                        end=min(fr(a.timeline_end), fr(b.timeline_end)),
                    )
                )
    return found


def _beyond(
    timebase: Timebase, overlays: tuple[OverlayItem, ...], audio: tuple[AudioItem, ...]
) -> list[Issue]:
    end = timebase.duration_frames
    found = [
        _issue(
            IssueClass.WARNING,
            "item_beyond_timeline",
            f"{o.kind} {o.source_ref} runs to frame {o.end_frame}, past the timeline's {end}",
            item_id=o.item_id,
            start=fr(o.timeline_start),
        )
        for o in overlays
        if o.end_frame > end
    ]
    rate = fr(timebase.frame_rate)
    found += [
        _issue(
            IssueClass.WARNING,
            "item_beyond_timeline",
            f"sound {a.source_ref} runs past the timeline's end",
            item_id=a.item_id,
            start=fr(a.timeline_start),
        )
        for a in audio
        if round(fr(a.timeline_end) * rate) > end
    ]
    return found


def _source_rates(video: tuple[VideoItem, ...], fps: Fraction) -> list[Issue]:
    found: list[Issue] = []
    seen: set[str] = set()
    for v in sorted(video, key=lambda v: v.item_id):
        if v.original_asset_id in seen:
            continue
        if v.source_variable_rate:
            seen.add(v.original_asset_id)
            found.append(
                _issue(
                    IssueClass.WARNING,
                    "variable_frame_rate_source",
                    f"{v.original_asset_id} is variable frame rate: cuts are exact in seconds, "
                    "the picture may need conforming to a constant rate",
                    item_id=v.item_id,
                )
            )
        elif v.source_frame_rate is not None and fr(v.source_frame_rate) != fps:
            seen.add(v.original_asset_id)
            found.append(
                _issue(
                    IssueClass.WARNING,
                    "frame_rate_conversion",
                    f"{v.original_asset_id} is {v.source_frame_rate} fps on a {float(fps):g} fps "
                    "timeline: the picture is conformed, cut times stay exact",
                    item_id=v.item_id,
                )
            )
    return found


def _identity(inputs: CompileInputs) -> list[Issue]:
    style = inputs.style
    found: list[Issue] = []
    for d in style.drift:
        if d.severity in {"error", "critical"}:
            klass = IssueClass.CREATIVE_CONFLICT
        elif d.severity == "warning":
            klass = IssueClass.WARNING
        else:
            continue
        found.append(
            _issue(
                klass,
                f"identity:{d.code}",
                f"channel identity: {d.message}",
                item_id=d.item_id,
            )
        )
    for f in style.fonts:
        if not f.available:
            found.append(
                _issue(
                    IssueClass.WARNING,
                    "font_unavailable",
                    f"font {f.family} {f.weight} ({f.key}) is not available: text cannot be "
                    "re-rendered in the channel face",
                    evidence=f.key,
                )
            )
    for b in style.brand_assets:
        if b.required and b.asset_id is None:
            found.append(
                _issue(
                    IssueClass.WARNING,
                    "brand_asset_missing",
                    f"the channel's required brand asset {b.slot!r} ({b.purpose}) is not bound",
                    evidence=b.slot,
                )
            )
    return found


def _duplicates(
    video: tuple[VideoItem, ...], overlays: tuple[OverlayItem, ...], audio: tuple[AudioItem, ...]
) -> list[Issue]:
    seen: set[str] = set()
    found: list[Issue] = []
    for item_id in (
        *(v.item_id for v in video),
        *(o.item_id for o in overlays),
        *(a.item_id for a in audio),
    ):
        if item_id in seen:
            found.append(
                _issue(
                    IssueClass.CRITICAL,
                    "duplicate_item_id",
                    f"item id {item_id} appears twice (two plan items have the same identity)",
                    item_id=item_id,
                )
            )
        seen.add(item_id)
    return found
