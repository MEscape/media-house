"""Camera moves on the talking picture: how each clip of the main picture is framed over time.

A creator edit hides a jump cut by changing the framing at the cut, and keeps a long take alive
with slow movement. Flipping between a wide and a tight picture on EVERY cut looks nervous, so
the moves follow a calm cycle instead (each one a few keys of a reframe window, zoomed about the
speaker's face so the face never jumps):

* ``push``: a slow, eased push in over the whole clip;
* ``snap``: a fast punch in on the cut (a quarter second, settling softly), then a long slow
  release;
* ``pull``: starts tight on the cut, then slowly widens;
* ``snap_out``: the reverse of ``snap``: starts tight, snaps out, then creeps back in;
* ``hold``: a static framing (wide or tight), the plain hard punch.

Every cut between two clips of the same recording changes the framing by at least 6 % (the end of
one move and the start of the next differ), but the picture only rarely flips straight back. A
clip too short for a move holds one framing; a snap needs time to settle after it. Keys stay on
the clip's own frames. Clips that already have a window, baked VFX clips and clips after a gap
start the cycle again; a clip hidden under cover footage gets no move (nobody sees it).

Screen recordings get their own window (the page without the browser bar and the task bar), with
a slow push across the clip: either the window fills the frame (``crop``) or the whole window
floats as a card on a dark ground (``card``: strategy ``screen_card``, nothing of the page is cut).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.model import CropKey, Reframe, VideoItem
from media_house.modules.edit_plan_compiler.domain.timing import fr
from media_house.modules.edit_plan_compiler.domain.values import ItemOrigin

#: The point the picture grows from: a talking head's eyes are in the upper half.
_FACE = (0.5, 0.42)
#: How long the fast part of a snap takes (seconds).
_SNAP = Fraction(1, 4)
#: The shortest clip that gets a snap (the snap plus time to settle), and one that gets a slow
#: move (seconds).
_MIN_SNAP = _SNAP + Fraction(6, 5)
_MIN_SLOW = Fraction(12, 5)
#: The smallest change of framing a jump cut must show.
_MIN_CHANGE = 0.06
#: The calm cycle; a move a clip is too short for falls back to a hold.
_CYCLE = ("push", "snap", "hold_wide", "pull", "snap_out", "hold_tight")
#: A screen recording's push grows the window by this factor over the clip (a card less: it is
#: already smaller than the frame).
_SCREEN_PUSH = 1.08
_CARD_PUSH = 1.06

type Key = tuple[Fraction, float, str]  # (seconds into the clip, zoom, curve into the key)


def camera_moves(
    video: Sequence[VideoItem], zoom: float, fps: Fraction = Fraction(30)
) -> tuple[VideoItem, ...]:
    """Frame every clip of the main picture with a move of the cycle (``zoom`` = tightest)."""
    if zoom <= 1.0:
        return tuple(video)
    covers = _covered_spans(video)
    tight = min(zoom, 1.15)
    levels = {"wide": 1.0, "mid": 1.0 + (tight - 1.0) / 2, "tight": tight}
    main = sorted(
        (
            v
            for v in video
            if v.layer == 0
            and v.origin is ItemOrigin.EDIT
            and all(r.target != "source" for r in v.reframe)
        ),
        key=lambda v: fr(v.timeline_start),
    )
    moved: dict[str, tuple[Key, ...]] = {}
    previous_end: Fraction | None = None
    previous_asset = ""
    last_zoom = 1.0
    step = 0
    for item in main:
        if _hidden(item, covers) or fr(item.speed) != 1:
            previous_end = None  # the next visible cut is not a jump cut
            continue
        jump = (
            previous_end is not None
            and fr(item.timeline_start) == previous_end
            and item.original_asset_id == previous_asset
        )
        if not jump:
            step, last_zoom = 0, 0.0
        duration = _last_frame(item, fps)
        keys = _move(_CYCLE[step % len(_CYCLE)], duration, levels, last_zoom)
        step += 1
        moved[item.item_id] = keys
        last_zoom = keys[-1][1]
        previous_end, previous_asset = fr(item.timeline_end), item.original_asset_id
    return tuple(
        _framed(v, moved[v.item_id], _pivot_of(v), f"camera:{zoom:g}") if v.item_id in moved else v
        for v in video
    )


#: A portrait Short's default seconds between two framings (``CompilerConfig.camera_beat``).
SHORT_BEAT = 2.2
#: Short-form beats: the framing levels in turn (each differs from the one before it), the zoom
#: a cut between two shots punches through (on top of the tightest level), and how close to a
#: clip's end a beat may still land (s).
_BEATS = ("tight", "wide", "mid", "tight", "mid", "wide")
_THROUGH = 0.12
_BEAT_TAIL = Fraction(2, 5)
#: The slow creep of a held beat (a beat never sits perfectly still).
_DRIFT = 1.03


def camera_beats(
    video: Sequence[VideoItem], zoom: float, beat: Fraction, fps: Fraction = Fraction(30)
) -> tuple[VideoItem, ...]:
    """A Short's framing: a new framing every ``beat`` seconds of the talking picture (a fast
    eased punch to the next level of ``_BEATS``, ``zoom`` = tightest, then a slow creep), and a
    zoom-through at every cut between two different shots (the outgoing clip punches in over
    its last ``_SNAP``, the incoming one starts there and snaps out). Jump cuts continue the
    beat sequence, so the framing changes at every cut too."""
    if zoom <= 1.0 or beat <= 0:
        return tuple(video)
    covers = _covered_spans(video)
    levels = {"wide": 1.0, "mid": 1.0 + (zoom - 1.0) / 2, "tight": zoom}
    through = min(zoom + _THROUGH, 1.5)
    main = [
        v
        for v in sorted(
            (
                v
                for v in video
                if v.layer == 0
                and v.origin is ItemOrigin.EDIT
                and all(r.target != "source" for r in v.reframe)
            ),
            key=lambda v: fr(v.timeline_start),
        )
        if not _hidden(v, covers) and fr(v.speed) == 1
    ]
    moved: dict[str, tuple[Key, ...]] = {}
    step = 0
    for i, item in enumerate(main):
        before = main[i - 1] if i else None
        after = main[i + 1] if i + 1 < len(main) else None
        duration = _last_frame(item, fps)
        cut_in = before is not None and not _jump(before, item)
        cut_out = after is not None and not _jump(item, after)
        moved[item.item_id], step = _beats(
            duration, beat, levels, step, through if cut_in else None, through if cut_out else None
        )
    return tuple(
        _framed(v, moved[v.item_id], _pivot_of(v), f"beats:{zoom:g}:{beat}")
        if v.item_id in moved
        else v
        for v in video
    )


def _jump(a: VideoItem, b: VideoItem) -> bool:
    """``b`` continues ``a``'s recording straight after it (a jump cut, not a new shot)."""
    return fr(b.timeline_start) == fr(a.timeline_end) and a.original_asset_id == b.original_asset_id


def _beats(
    duration: Fraction,
    beat: Fraction,
    levels: Mapping[str, float],
    step: int,
    into: float | None,
    out: float | None,
) -> tuple[tuple[Key, ...], int]:
    """One clip's keys (and the next step of the sequence): a level per beat, punched to fast
    and then creeping in; ``into`` = the clip opens on that zoom and snaps out of it, ``out`` =
    it punches in to that zoom over its last ``_SNAP``."""
    level = levels[_BEATS[step % len(_BEATS)]]
    step += 1
    keys: list[Key] = []
    if into is not None and duration > 3 * _SNAP:
        keys = [(Fraction(0), into, "hold"), (_SNAP, level, "ease_out")]
    else:
        keys = [(Fraction(0), level, "hold")]
    end = duration - _SNAP if out is not None else duration
    at = beat
    while at + _BEAT_TAIL <= end:
        keys.append((at, round(level * _DRIFT, 4), "ease_in_out"))
        level = levels[_BEATS[step % len(_BEATS)]]
        step += 1
        keys.append((at + _SNAP, level, "ease_out"))
        at += beat
    if end > keys[-1][0]:
        keys.append((end, round(level * _DRIFT, 4), "ease_in_out"))
    if out is not None and duration > keys[-1][0]:
        keys.append((duration, out, "ease_in"))
    return tuple(keys), step


def _pivot_of(item: VideoItem) -> tuple[float, float]:
    """The face, or in a reframed format (9:16 of 16:9) the middle of the window the clip shows:
    the move then zooms into what the viewer sees."""
    keys = [k for r in item.reframe for k in r.keys]
    if not keys:
        return _FACE
    middle = (fr(item.source_start) + fr(item.source_end)) / 2
    k = min(keys, key=lambda k: abs(fr(k.source_time) - middle))
    return (round(k.x + k.w / 2, 6), _FACE[1])


def _move(
    name: str, duration: Fraction, levels: Mapping[str, float], last: float
) -> tuple[Key, ...]:
    wide, mid, tight = levels["wide"], levels["mid"], levels["tight"]
    keys = _moving(name, duration, wide, mid, tight, last)
    if keys is not None and _change(keys[0][1], last) >= _MIN_CHANGE:
        return keys
    # a hold: never (nearly) the framing the previous clip ended on: the cut must show a change
    target = tight if name == "hold_tight" else wide
    if _change(target, last) < _MIN_CHANGE:
        target = wide if target == tight else tight
    return ((Fraction(0), target, "hold"),)


def _change(a: float, b: float) -> float:
    return 1.0 if b <= 0 else abs(a - b) / max(a, b)


def _moving(
    name: str, duration: Fraction, wide: float, mid: float, tight: float, last: float
) -> tuple[Key, ...] | None:
    zero = Fraction(0)
    if name == "push" and duration >= _MIN_SLOW and last != wide:
        return ((zero, wide, "hold"), (duration, tight if duration > 4 else mid, "ease_in_out"))
    if name == "snap" and duration >= _MIN_SNAP and last != wide:
        return ((zero, wide, "hold"), (_SNAP, tight, "ease_out"), (duration, mid, "ease_in_out"))
    if name == "pull" and duration >= _MIN_SLOW and last != tight:
        return ((zero, tight, "hold"), (duration, wide, "ease_out"))
    if name == "snap_out" and duration >= _MIN_SNAP and last != tight:
        return ((zero, tight, "hold"), (_SNAP, wide, "ease_out"), (duration, mid, "ease_in_out"))
    return None


def _covered_spans(video: Sequence[VideoItem]) -> list[tuple[Fraction, Fraction]]:
    """Where cover footage hides the main picture (merged spans of the timeline)."""
    spans = sorted((fr(v.timeline_start), fr(v.timeline_end)) for v in video if v.layer >= 1)
    merged: list[tuple[Fraction, Fraction]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _hidden(item: VideoItem, covers: Sequence[tuple[Fraction, Fraction]]) -> bool:
    a, b = fr(item.timeline_start), fr(item.timeline_end)
    return any(s <= a and b <= e for s, e in covers)


def _last_frame(item: VideoItem, fps: Fraction) -> Fraction:
    """Seconds from the clip's first frame to its last one (a key never lands past the clip)."""
    return Fraction(max(item.frames - 1, 0)) / fps


def frame_screens(
    video: Sequence[VideoItem],
    windows: Mapping[str, tuple[float, float, float, float]],
    style: str = "crop",
    fps: Fraction = Fraction(30),
) -> tuple[VideoItem, ...]:
    """A screen recording shows only its page (``windows``: recording -> x, y, w, h), pushed in
    slowly so the still page keeps moving."""
    if not windows:
        return tuple(video)
    out: list[VideoItem] = []
    for v in video:
        window = windows.get(v.original_asset_id)
        if window is None or v.reframe:
            out.append(v)
            continue
        x, y, w, h = window
        duration = _last_frame(v, fps)
        if style == "card":
            out.append(_card(v, window, duration))
            continue
        pivot = (_pivot(x, w), _pivot(y, h))
        start = 1.0 / max(w, h)
        keys: tuple[Key, ...] = (
            (Fraction(0), start, "hold"),
            (duration, start * _SCREEN_PUSH, "ease_in_out"),
        )
        out.append(_framed(v, keys, pivot, "screen_window"))
    return tuple(out)


def _card(
    item: VideoItem, window: tuple[float, float, float, float], duration: Fraction
) -> VideoItem:
    """The page window as a floating card (strategy ``screen_card``): both keys are the window
    itself; the application fits it to the frame and grows it by ``_CARD_PUSH`` over the clip."""
    x, y, w, h = window
    start = fr(item.source_start)
    keys = tuple(
        CropKey(source_time=_rational(start + at), x=x, y=y, w=w, h=h, interpolation=curve)
        for at, curve in ((Fraction(0), "hold"), (duration, "ease_in_out"))
    )
    reframe = Reframe(
        target="source",
        recommendation_id=f"screen_card:{item.item_id}",
        analysis_id=f"screen_card:{_CARD_PUSH:g}",
        strategy="screen_card",
        keys=keys,
    )
    return replace(item, reframe=(*item.reframe, reframe))


def _pivot(start: float, side: float) -> float:
    return 0.5 if side >= 1.0 else min(max(start / (1.0 - side), 0.0), 1.0)


def _framed(
    item: VideoItem, keys: Sequence[Key], pivot: tuple[float, float], analysis: str
) -> VideoItem:
    if len(keys) == 1 and keys[0][1] <= 1.0:
        return item
    source_start = fr(item.source_start)
    crop = tuple(
        CropKey(
            source_time=_rational(source_start + at),
            **_window(zoom, pivot),
            interpolation=curve,
        )
        for at, zoom, curve in keys
    )
    reframe = Reframe(
        target="source",
        recommendation_id=f"camera:{item.item_id}",
        analysis_id=analysis,
        strategy="camera_move" if len(keys) > 1 else "punch_in",
        keys=crop,
    )
    return replace(item, reframe=(*item.reframe, reframe))


def _window(zoom: float, pivot: tuple[float, float]) -> dict[str, float]:
    """The window a zoom about ``pivot`` shows (normalised, top-left)."""
    side = 1.0 / zoom
    return {
        "x": round(pivot[0] * (1.0 - side), 6),
        "y": round(pivot[1] * (1.0 - side), 6),
        "w": round(side, 6),
        "h": round(side, 6),
    }


def _rational(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)
