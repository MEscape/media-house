"""The auto grade and the tolerance for Resolve's own rounding when reading clips back."""

from media_house.modules.resolve_adapter.domain.grade import auto_grade
from media_house.modules.resolve_adapter.domain.reconcile import _close


def test_a_warm_picture_is_cooled_half_way_and_lifted_a_touch() -> None:
    grade = auto_grade((0.6, 0.5, 0.4))
    assert grade is not None
    red, green, blue = grade.slope
    assert red < green < blue  # the cast is reduced, not inverted
    assert 0.9 < red < 1.0 and 1.1 < blue < 1.2
    assert grade.offset[0] < 0 and grade.saturation == 1.05


def test_a_black_picture_gets_no_grade() -> None:
    assert auto_grade((0.0, 0.01, 0.0)) is None


def test_a_conformed_clip_one_frame_off_is_not_a_persons_change() -> None:
    assert _close("src_start", "301", "300")
    assert not _close("src_start", "303", "300")
    assert _close("prop:ZoomX", "1.1250", "1.125")
    assert not _close("asset", "b", "a")
