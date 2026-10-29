"""Crop geometry is arithmetic: these tests prove the arithmetic, on synthetic sizes only."""

import pytest

from media_house.core.domain import Rational
from media_house.modules.reframing_intelligence.domain.geometry import (
    Box,
    Crop,
    SourceGeometry,
    TargetFormat,
    aspect_error,
    crop_around,
    crop_problems,
    effective_scale,
    interpolate,
    widest_crop,
)
from media_house.modules.reframing_intelligence.domain.model import CropKeyframe, crop_at
from media_house.modules.reframing_intelligence.domain.values import ASPECT_TOLERANCE, Interpolation
from media_house.shared.errors import InvariantViolation
from tests.support.refr_fakes import T11, T169, T916, geom


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ((1920, 1080), T916, (0.31640625, 1.0)),  # landscape -> vertical: full height
        ((1920, 1080), T11, (0.5625, 1.0)),  # landscape -> square: full height
        ((1920, 1080), T169, (1.0, 1.0)),  # already the target's shape: the whole frame
        ((1080, 1920), T169, (1.0, 0.31640625)),  # portrait -> landscape: full width
        ((1080, 1920), T916, (1.0, 1.0)),
        ((1080, 1080), T916, (0.5625, 1.0)),
    ],
)
def test_widest_crop_keeps_the_target_shape_and_uses_the_full_side(
    source: tuple[int, int], target: TargetFormat, expected: tuple[float, float]
) -> None:
    crop = widest_crop(geom(*source), target)

    assert crop == pytest.approx(expected)
    window = crop_around(geom(*source), target, 1.0, 0.5, 0.5)
    assert aspect_error(window, geom(*source), target) < ASPECT_TOLERANCE


def test_a_crop_is_never_stretched_at_any_zoom_or_position() -> None:
    source = geom(2560, 1080)
    for target in (T169, T916, T11, TargetFormat.of("4:5")):
        for zoom in (1.0, 0.8, 0.5):
            for cx in (0.0, 0.3, 1.0):
                crop = crop_around(source, target, zoom, cx, 0.5)
                assert crop_problems(crop, source, target) == []


def test_a_crop_outside_the_picture_or_of_the_wrong_shape_is_refused() -> None:
    with pytest.raises(InvariantViolation):
        Crop(0.8, 0.0, 0.5, 1.0)
    with pytest.raises(InvariantViolation):
        Crop(0.0, 0.0, 0.0, 1.0)
    wrong = Crop(0.0, 0.0, 0.5, 0.5)  # a 1:1 window of a 16:9 picture is NOT 9:16

    assert crop_problems(wrong, geom(), T916)


def test_a_box_must_lie_in_the_picture() -> None:
    with pytest.raises(InvariantViolation):
        Box(0.5, 0.2, 0.4, 0.8)
    with pytest.raises(InvariantViolation):
        Box(-0.2, 0.2, 0.4, 0.8)


def test_target_formats_derive_or_refuse_a_resolution() -> None:
    assert (T916.width, T916.height) == (1080, 1920)
    assert (TargetFormat.of("4:5", height=1350).width) == 1080
    assert TargetFormat.of("21:9").width == 1920
    with pytest.raises(InvariantViolation):
        TargetFormat.of("nonsense")
    with pytest.raises(InvariantViolation):
        TargetFormat("9:16", Rational(9, 16), 1920, 1080)  # the pixels do not have the shape


def test_effective_scale_counts_stored_samples_not_display_pixels() -> None:
    full = Crop(0.0, 0.0, 1.0, 1.0)
    square = geom(1920, 1080)
    # 1920x1080 source cropped to 9:16 (607.5 x 1080 samples) shown at 1080x1920: 1.78x enlargement
    crop = crop_around(square, T916, 1.0, 0.5, 0.5)
    assert effective_scale(crop, square, T916) == pytest.approx(1920 / 1080, rel=0.001)
    # the whole frame at its own size is one to one
    assert effective_scale(full, square, T169) == pytest.approx(1.0)
    # a 4K source gives the same crop real headroom (no enlargement)
    assert effective_scale(full, geom(3840, 2160), T169) == pytest.approx(0.5)


def test_pixel_shape_shows_up_as_enlargement_on_one_axis() -> None:
    # 1440x1080 stored, pixel shape 4:3 -> 1920x1080 displayed: the whole frame is still 1.33x wide
    anamorphic = geom(1920, 1080, samples=(1440, 1080))

    assert effective_scale(Crop(0, 0, 1, 1), anamorphic, T169) == pytest.approx(1920 / 1440)


def test_rotation_swaps_the_stored_axes_into_display_orientation() -> None:
    # stored 1080x1920 with a 90 degree rotation tag is displayed 1920x1080; the stored samples in
    # display orientation are 1920 across and 1080 down
    rotated = SourceGeometry(1920, 1080, 1920, 1080, 90, True)

    assert effective_scale(Crop(0, 0, 1, 1), rotated, T169) == pytest.approx(1.0)
    with pytest.raises(InvariantViolation):
        SourceGeometry(1920, 1080, 1920, 1080, 45, True)


def test_keyframes_hold_or_move_linearly_and_keep_the_aspect() -> None:
    source = geom()
    a = crop_around(source, T916, 1.0, 0.2, 0.5)
    b = crop_around(source, T916, 0.8, 0.7, 0.5)
    keys = (
        CropKeyframe(time=Rational(0, 1), crop=a, interpolation=Interpolation.LINEAR),
        CropKeyframe(time=Rational(4, 1), crop=b, interpolation=Interpolation.HOLD),
    )

    assert crop_at(keys, -1.0) == a  # before the first keyframe: its crop
    assert crop_at(keys, 0.0) == a
    middle = crop_at(keys, 2.0)
    assert middle == interpolate(a, b, 0.5)
    assert crop_problems(middle, source, T916) == []  # linear w and h keep the aspect exact
    assert crop_at(keys, 4.0) == b
    assert crop_at(keys, 9.0) == b  # after the last keyframe: its crop holds

    held = (
        CropKeyframe(time=Rational(0, 1), crop=a, interpolation=Interpolation.HOLD),
        CropKeyframe(time=Rational(4, 1), crop=b, interpolation=Interpolation.HOLD),
    )
    assert crop_at(held, 3.9) == a  # hold keeps the crop until the next keyframe, then jumps
