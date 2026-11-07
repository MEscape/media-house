"""A colour the picture must not show (the green wall of a set): the window crops in until it is gone."""

from dataclasses import replace

import pytest

from media_house.modules.reframing_intelligence.domain.geometry import Box
from media_house.modules.reframing_intelligence.domain.keepout import (
    GRID_COLUMNS,
    GRID_ROWS,
    KEEP_OUT_PRESETS,
    KeepOut,
    find_window,
    flagged_cells,
)
from media_house.modules.reframing_intelligence.domain.model import crop_at
from media_house.modules.reframing_intelligence.domain.values import IssueKind, Strategy
from tests.support.refr_fakes import T169, geom, plan, profile, shot, still

GREEN, PINK, GREY = (110, 190, 60), (210, 170, 180), (120, 120, 120)
PERSON = (0.34, 0.30, 0.58, 0.98)


def grid(*, green_columns: int = 0, green_rows: int = 0) -> tuple[int, ...]:
    """A wall: green for the first columns (in sixteenths, all rows) and the first rows (ninths)."""
    cells: list[int] = []
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            hit = column < green_columns * GRID_COLUMNS // 16 or row < green_rows * GRID_ROWS // 9
            cells.extend(GREEN if hit else PINK if column * 16 // GRID_COLUMNS > 10 else GREY)
    return tuple(cells)


def test_a_cell_is_flagged_by_the_colour_band_only() -> None:
    rule = KEEP_OUT_PRESETS["green"]

    assert rule.matches(*GREEN) and not rule.matches(*PINK) and not rule.matches(*GREY)
    assert not rule.matches(5, 20, 5)  # too dark to be a wall


def test_no_rule_or_no_grid_flags_nothing() -> None:
    assert flagged_cells(grid(green_columns=4), ())[0] == frozenset()
    assert flagged_cells(None, (KEEP_OUT_PRESETS["green"],))[0] == frozenset()
    assert flagged_cells((1, 2, 3), (KEEP_OUT_PRESETS["green"],))[0] == frozenset()  # wrong size


def test_the_window_crops_in_until_no_flagged_cell_is_visible_and_keeps_the_subject() -> None:
    cells, names = flagged_cells(grid(green_columns=4), (KEEP_OUT_PRESETS["green"],))

    found = find_window(cells, names, Box(*PERSON), 1.0, 1.0, 0.03)

    assert found is not None and names == ("green",)
    assert found.zoom <= 0.75 + 1e-9
    assert found.x >= 4 / 16 - 1e-3  # the first four columns are green
    assert found.x <= PERSON[0] and PERSON[2] <= found.x + found.zoom


def test_a_subject_standing_in_the_wall_cannot_be_framed_without_it() -> None:
    cells, names = flagged_cells(grid(green_columns=6), (KEEP_OUT_PRESETS["green"],))
    person_in_wall = Box(0.05, 0.2, 0.40, 0.98)

    assert find_window(cells, names, person_in_wall, 1.0, 1.0, 0.03) is None


def test_a_16_9_shot_of_a_set_with_a_green_wall_is_cropped_in_instead_of_left_alone() -> None:
    wall = grid(green_columns=4)
    kept = plan(
        shot(tracks=(still("p", PERSON),), color_grid=wall),
        target=T169,
        geometry=geom(3840, 2160),
        prof=replace(profile(), keep_out=(KEEP_OUT_PRESETS["green"],)),
    )
    plain = plan(
        shot(tracks=(still("p", PERSON),), color_grid=wall), target=T169, geometry=geom(3840, 2160)
    )

    assert plain.strategy is Strategy.ORIGINAL
    assert kept.strategy is not Strategy.ORIGINAL and kept.executable
    crop = crop_at(kept.keyframes, 1.0)
    assert crop.x >= 0.25 - 1e-3 and crop.w <= 0.75 + 1e-6
    assert crop.inside_share(Box(*PERSON)) == pytest.approx(1.0)
    assert any("green" in r for r in kept.reasons)


def test_when_no_window_can_hide_the_wall_the_shot_is_reported_not_cropped_into_the_person() -> (
    None
):
    p = plan(
        shot(tracks=(still("p", (0.05, 0.2, 0.40, 0.98)),), color_grid=grid(green_columns=6)),
        target=T169,
        geometry=geom(3840, 2160),
        prof=replace(profile(), keep_out=(KEEP_OUT_PRESETS["green"],)),
    )

    assert any(i.kind is IssueKind.KEEP_OUT for i in p.issues)
    assert crop_at(p.keyframes, 1.0).inside_share(Box(0.05, 0.2, 0.40, 0.98)) == pytest.approx(1.0)


def test_a_rule_is_checked_when_the_configuration_is_resolved() -> None:
    from media_house.modules.reframing_intelligence.domain.errors import InvalidReframingConfig
    from media_house.modules.reframing_intelligence.domain.profile import ReframingConfig

    with pytest.raises(InvalidReframingConfig):
        ReframingConfig(keep_out=(KeepOut("bad", 200, 100),)).resolve()


def test_a_lone_flagged_cell_is_not_a_wall() -> None:
    cells_of_colour = list(grid(green_columns=4))
    stray = (9 * GRID_COLUMNS + 20) * 3  # one green cell in the middle of the grey wall
    cells_of_colour[stray : stray + 3] = GREEN
    cells, _ = flagged_cells(tuple(cells_of_colour), (KEEP_OUT_PRESETS["green"],))

    assert (9, 20) not in cells and (9, 0) in cells
    assert find_window(cells, ("green",), Box(*PERSON), 1.0, 1.0, 0.03) is not None
