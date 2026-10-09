"""Positions in the picture: normalised boxes and their relations.

Convention (documented once, used everywhere): a position is a fraction (0-1) of the picture in
DISPLAY orientation (rotation metadata applied, pixel aspect corrected); x grows to the right and
y grows downwards, so (0, 0) is the top-left corner.
"""

from dataclasses import dataclass

from media_house.shared.errors import InvariantViolation

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class BBox:
    """An axis-aligned box in normalised picture coordinates; ``x0 < x1`` and ``y0 < y1``."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        inside = all(-_EPSILON <= v <= 1 + _EPSILON for v in (self.x0, self.y0, self.x1, self.y1))
        if not inside or self.x1 <= self.x0 or self.y1 <= self.y0:
            raise InvariantViolation(
                "A box lies within the 0-1 picture and has a positive size",
                details={"box": (self.x0, self.y0, self.x1, self.y1)},
            )

    @classmethod
    def clamped(cls, x0: float, y0: float, x1: float, y1: float) -> "BBox | None":
        """The box limited to the picture, or ``None`` when nothing of it is inside."""
        cx0, cy0 = min(max(x0, 0.0), 1.0), min(max(y0, 0.0), 1.0)
        cx1, cy1 = min(max(x1, 0.0), 1.0), min(max(y1, 0.0), 1.0)
        if cx1 - cx0 <= _EPSILON or cy1 - cy0 <= _EPSILON:
            return None
        return cls(cx0, cy0, cx1, cy1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


def intersection_area(a: BBox, b: BBox) -> float:
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    return width * height if width > 0 and height > 0 else 0.0


def iou(a: BBox, b: BBox) -> float:
    """Intersection over union (0 for disjoint boxes)."""
    shared = intersection_area(a, b)
    return shared / (a.area + b.area - shared) if shared > 0 else 0.0


def union(boxes: "list[BBox] | tuple[BBox, ...]") -> BBox | None:
    """The smallest box containing every box, or ``None`` for no boxes."""
    if not boxes:
        return None
    return BBox(
        min(b.x0 for b in boxes),
        min(b.y0 for b in boxes),
        max(b.x1 for b in boxes),
        max(b.y1 for b in boxes),
    )
