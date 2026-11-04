"""Exact time maps: the clip keeps its timeline span, every boundary is on the frame grid and
every source time follows from ``start + rate x (t - t0)``."""

from fractions import Fraction

from media_house.modules.vfx_engine.domain.facts import Box, fr
from media_house.modules.vfx_engine.domain.model import Mask, MaskKeyframe
from media_house.modules.vfx_engine.domain.timing import (
    box_at,
    crop_window,
    map_problems,
    retime_map,
    reverse_map,
)
from media_house.modules.vfx_engine.domain.values import (
    AudioIntent,
    Interpolation,
    MaskKind,
    SegmentKind,
)
from tests.support.vfx_fakes import q

FPS = Fraction(30)


def slow(**kw: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "timeline_start": Fraction(10),
        "timeline_end": Fraction(14),
        "source_start": Fraction(0),
        "peak": Fraction(2),
        "fps": FPS,
        "rate": Fraction(1, 2),
        "pre_roll": Fraction(2, 5),
        "post_roll": Fraction(6, 5),
        "freeze": Fraction(3, 5),
        "ramp": False,
        "interpolation": Interpolation.BLEND,
        "audio": AudioIntent.KEEP_ORIGINAL,
    }
    args.update(kw)
    return retime_map(**args)  # type: ignore[arg-type]


def test_slow_motion_and_freeze_keep_the_span_and_are_exact() -> None:
    m = slow()

    assert map_problems(m, Fraction(10), Fraction(14)) == []
    kinds = [s.kind for s in m.segments]
    assert kinds == [SegmentKind.NORMAL, SegmentKind.SLOW, SegmentKind.FREEZE, SegmentKind.SLOW]
    normal, before, hold, _after = m.segments
    assert fr(normal.source_end) == Fraction(8, 5)  # 0.4 s before the peak
    assert fr(before.source_end) == 2  # the slow part arrives exactly at the burst
    assert fr(hold.source_start) == fr(hold.source_end) == 2 and fr(hold.rate) == 0
    assert hold.end_frame - hold.start_frame == 18  # 0.6 s at 30 fps
    assert fr(m.source_used_end) < 4  # less source is shown; the rest is recorded as unused
    assert normal.audio is AudioIntent.SOURCE_SYNC and before.audio is AudioIntent.KEEP_ORIGINAL


def test_frames_are_rounded_half_to_even_on_exact_times() -> None:
    m = slow(timeline_start=Fraction(1, 60), timeline_end=Fraction(121, 60))

    assert m.segments[0].start_frame == 0  # 0.5 frame -> 0 (half to even)
    assert map_problems(m, Fraction(1, 60), Fraction(121, 60)) == []


def test_a_freeze_alone_holds_the_peak_and_resumes_at_speed_one() -> None:
    m = slow(rate=Fraction(1), freeze=Fraction(1, 2))

    assert [s.kind for s in m.segments] == [
        SegmentKind.NORMAL,
        SegmentKind.FREEZE,
        SegmentKind.NORMAL,
    ]
    assert m.interpolation is Interpolation.DUPLICATE
    assert fr(m.segments[2].source_start) == 2


def test_a_ramp_steps_down_to_the_slow_rate_instead_of_jumping() -> None:
    m = slow(ramp=True)

    rates = [fr(s.rate) for s in m.segments if s.kind is SegmentKind.SLOW]
    assert rates[0] == Fraction(3, 4) and rates[1] == Fraction(1, 2)
    assert map_problems(m, Fraction(10), Fraction(14)) == []


def test_a_peak_near_the_clip_end_is_cut_at_the_end() -> None:
    m = slow(peak=Fraction(39, 10))

    assert fr(m.segments[-1].timeline_end) == 14
    assert map_problems(m, Fraction(10), Fraction(14)) == []


def test_a_clip_end_off_the_frame_grid_is_still_covered_exactly() -> None:
    ntsc = Fraction(30000, 1001)  # 60 s is frame 1798.2: its snap (1798) falls short of the end
    m = slow(timeline_start=Fraction(40), timeline_end=Fraction(60), fps=ntsc, peak=Fraction(6))

    assert fr(m.segments[-1].timeline_end) == 60
    assert map_problems(m, Fraction(40), Fraction(60)) == []


def test_reverse_replays_the_last_seconds_backwards() -> None:
    m = reverse_map(
        timeline_start=Fraction(0),
        timeline_end=Fraction(5),
        source_start=Fraction(0),
        at=Fraction(2),
        length=Fraction(1),
        fps=FPS,
        audio=AudioIntent.MUTE,
    )

    assert [s.kind for s in m.segments] == [
        SegmentKind.NORMAL,
        SegmentKind.REVERSE,
        SegmentKind.NORMAL,
    ]
    back = m.segments[1]
    assert (fr(back.source_start), fr(back.source_end)) == (Fraction(1), Fraction(2))
    assert map_problems(m, Fraction(0), Fraction(5)) == []


def test_a_mask_interpolates_holds_and_covers_fallback_spans_with_the_whole_picture() -> None:
    a, b = Box(x0=0.1, y0=0.1, x1=0.2, y1=0.2), Box(x0=0.3, y0=0.1, x1=0.4, y1=0.2)
    mask = Mask(
        mask_id="m",
        kind=MaskKind.TRACKED,
        keyframes=(
            MaskKeyframe(source_time=q(1.0), box=a),
            MaskKeyframe(source_time=q(2.0), box=b),
        ),
        fallback_spans=((q(1.5), q(1.6)),),
    )

    mid = box_at(mask, Fraction(5, 4))
    assert mid is not None and abs(mid.x0 - 0.15) < 1e-9
    assert box_at(mask, Fraction(31, 20)) == Box(x0=0, y0=0, x1=1, y1=1)
    assert box_at(mask, Fraction(1, 2)) is None  # outside its keyframes the mask is off


def test_a_crop_window_stays_inside_the_picture() -> None:
    w = crop_window(1.25, 0.95, 0.05)

    assert w.x1 <= 1 and w.y0 >= 0 and abs(w.width - 0.8) < 1e-9
