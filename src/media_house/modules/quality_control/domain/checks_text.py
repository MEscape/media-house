"""Checks of the text on screen: caption cues and the graphics around them."""

from itertools import pairwise

from media_house.modules.quality_control.domain.classify import make_issue
from media_house.modules.quality_control.domain.facts import ClipFacts, TimelineFacts
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.profile import QualityProfile

#: How far (in picture widths / heights) a graphic may sit off-centre before it leaves the frame.
_OFF_FRAME = 1.0
_EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF))


def _has_emoji(text: str) -> bool:
    return any(lo <= ord(ch) <= hi for ch in text for lo, hi in _EMOJI_RANGES)


def check_captions(timeline: TimelineFacts, profile: QualityProfile) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    fps = float(timeline.settings.fps)
    cues = sorted(timeline.cues, key=lambda c: (c.start_frame, c.index))
    for cue in cues:
        seconds = (cue.end_frame - cue.start_frame) / fps
        text = cue.text.strip()
        if not text:
            issues.append(
                make_issue(
                    timeline,
                    "caption.empty",
                    f"Caption {cue.index} has no text.",
                    key=str(cue.index),
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
            continue
        if cue.end_frame > timeline.settings.duration_frames:
            issues.append(
                make_issue(
                    timeline,
                    "caption.beyond_timeline",
                    f"Caption {cue.index} ends after the timeline does.",
                    key=str(cue.index),
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
        if len(text) > profile.max_cue_characters:
            issues.append(
                make_issue(
                    timeline,
                    "caption.too_dense",
                    f"Caption {cue.index} has {len(text)} characters (more than "
                    f"{profile.max_cue_characters}).",
                    key=str(cue.index),
                    evidence=(text[:80],),
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
        if seconds > 0 and len(text) / seconds > profile.max_chars_per_second:
            issues.append(
                make_issue(
                    timeline,
                    "caption.reading_speed",
                    f"Caption {cue.index} needs {len(text) / seconds:.0f} characters per second "
                    f"(comfortable: up to {profile.max_chars_per_second:g}).",
                    confidence=0.8,
                    key=str(cue.index),
                    evidence=(text[:80],),
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
        if seconds < profile.min_cue_seconds and len(text.split()) > 2:
            issues.append(
                make_issue(
                    timeline,
                    "caption.too_fast",
                    f"Caption {cue.index} is on screen for {seconds:.2f} s.",
                    key=str(cue.index),
                    evidence=(text[:80],),
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
        if _has_emoji(text) and seconds < 0.5:
            issues.append(
                make_issue(
                    timeline,
                    "caption.emoji_flash",
                    f"Caption {cue.index} shows an emoji for {seconds:.2f} s.",
                    confidence=0.5,
                    key=str(cue.index),
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
    for a, b in pairwise(cues):
        if b.start_frame < a.end_frame:
            issues.append(
                make_issue(
                    timeline,
                    "caption.overlap",
                    f"Captions {a.index} and {b.index} are on screen together for "
                    f"{a.end_frame - b.start_frame} frames.",
                    start_frame=b.start_frame,
                    end_frame=min(a.end_frame, b.end_frame),
                    key=f"{a.index}-{b.index}",
                )
            )
    if not cues and not any(c.kind == "caption" for c in timeline.clips):
        speech = [s for s in timeline.sounds if s.role == "dialogue" and s.enabled]
        if speech:
            issues.append(
                make_issue(
                    timeline,
                    "caption.missing",
                    "The timeline has dialogue and no captions.",
                    confidence=0.4,
                )
            )
    issues += _consistent(timeline, "caption", "caption.style_inconsistent", "captions")
    return issues


def _consistent(timeline: TimelineFacts, kind: str, code: str, noun: str) -> list[QualityIssue]:
    styled = [c for c in timeline.clips if c.kind == kind and c.design]
    variants = {c.design for c in styled}
    if len(variants) <= 1:
        return []
    common = max(variants, key=lambda v: sum(1 for c in styled if c.design == v))
    odd = tuple(c for c in styled if c.design != common)
    return [
        make_issue(
            timeline,
            code,
            f"{len(odd)} of {len(styled)} {noun} are styled differently from the rest.",
            confidence=0.7,
            start_frame=odd[0].start_frame,
            end_frame=odd[0].end_frame,
            clips=odd[:5],
            evidence=tuple(f"{k}={v}" for k, v in odd[0].design[:4]),
            key=kind,
        )
    ]


def check_graphics(timeline: TimelineFacts, profile: QualityProfile) -> list[QualityIssue]:
    del profile
    issues: list[QualityIssue] = []
    zones = {z.name for z in timeline.identity.zones}
    for family, available in timeline.identity.fonts:
        if not available:
            issues.append(
                make_issue(
                    timeline,
                    "graphic.font_missing",
                    f"The font '{family}' of the channel identity is not available.",
                    key=family,
                )
            )
    for slot, required, has_asset in timeline.identity.brand_slots:
        if required and not has_asset:
            issues.append(
                make_issue(
                    timeline,
                    "graphic.brand_missing",
                    f"The required brand asset '{slot}' is not set.",
                    key=slot,
                )
            )
    graphics = [c for c in timeline.clips if c.kind in ("graphic", "caption") and c.enabled]
    for clip in graphics:
        if zones and clip.zone and clip.zone not in zones:
            issues.append(
                make_issue(
                    timeline,
                    "graphic.unknown_zone",
                    f"'{clip.name}' sits in zone '{clip.zone}', which the channel identity does "
                    f"not define.",
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                )
            )
        if (
            abs(clip.position_x) > _OFF_FRAME * timeline.settings.width
            or abs(clip.position_y) > _OFF_FRAME * timeline.settings.height
        ):
            issues.append(
                make_issue(
                    timeline,
                    "graphic.outside_frame",
                    f"'{clip.name}' is placed at ({clip.position_x:g}, {clip.position_y:g}), "
                    "outside the picture.",
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                )
            )
    issues += _overlaps(timeline, [c for c in graphics if c.kind == "graphic"])
    issues += _consistent(timeline, "graphic", "graphic.style_inconsistent", "graphics")
    return issues


def _overlaps(timeline: TimelineFacts, graphics: list[ClipFacts]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    ordered = sorted(graphics, key=lambda c: c.start_frame)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.start_frame >= a.end_frame:
                break
            if a.zone and a.zone == b.zone:
                issues.append(
                    make_issue(
                        timeline,
                        "graphic.overlap",
                        f"'{a.name}' and '{b.name}' are both in zone '{a.zone}' at the same time.",
                        start_frame=b.start_frame,
                        end_frame=min(a.end_frame, b.end_frame),
                        clips=(a, b),
                    )
                )
    return issues
