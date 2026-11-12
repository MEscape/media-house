"""Camera-move rules, focus blur and full-frame backgrounds (pure domain, no fixtures)."""

from fractions import Fraction
from itertools import pairwise

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.facts import AssetFacts
from media_house.modules.edit_plan_compiler.domain.focus import backgrounds, focus_blur
from media_house.modules.edit_plan_compiler.domain.model import OverlayItem, VideoItem
from media_house.modules.edit_plan_compiler.domain.punch import (
    camera_beats,
    camera_moves,
    frame_screens,
)
from media_house.modules.edit_plan_compiler.domain.timing import fr
from media_house.modules.edit_plan_compiler.domain.values import (
    FULL_FRAME_ZONE,
    Composite,
    Execution,
    ItemOrigin,
)

FPS = Fraction(30)


def r(value: Fraction | int | float) -> Rational:
    f = Fraction(value)
    return Rational(f.numerator, f.denominator)


def clip(
    item_id: str, start: float, end: float, *, layer: int = 0, asset: str = "talk"
) -> VideoItem:
    return VideoItem(
        item_id=item_id,
        unit_id="u",
        clip_id=item_id,
        track_id="",
        origin=ItemOrigin.EDIT,
        asset_id=asset,
        original_asset_id=asset,
        source_start=r(start + 100),
        source_end=r(end + 100),
        source_start_frame=None,
        timeline_start=r(Fraction(start)),
        timeline_end=r(Fraction(end)),
        start_frame=round(start * 30),
        end_frame=round(end * 30),
        speed=Rational(1, 1),
        layer=layer,
        section_id=None,
        beat_id=None,
    )


def overlay(item_id: str, kind: str, start: float, end: float, zone: str = "") -> OverlayItem:
    return OverlayItem(
        item_id=item_id,
        unit_id="g",
        origin=ItemOrigin.GRAPHICS,
        source_ref=item_id,
        kind=kind,
        track_id="",
        asset_id="mov",
        asset_start_frame=0,
        timeline_start=r(Fraction(start)),
        timeline_end=r(Fraction(end)),
        start_frame=round(start * 30),
        end_frame=round(end * 30),
        composite=Composite.ALPHA_OVER,
        execution=Execution.RENDERED,
        zone=zone,
    )


def zoom_of(key_w: float) -> float:
    return 1 / key_w


class TestCameraMoves:
    def test_every_jump_cut_changes_the_framing_and_keys_stay_on_the_clip(self) -> None:
        video = [clip(f"c{i}", i * 3.0, i * 3.0 + 3.0) for i in range(8)]
        moved = {v.item_id: v for v in camera_moves(video, 1.15, FPS)}
        last = None
        for v in video:
            keys = moved[v.item_id].reframe[0].keys if moved[v.item_id].reframe else ()
            first = zoom_of(keys[0].w) if keys else 1.0
            end = zoom_of(keys[-1].w) if keys else 1.0
            if last is not None:
                assert abs(first - last) / max(first, last) >= 0.06
            for k in keys:  # never past the clip's last frame
                assert fr(k.source_time) <= fr(v.source_end) - Fraction(1, 30)
            last = end

    def test_a_clip_hidden_under_cover_footage_gets_no_move(self) -> None:
        video = [clip("a", 0, 3), clip("b", 3, 6), clip("cover", 2.5, 6.5, layer=1, asset="broll")]
        moved = {v.item_id: v for v in camera_moves(video, 1.15, FPS)}
        assert moved["b"].reframe == ()

    def test_a_card_keeps_the_whole_page_window(self) -> None:
        (v,) = frame_screens(
            [clip("s", 0, 5, asset="screen")], {"screen": (0.1, 0.1, 0.8, 0.8)}, "card", FPS
        )
        (reframe,) = v.reframe
        assert reframe.strategy == "screen_card"
        assert {(k.x, k.y, k.w, k.h) for k in reframe.keys} == {(0.1, 0.1, 0.8, 0.8)}


class TestCameraBeats:
    def test_a_long_take_changes_framing_every_beat(self) -> None:
        (v,) = camera_beats([clip("a", 0, 10)], 1.3, Fraction(11, 5), FPS)
        (reframe,) = v.reframe
        keys = [(float(fr(k.source_time) - 100), round(zoom_of(k.w), 3)) for k in reframe.keys]
        # a fast punch (0.25 s) at every beat; no stretch of more than one beat without one
        punches = [t for (t, _), (t2, _) in pairwise(keys) if t2 - t <= 0.25]
        assert punches == [2.2, 4.4, 6.6, 8.8]
        assert max(z for _, z in keys) <= 1.3 * 1.03 + 1e-6
        for k in reframe.keys:
            assert fr(k.source_time) <= fr(v.source_end) - Fraction(1, 30)

    def test_a_cut_between_two_shots_zooms_through(self) -> None:
        video = [clip("a", 0, 4), clip("b", 4, 8, asset="other")]
        moved = {v.item_id: v for v in camera_beats(video, 1.3, Fraction(11, 5), FPS)}
        out_keys = moved["a"].reframe[0].keys
        in_keys = moved["b"].reframe[0].keys
        assert round(zoom_of(out_keys[-1].w), 3) == 1.42  # punches in to the cut
        assert out_keys[-1].interpolation == "ease_in"
        assert round(zoom_of(in_keys[0].w), 3) == 1.42  # and snaps out of it after the cut
        assert in_keys[1].interpolation == "ease_out"

    def test_jump_cuts_continue_the_sequence(self) -> None:
        video = [clip("a", 0, 1.5), clip("b", 1.5, 3)]
        moved = {v.item_id: v for v in camera_beats(video, 1.3, Fraction(11, 5), FPS)}
        first = zoom_of(moved["a"].reframe[0].keys[-1].w)
        second = zoom_of(moved["b"].reframe[0].keys[0].w) if moved["b"].reframe else 1.0
        assert abs(first - second) / max(first, second) >= 0.06

    def test_off_without_a_beat(self) -> None:
        video = [clip("a", 0, 10)]
        assert camera_beats(video, 1.3, Fraction(0), FPS) == tuple(video)


class TestFocusBlur:
    def test_the_picture_blurs_in_fast_and_out_fast_around_a_focus_graphic(self) -> None:
        video = [clip("a", 0, 10)]
        (v,) = focus_blur(video, [overlay("chart", "chart", 2, 6)], 6.0, FPS)
        times = [float(fr(k.source_time) - 100) for k in v.blur]
        sizes = [k.size for k in v.blur]
        assert times == [2.0, 2.25, 5.8, 6.0]
        assert sizes == [0.0, 6.0, 6.0, 0.0]
        assert [k.interpolation for k in v.blur][1::2] == ["ease_out", "ease_in"]

    def test_small_graphics_and_full_frame_cards_never_blur(self) -> None:
        video = [clip("a", 0, 10)]
        cards = [
            overlay("tag", "price_tag", 1, 3),
            overlay("full", "spec_card", 4, 8, FULL_FRAME_ZONE),
        ]
        (v,) = focus_blur(video, cards, 6.0, FPS)
        assert v.blur == ()


def test_each_full_frame_card_gets_its_own_part_of_the_background() -> None:
    footage = AssetFacts(asset_id="bg", exists=True, duration=Rational(200, 1))
    cards = [
        overlay("one", "spec_card", 1, 6, FULL_FRAME_ZONE),
        overlay("two", "spec_card", 10, 15, FULL_FRAME_ZONE),
    ]
    first, second = backgrounds(cards, footage)
    assert (first.layer, first.asset_id) == (1, "bg")
    assert fr(first.source_start) != fr(second.source_start)
    assert fr(second.source_end) - fr(second.source_start) == 5
