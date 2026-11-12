"""The overlay count and the label spans read from built clips (pure, no pipeline)."""

from media_house.core.domain.rational import Rational
from media_house.modules.production_project.infrastructure.timeline_source import (
    _is_label,
    _is_overlay,
    _max_overlap,
)
from media_house.modules.timeline_builder.application.contracts import ClipKind, VideoClip


def clip(kind: ClipKind, start: int, end: int, *, zone: str = "") -> VideoClip:
    return VideoClip(
        clip_id=f"c{start}-{end}",
        track_id="t",
        kind=kind,
        name="x",
        source=None,
        timeline_start=Rational(start, 25),
        timeline_end=Rational(end, 25),
        start_frame=start,
        end_frame=end,
        zone=zone,
    )


def test_back_to_back_clips_do_not_overlap_but_stacked_ones_do() -> None:
    assert _max_overlap([clip(ClipKind.GRAPHIC, 0, 50), clip(ClipKind.GRAPHIC, 50, 100)]) == 1
    stacked = [clip(ClipKind.GRAPHIC, 0, 100), clip(ClipKind.GRAPHIC, 40, 60)]
    assert _max_overlap([*stacked, clip(ClipKind.CUSTOM, 45, 55)]) == 3
    assert _max_overlap([]) == 0


def test_the_label_is_found_by_its_zone_and_is_no_overlay() -> None:
    label = clip(ClipKind.GRAPHIC, 0, 100, zone="sponsor_label")

    assert _is_label(label)
    assert not _is_overlay(label)
    assert _is_overlay(clip(ClipKind.GRAPHIC, 0, 10))
    assert not _is_overlay(clip(ClipKind.CAPTION, 0, 10))
    assert not _is_overlay(clip(ClipKind.PRIMARY, 0, 10))
