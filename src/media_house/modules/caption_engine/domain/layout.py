"""Caption layout intent: the style's caption zone, subject-aware anchor, readability checks.

Captions sit ONLY in the caption zone of the look (``caption_band``; the meme ``line`` look uses
``title_band``). Inside it the block is centred; when a face or subject (video tracks mapped
through the reframing crop to the output) overlaps the block while the caption is up, the
block moves to the zone's top or bottom edge, whichever stays clear. If none does, the default
stays and a warning names the covered subject. The block size is an estimate (motion graphics
measures the real font and refuses overflow); 9:16 platform keep-outs are checked.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.caption_engine.domain.facts import (
    CaptionClip,
    Rect,
    StyleFacts,
    SubjectBox,
    frac,
)
from media_house.modules.caption_engine.domain.model import LayoutIntent
from media_house.modules.caption_engine.domain.profile import Layout

ANCHORS = ("center", "bottom", "top")


@dataclass(frozen=True, slots=True, kw_only=True)
class Placed:
    layout: LayoutIntent
    covered: tuple[str, ...]
    outside_zone: bool
    keep_outs: tuple[str, ...]


def block(lines: Sequence[str], style: StyleFacts, rules: Layout) -> tuple[int, int]:
    widest = max((len(line) for line in lines), default=0)
    width = round(widest * style.size_px * rules.char_width_em) + 2 * style.stroke_px
    height = style.line_px * max(1, len(lines)) + 2 * style.stroke_px
    return min(width, style.zone.rect.width), height


def _rect(anchor: str, width: int, height: int, zone: Rect) -> Rect:
    left = zone.left + (zone.width - width) // 2
    if anchor == "top":
        top = zone.top
    elif anchor == "bottom":
        top = zone.top + zone.height - height
    else:
        top = zone.top + (zone.height - height) // 2
    return Rect(left=left, top=top, width=width, height=height)


def subjects_during(
    start: Fraction,
    end: Fraction,
    clips: Sequence[CaptionClip],
    subjects: Sequence[SubjectBox],
    style: StyleFacts,
    margin: float,
) -> list[tuple[str, Rect]]:
    """Output-pixel boxes of every subject visible on screen between ``start`` and ``end``."""
    found: list[tuple[str, Rect]] = []
    for clip in clips:
        if not clip.picture:
            continue
        lo = max(start, frac(clip.timeline_start))
        hi = min(end, frac(clip.timeline_end))
        if hi <= lo:
            continue
        s0, s1 = clip.to_source(lo), clip.to_source(hi)
        for box in subjects:
            if box.asset_id != clip.asset_id or box.end <= s0 or box.start >= s1:
                continue
            x0 = max(0.0, box.x0 - margin) * style.width
            y0 = max(0.0, box.y0 - margin) * style.height
            x1 = min(1.0, box.x1 + margin) * style.width
            y1 = min(1.0, box.y1 + margin) * style.height
            found.append(
                (
                    box.label,
                    Rect(
                        left=round(x0),
                        top=round(y0),
                        width=max(1, round(x1 - x0)),
                        height=max(1, round(y1 - y0)),
                    ),
                )
            )
    return found


def place(
    lines: Sequence[str],
    subjects: Sequence[tuple[str, Rect]],
    style: StyleFacts,
    rules: Layout,
) -> Placed:
    width, height = block(lines, style, rules)
    zone = style.zone.rect
    options = ANCHORS if rules.avoid_subjects and subjects else ANCHORS[:1]
    tried: list[tuple[str, Rect, tuple[str, ...], int]] = []
    for anchor in options:
        rect = _rect(anchor, width, height, zone)
        hit = tuple(dict.fromkeys(label for label, box in subjects if rect.overlaps(box)))
        tried.append((anchor, rect, hit, sum(_overlap(rect, box) for _, box in subjects)))
        if not hit:
            break
    anchor, rect, hit, _ = min(tried, key=lambda t: (t[3], ANCHORS.index(t[0])))
    avoided = tuple(label for label in tried[0][2] if label not in hit)
    keep_outs = tuple(z.name for z in style.zones if z.keep_out and z.rect.overlaps(rect))
    reason = (
        f"moved to the zone {anchor} to keep {', '.join(avoided)} clear"
        if avoided
        else "zone default"
    )
    return Placed(
        layout=LayoutIntent(
            look=style.look,
            zone=style.zone.name,
            anchor=anchor,
            left=rect.left,
            top=rect.top,
            width=rect.width,
            height=rect.height,
            size_px=style.size_px,
            lines=tuple(lines),
            avoided=avoided,
            reason=reason,
        ),
        covered=hit,
        outside_zone=height > zone.height,
        keep_outs=keep_outs,
    )


def _overlap(a: Rect, b: Rect) -> int:
    w = min(a.left + a.width, b.left + b.width) - max(a.left, b.left)
    h = min(a.top + a.height, b.top + b.height) - max(a.top, b.top)
    return max(0, w) * max(0, h)
