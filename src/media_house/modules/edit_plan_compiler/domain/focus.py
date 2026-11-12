"""Picture treatments that follow the graphics: focus blur and full-frame backgrounds.

* Focus blur: while a graphic that wants the viewer's attention (a chart, a list, a comparison, a
  timeline) is on screen over the talking picture, the main picture blurs: in fast and eased when
  the graphic arrives, out fast when it leaves. Small graphics (stickers, price tags, labels,
  captions) never blur the picture, and a full-frame card hides it anyway.
* Background footage: a full-frame card plays over a muted clip of the given footage (a looping
  plate such as fireworks), each card from a different point of it, so repeats never look alike.
"""

from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.facts import AssetFacts
from media_house.modules.edit_plan_compiler.domain.model import BlurKey, OverlayItem, VideoItem
from media_house.modules.edit_plan_compiler.domain.timing import fr
from media_house.modules.edit_plan_compiler.domain.values import FULL_FRAME_ZONE, ItemOrigin

#: Graphic kinds that take the stage: the picture behind them blurs.
FOCUS_KINDS = frozenset(
    {
        "comparison",
        "comparison_matrix",
        "feature_list",
        "step_list",
        "timeline",
        "chart",
        "spec_card",
    }
)
_RAMP_IN = Fraction(1, 4)
_RAMP_OUT = Fraction(1, 5)
#: Focus graphics closer than this stay one blurred span (no flicker between them).
_BRIDGE = Fraction(1, 2)
#: Successive full-frame cards start this far apart in the background footage.
_BACKGROUND_STEP = Fraction(37)


def focus_blur(
    video: Sequence[VideoItem], overlays: Sequence[OverlayItem], size: float, fps: Fraction
) -> tuple[VideoItem, ...]:
    """Blur keys on every main-picture clip a focus graphic is shown over (``size`` 0 = off)."""
    if size <= 0:
        return tuple(video)
    spans = _spans(overlays)
    if not spans:
        return tuple(video)
    out: list[VideoItem] = []
    for v in video:
        keys = _keys(v, spans, size, fps) if v.layer == 0 and fr(v.speed) == 1 else ()
        out.append(v if not keys else _with_blur(v, keys))
    return tuple(out)


def _spans(overlays: Sequence[OverlayItem]) -> list[tuple[Fraction, Fraction]]:
    found = sorted(
        (fr(o.timeline_start), fr(o.timeline_end))
        for o in overlays
        if o.origin is ItemOrigin.GRAPHICS and o.kind in FOCUS_KINDS and o.zone != FULL_FRAME_ZONE
    )
    merged: list[tuple[Fraction, Fraction]] = []
    for a, b in found:
        if merged and a <= merged[-1][1] + _BRIDGE:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _keys(
    v: VideoItem, spans: Sequence[tuple[Fraction, Fraction]], size: float, fps: Fraction
) -> list[tuple[Fraction, float, str]]:
    """(seconds into the clip, blur size, curve into the key), strictly increasing."""
    start, last = fr(v.timeline_start), fr(v.timeline_start) + Fraction(max(v.frames - 1, 0)) / fps
    keys: list[tuple[Fraction, float, str]] = []

    def add(at: Fraction, value: float, curve: str) -> None:
        at = min(max(at, start), last) - start
        if keys and at <= keys[-1][0]:
            return
        keys.append((at, value, curve))

    for a, b in spans:
        if b <= start or a >= last:
            continue
        if a > start:
            add(a, 0.0, "hold")
            add(a + _RAMP_IN, size, "ease_out")
        else:
            add(start, size, "hold")
        if b < last:
            add(max(b - _RAMP_OUT, a + _RAMP_IN), size, "linear")
            add(b, 0.0, "ease_in")
        else:
            add(last, size, "linear")
    return keys


def _with_blur(v: VideoItem, keys: Sequence[tuple[Fraction, float, str]]) -> VideoItem:
    source_start = fr(v.source_start)
    return replace(
        v,
        blur=tuple(
            BlurKey(source_time=_rational(source_start + at), size=value, interpolation=curve)
            for at, value, curve in keys
        ),
    )


def backgrounds(
    overlays: Sequence[OverlayItem], footage: AssetFacts | None
) -> tuple[VideoItem, ...]:
    """A muted cover clip of ``footage`` under every full-frame graphic."""
    if footage is None or footage.duration is None:
        return ()
    length = fr(footage.duration)
    out: list[VideoItem] = []
    cards = sorted(
        (o for o in overlays if o.zone == FULL_FRAME_ZONE),
        key=lambda o: (fr(o.timeline_start), o.item_id),
    )
    for index, o in enumerate(cards):
        seconds = fr(o.timeline_end) - fr(o.timeline_start)
        room = length - seconds
        if room < 0:
            continue
        offset = (_BACKGROUND_STEP * index) % room if room > 0 else Fraction(0)
        out.append(
            VideoItem(
                item_id=f"v:background:{o.source_ref}",
                unit_id=o.unit_id,
                clip_id=f"background:{o.source_ref}",
                track_id="",
                origin=ItemOrigin.EDIT,
                asset_id=footage.asset_id,
                original_asset_id=footage.asset_id,
                source_start=_rational(offset),
                source_end=_rational(offset + seconds),
                source_start_frame=None,
                timeline_start=o.timeline_start,
                timeline_end=o.timeline_end,
                start_frame=o.start_frame,
                end_frame=o.end_frame,
                speed=Rational(1, 1),
                layer=1,
                section_id=o.section_id,
                beat_id=None,
                source_frame_rate=footage.frame_rate,
                source_variable_rate=footage.frame_rate_variable,
            )
        )
    return tuple(out)


def _rational(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)
