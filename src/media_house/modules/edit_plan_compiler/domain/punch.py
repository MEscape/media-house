"""Jump-cut punch-ins: every other cut of the talking picture is framed tighter.

A creator edit hides a jump cut by changing the framing: wide, then tight, then wide again. That
is a STATIC window per clip (no animation, so nothing can stutter or pop mid-shot): the window is
a reframe with one key, which a timeline application sets as plain zoom and position. Only clips
of the main picture that follow another main-picture clip straight away alternate; a clip after a
gap, a baked VFX clip or a clip that already has a reframing window starts wide again.
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from media_house.modules.edit_plan_compiler.domain.model import CropKey, Reframe, VideoItem
from media_house.modules.edit_plan_compiler.domain.timing import fr
from media_house.modules.edit_plan_compiler.domain.values import ItemOrigin

if TYPE_CHECKING:
    from fractions import Fraction

#: Where the window sits vertically: a talking head's eyes are in the upper half.
_FACE_Y = 0.42


def punch_in_jump_cuts(video: Sequence[VideoItem], zoom: float) -> tuple[VideoItem, ...]:
    if zoom <= 1.0:
        return tuple(video)
    side = 1.0 / zoom
    y = min(max(_FACE_Y - side / 2, 0.0), 1.0 - side)
    window = (round((1.0 - side) / 2, 6), round(y, 6), round(side, 6), round(side, 6))
    main = sorted(
        (v for v in video if v.layer == 0 and v.origin is ItemOrigin.EDIT),
        key=lambda v: fr(v.timeline_start),
    )
    tight: set[str] = set()
    previous_end: Fraction | None = None
    previous_tight = True
    for item in main:
        follows = previous_end is not None and fr(item.timeline_start) == previous_end
        now_tight = follows and not previous_tight and not item.reframe
        if now_tight:
            tight.add(item.item_id)
        previous_tight = now_tight or bool(item.reframe)
        previous_end = fr(item.timeline_end)
    return tuple(_tightened(v, window, zoom) if v.item_id in tight else v for v in video)


def _tightened(
    item: VideoItem, window: tuple[float, float, float, float], zoom: float
) -> VideoItem:
    x, y, w, h = window
    key = CropKey(source_time=item.source_start, x=x, y=y, w=w, h=h, interpolation="hold")
    reframe = Reframe(
        target="source",
        recommendation_id=f"punch_in:{item.item_id}",
        analysis_id=f"jump_cut_zoom:{zoom:g}",
        strategy="punch_in",
        keys=(key,),
    )
    return replace(item, reframe=(reframe,))
