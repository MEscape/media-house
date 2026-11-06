"""What a person's change in Resolve becomes in the builder's own vocabulary of edits - and what
cannot become one (it stays in Resolve, reported, never touched)."""

from fractions import Fraction

import pytest

from media_house.modules.resolve_adapter.application.sync_from_resolve import _edit, _track_ids
from media_house.modules.resolve_adapter.domain.binding import ElementBinding
from media_house.modules.resolve_adapter.domain.live import LiveItem
from media_house.modules.timeline_builder.application.contracts import EditKind
from tests.unit.timeline_builder import fixtures as bf

TIMELINE = bf.built()
FPS = Fraction(25)
TRACKS = _track_ids(TIMELINE)


def binding(element: str, **fields: str) -> ElementBinding:
    base = {
        "v:pA": {"asset": "rec1", "duration": "100", "enabled": "1", "src_start": "250",
                 "start": "0", "track": "video:1"},
        "a:aV": {"asset": "rec1", "duration": "100", "enabled": "1", "src_start": "250",
                 "start": "0", "track": "audio:1"},
        "a:sfx:sfx:1": {"asset": "whoosh1", "duration": "10", "enabled": "1", "src_start": "0",
                        "start": "98", "track": "audio:5"},
    }[element]  # fmt: skip
    base.update(fields)
    kind = "video" if element.startswith("v:") else "audio"
    return ElementBinding(element, kind, "item-1", tuple(sorted(base.items())), "d")


def live(element: str) -> LiveItem:
    return LiveItem(
        item_id="item-1",
        name="x",
        tag=element,
        track_kind="video",
        track_index=1,
        start=0,
        duration=100,
        media_item_id="pool-1",
        left_offset=250,
        enabled=True,
    )


def changes_of(element: str, moved: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    edit, kept = _edit(binding(element), live(element), moved, TIMELINE, FPS, TRACKS)
    return (dict(edit.changes) if edit else {}), kept


def test_the_track_numbers_are_the_translations_so_a_move_between_tracks_is_understood() -> None:
    assert TRACKS["video:1"] == "V1" and TRACKS["video:5"] == "V5" and TRACKS["audio:3"] == "A3"


def test_a_moved_picture_clip_becomes_a_new_start_and_end_frame_only() -> None:
    edit, kept = changes_of("v:pA", {"start": "30"})

    assert edit == {"start_frame": "30", "end_frame": "130"}  # the source range is unchanged
    assert kept == set()


def test_a_trimmed_clip_keeps_its_start_and_changes_its_length_and_out_point() -> None:
    edit, _ = changes_of("v:pA", {"duration": "60"})

    assert edit["start_frame"] == "0" and edit["end_frame"] == "60"
    assert edit["source_in"] == "10/1" and edit["source_out"] == "62/5"  # 10 s + 60 frames


def test_a_new_in_point_is_a_new_source_in() -> None:
    edit, _ = changes_of("v:pA", {"src_start": "275"})

    assert edit["source_in"] == "11/1"


def test_a_moved_sound_clip_is_counted_in_samples_not_frames() -> None:
    edit, _ = changes_of("a:aV", {"start": "25"})

    assert edit["start_sample"] == str(48000) and edit["end_sample"] == str(48000 * 5)


def test_disabling_a_clip() -> None:
    edit, _ = changes_of("v:pA", {"enabled": "0"})

    assert edit == {"enabled": "false"}


def test_a_clip_moved_to_another_track_names_the_builders_track() -> None:
    edit, _ = changes_of("v:pA", {"track": "video:2"})

    assert edit == {"track_id": "V2"}


def test_a_track_the_timeline_does_not_have_stays_in_resolve() -> None:
    edit, kept = changes_of("v:pA", {"track": "video:9"})

    assert edit == {} and kept == {"track"}


def test_opacity_is_a_fraction_in_the_builder() -> None:
    edit, _ = changes_of("v:pA", {"prop:Opacity": "40"})

    assert edit == {"opacity": "0.4"}


def test_what_the_builder_has_no_field_for_stays_in_resolve() -> None:
    edit, kept = changes_of("v:pA", {"prop:ZoomX": "1.4", "asset": "other"})

    assert edit == {} and kept == {"prop:ZoomX", "asset"}


def test_a_retimed_clip_cannot_have_its_in_point_translated() -> None:
    edit, kept = changes_of("a:sfx:sfx:1", {"src_start": "5"})

    assert "source_in" not in edit and kept == {"src_start"}


def test_nothing_representable_means_no_edit() -> None:
    edit, _ = _edit(binding("v:pA"), live("v:pA"), {"prop:Pan": "20"}, TIMELINE, FPS, TRACKS)

    assert edit is None


def test_the_edit_is_a_modify_of_that_element_by_resolve() -> None:
    edit, _ = _edit(binding("v:pA"), live("v:pA"), {"enabled": "0"}, TIMELINE, FPS, TRACKS)

    assert edit is not None
    assert (edit.kind, edit.element_id, edit.editor) == (EditKind.MODIFY, "v:pA", "resolve")


@pytest.mark.parametrize("moved", [{"start": "0"}, {}])
def test_an_unchanged_clip_has_nothing_to_record(moved: dict[str, str]) -> None:
    edit, _ = _edit(binding("v:pA"), live("v:pA"), moved, TIMELINE, FPS, TRACKS)

    assert edit is None or dict(edit.changes)["start_frame"] == "0"
