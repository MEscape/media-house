"""Parts of the source picture that must not be on screen (a wall of the set, a clutter corner).

A rule is a colour band (hue, saturation, value). The picture analysis stores the mean colour of
each cell of a 32 x 18 grid per shot; a cell whose mean colour falls in a band is FLAGGED. The
window
is the widest one (zoom 1 is the widest the shape allows, smaller is tighter) that shows no flagged
cell and keeps what the shot requires inside, with the composition margin. Nothing is guessed: with
no rule or no grid nothing is flagged, and when no window hides every flagged cell the result is
``None`` so the planner reports it instead of cropping into the subject.
"""

import colorsys
from dataclasses import dataclass

from media_house.modules.reframing_intelligence.domain.geometry import Box

GRID_COLUMNS, GRID_ROWS = 32, 18
#: The tightest window looked at (zoom): beyond it the picture would be enlarged too much to serve.
MIN_ZOOM = 0.5
_ZOOM_STEP = 0.02
_POSITION_STEPS = 64
#: A window may overlap a flagged cell by this share of the cell (rounding of the window edge).
_OVERLAP_ALLOWED = 0.02


@dataclass(frozen=True, slots=True)
class KeepOut:
    """A colour that must not be visible: hue range in degrees, minimum saturation and value."""

    name: str
    hue_min: float
    hue_max: float
    min_saturation: float = 0.25
    min_value: float = 0.15

    def problem(self) -> str | None:
        if not (0 <= self.hue_min <= self.hue_max <= 360):
            return f"keep-out {self.name!r}: the hue range must lie within 0-360 degrees"
        if not (0 <= self.min_saturation <= 1 and 0 <= self.min_value <= 1):
            return f"keep-out {self.name!r}: saturation and value are fractions"
        return None

    def matches(self, red: int, green: int, blue: int) -> bool:
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        return (
            self.hue_min <= hue * 360 <= self.hue_max
            and saturation >= self.min_saturation
            and value >= self.min_value
        )


KEEP_OUT_PRESETS: dict[str, KeepOut] = {
    "green": KeepOut("green", 50.0, 170.0),
    "blue": KeepOut("blue", 190.0, 260.0),
    "red": KeepOut("red", 345.0, 360.0),
}


#: The subject's sideways extent over the shot is trimmed to this share of the moments, then
#: shrunk by ``_CORE_SHRINK`` of its width per side: a lean or a raised arm that reaches the wall
#: for a
#: moment must not decide a window that is fixed for the whole shot.
_TRIM = 0.10
_CORE_SHRINK = 0.08


def core_extent(boxes: list[Box]) -> Box | None:
    """The subject's core over a shot: the trimmed extent of its boxes, a little smaller."""
    if not boxes:
        return None

    def trimmed(values: list[float], high: bool) -> float:
        ordered = sorted(values)
        at = round((len(ordered) - 1) * ((1 - _TRIM) if high else _TRIM))
        return ordered[at]

    x0 = trimmed([b.x0 for b in boxes], False)
    x1 = trimmed([b.x1 for b in boxes], True)
    # Sideways a lean or an arm may be given up; the head is never: the top of the subject is its
    # highest point over the whole shot, with no shrinking.
    y0 = min(b.y0 for b in boxes)
    y1 = trimmed([b.y1 for b in boxes], True)
    dx = (x1 - x0) * _CORE_SHRINK
    return Box(x0 + dx, y0, x1 - dx, y1)


@dataclass(frozen=True, slots=True)
class KeepOutWindow:
    zoom: float
    x: float
    y: float
    #: Flagged cells there were, and the rule names that flagged them.
    flagged: int
    names: tuple[str, ...]


def flagged_cells(
    grid: tuple[int, ...] | None, rules: tuple[KeepOut, ...]
) -> tuple[frozenset[tuple[int, int]], tuple[str, ...]]:
    """The (row, column) cells whose mean colour a rule forbids, and which rules flagged any."""
    if not grid or not rules or len(grid) != GRID_ROWS * GRID_COLUMNS * 3:
        return frozenset(), ()
    cells: set[tuple[int, int]] = set()
    names: set[str] = set()
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            at = (row * GRID_COLUMNS + column) * 3
            for rule in rules:
                if rule.matches(grid[at], grid[at + 1], grid[at + 2]):
                    cells.add((row, column))
                    names.add(rule.name)
    # A wall is a connected area: a lone cell (a shadow, a reflection) is not one.
    connected = {
        (r, c)
        for r, c in cells
        if any(
            (r + dr, c + dc) in cells
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            if (dr, dc) != (0, 0)
        )
    }
    return frozenset(connected), tuple(sorted(names)) if connected else ()


def with_margin(cells: frozenset[tuple[int, int]]) -> frozenset[tuple[int, int]]:
    """The flagged cells and the cells next to them: the edge of a wall is lit and blended, so
    the neighbours still show a sliver of it (which reads as a mistake at the picture's edge)."""
    return frozenset(
        (r + dr, c + dc)
        for r, c in cells
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if 0 <= r + dr < GRID_ROWS and 0 <= c + dc < GRID_COLUMNS
    )


def _shows_flagged(
    cells: frozenset[tuple[int, int]], x: float, y: float, w: float, h: float
) -> bool:
    cell_w, cell_h = 1 / GRID_COLUMNS, 1 / GRID_ROWS
    for row, column in cells:
        x0, y0 = column * cell_w, row * cell_h
        overlap_x = min(x + w, x0 + cell_w) - max(x, x0)
        overlap_y = min(y + h, y0 + cell_h) - max(y, y0)
        if (
            overlap_x > 0
            and overlap_y > 0
            and overlap_x * overlap_y > _OVERLAP_ALLOWED * cell_w * cell_h
        ):
            return True
    return False


def find_window(
    cells: frozenset[tuple[int, int]],
    names: tuple[str, ...],
    union: Box | None,
    w0: float,
    h0: float,
    margin: float,
) -> KeepOutWindow | None:
    """The widest window that shows no flagged cell and keeps ``union`` inside, else ``None``."""
    if not cells:
        return None
    zoom = 1.0
    while zoom >= MIN_ZOOM - 1e-9:
        w, h = w0 * zoom, h0 * zoom
        # The composition margin is a preference (a subject standing at the picture's edge cannot
        # have it): without it the subject only has to be inside.
        found = _position(cells, union, w, h, margin) or _position(cells, union, w, h, 0.0)
        if found is not None:
            return KeepOutWindow(round(zoom, 4), found[0], found[1], len(cells), names)
        zoom -= _ZOOM_STEP
    return None


def _position(
    cells: frozenset[tuple[int, int]], union: Box | None, w: float, h: float, margin: float
) -> tuple[float, float] | None:
    want_x = (union.x0 + union.x1) / 2 - w / 2 if union else (1 - w) / 2
    want_y = (union.y0 + union.y1) / 2 - h / 2 if union else (1 - h) / 2
    best: tuple[float, float, float] | None = None
    for i in range(_POSITION_STEPS + 1):
        x = (1 - w) * i / _POSITION_STEPS
        if union and not (x + margin * w <= union.x0 and union.x1 <= x + w - margin * w):
            continue
        for j in range(_POSITION_STEPS + 1):
            y = (1 - h) * j / _POSITION_STEPS
            if union and not (y + margin * h <= union.y0 and union.y1 <= y + h - margin * h):
                continue
            if _shows_flagged(cells, x, y, w, h):
                continue
            distance = (x - want_x) ** 2 + (y - want_y) ** 2
            if best is None or distance < best[0]:
                best = (distance, x, y)
    return None if best is None else (best[1], best[2])
