"""Crop geometry: the only place that knows how a window of the source becomes the output.

Everything here is deterministic arithmetic on normalised positions (see ``values`` for the
coordinate convention). A crop is valid when it lies inside the picture and its aspect, measured
in display pixels, equals the target's within ``ASPECT_TOLERANCE``. The window is scaled
uniformly to the output resolution, so a valid crop is never stretched.

Effective resolution: the crop covers ``w x sample_width`` by ``h x sample_height`` STORED samples
(rotation applied, pixel shape not), and the output has ``target.width x target.height`` pixels.
The scaling ratio is the larger of the two axis ratios (>1 means the picture is enlarged,
1 means one sample per output pixel). A pixel shape other than square therefore shows up as an
enlargement on one axis even for the whole frame, which is true of the footage.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.reframing_intelligence.domain.values import ASPECT_TOLERANCE, EPSILON
from media_house.shared.errors import InvariantViolation

#: Standard output formats (name -> pixels). A custom aspect names its own resolution.
STANDARD_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}
_DEFAULT_LONG_SIDE = 1920
_DECIMALS = 6


def _in_unit(*values: float) -> bool:
    return all(-EPSILON <= v <= 1 + EPSILON for v in values)


def _snap(value: float) -> float:
    """Round to the stored precision and remove a tiny overshoot of [0, 1]."""
    return min(1.0, max(0.0, round(value, _DECIMALS)))


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned region of the source picture, normalised; ``x0 < x1`` and ``y0 < y1``."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if not _in_unit(self.x0, self.y0, self.x1, self.y1) or self.x1 <= self.x0:
            raise InvariantViolation(
                "A box lies within the 0-1 picture and has a positive size",
                details={"box": (self.x0, self.y0, self.x1, self.y1)},
            )
        if self.y1 <= self.y0:
            raise InvariantViolation("A box needs a positive height")

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


def union_box(boxes: "list[Box] | tuple[Box, ...]") -> Box | None:
    if not boxes:
        return None
    return Box(
        min(b.x0 for b in boxes),
        min(b.y0 for b in boxes),
        max(b.x1 for b in boxes),
        max(b.y1 for b in boxes),
    )


@dataclass(frozen=True, slots=True)
class Crop:
    """A window of the source picture: top-left corner ``(x, y)``, size ``(w, h)``."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        if self.w <= 0 or self.h <= 0 or not _in_unit(self.x, self.y, self.w, self.h):
            raise InvariantViolation(
                "A crop has a positive size inside the picture",
                details={"crop": (self.x, self.y, self.w, self.h)},
            )
        if self.x + self.w > 1 + EPSILON or self.y + self.h > 1 + EPSILON:
            raise InvariantViolation(
                "A crop lies inside the picture",
                details={"crop": (self.x, self.y, self.w, self.h)},
            )

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def rounded(self) -> "Crop":
        """The stored form: 6 decimals, never outside the picture."""
        w, h = _snap(self.w), _snap(self.h)
        return Crop(min(_snap(self.x), 1.0 - w), min(_snap(self.y), 1.0 - h), w, h)

    def to_output(self, box: Box) -> tuple[float, float, float, float]:
        """``box`` in this crop's own 0-1 frame (the output picture); values may leave [0, 1]."""
        return (
            (box.x0 - self.x) / self.w,
            (box.y0 - self.y) / self.h,
            (box.x1 - self.x) / self.w,
            (box.y1 - self.y) / self.h,
        )

    def inside_share(self, box: Box) -> float:
        """The share of ``box``'s area that lies inside the crop (0-1)."""
        width = min(box.x1, self.x1) - max(box.x0, self.x)
        height = min(box.y1, self.y1) - max(box.y0, self.y)
        if width <= 0 or height <= 0:
            return 0.0
        return min(1.0, (width * height) / box.area)

    def margins(self, box: Box) -> tuple[float, float, float, float]:
        """Distance from ``box`` to the left, top, right and bottom crop edge, as a fraction of the
        crop's width (horizontal) or height (vertical); negative when the box crosses the edge."""
        return (
            (box.x0 - self.x) / self.w,
            (box.y0 - self.y) / self.h,
            (self.x1 - box.x1) / self.w,
            (self.y1 - box.y1) / self.h,
        )


@dataclass(frozen=True, slots=True)
class SourceGeometry:
    """The source picture as the crop sees it."""

    #: Size as displayed: pixel shape applied, then rotated.
    display_width: int
    display_height: int
    #: Stored samples in display orientation (rotated, pixel shape NOT applied).
    sample_width: int
    sample_height: int
    #: Clockwise degrees a player applies: 0, 90, 180 or 270.
    rotation: int
    #: ``False`` when only the display size was known: samples are then assumed to equal it
    #: (square pixels, no scaling) and the effective resolution is only as good as that guess.
    exact: bool

    def __post_init__(self) -> None:
        sizes = (self.display_width, self.display_height, self.sample_width, self.sample_height)
        if min(sizes) <= 0 or self.rotation not in (0, 90, 180, 270):
            raise InvariantViolation("A source needs positive sizes and a right-angle rotation")

    @property
    def aspect(self) -> Fraction:
        return Fraction(self.display_width, self.display_height)


@dataclass(frozen=True, slots=True)
class TargetFormat:
    """What the output looks like: its shape and its pixel size."""

    name: str
    aspect: Rational
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise InvariantViolation("A target format needs a positive resolution")
        if abs((self.width / self.height) / self.aspect.value - 1) > ASPECT_TOLERANCE:
            raise InvariantViolation(
                f"The resolution {self.width}x{self.height} does not have the aspect {self.name}"
            )

    @classmethod
    def of(
        cls, aspect: str, *, width: int | None = None, height: int | None = None
    ) -> "TargetFormat":
        """``of("9:16")`` (the standard 1080x1920), ``of("4:5", height=1350)`` (width derived) or
        ``of("21:9", width=2560, height=1097)``. A custom aspect with no size gets 1920 on its
        longer side."""
        parsed = Rational.parse(aspect)
        if parsed is None:
            raise InvariantViolation(f"{aspect!r} is not an aspect ratio such as 16:9")
        name = f"{parsed.numerator}:{parsed.denominator}"
        if width is None and height is None:
            width, height = STANDARD_RESOLUTIONS.get(name) or _longest(parsed)
        elif width is None and height is not None:
            width = round(height * parsed.value)
        elif height is None and width is not None:
            height = round(width / parsed.value)
        if width is None or height is None:  # unreachable; narrows the types
            raise InvariantViolation("A target format needs a resolution")
        return cls(name, parsed, width, height)

    @property
    def ratio(self) -> Fraction:
        return Fraction(self.aspect.numerator, self.aspect.denominator)


def _longest(aspect: Rational) -> tuple[int, int]:
    if aspect.value >= 1:
        return _DEFAULT_LONG_SIDE, round(_DEFAULT_LONG_SIDE / aspect.value)
    return round(_DEFAULT_LONG_SIDE * aspect.value), _DEFAULT_LONG_SIDE


def widest_crop(source: SourceGeometry, target: TargetFormat) -> tuple[float, float]:
    """Width and height (normalised) of the largest window of the target's shape (zoom 1)."""
    ratio = float(target.ratio)
    width_at_full_height = ratio * source.display_height / source.display_width
    if abs(width_at_full_height - 1) <= ASPECT_TOLERANCE:
        return 1.0, 1.0
    if width_at_full_height < 1:
        return width_at_full_height, 1.0
    return 1.0, source.display_width / (ratio * source.display_height)


def crop_around(
    source: SourceGeometry, target: TargetFormat, zoom: float, cx: float, cy: float
) -> Crop:
    """The window ``zoom`` times the widest one, centred on ``(cx, cy)`` and pushed inside the
    picture. ``zoom`` 1 is the widest window, smaller values are tighter."""
    width, height = widest_crop(source, target)
    w, h = width * zoom, height * zoom
    x = min(max(cx - w / 2, 0.0), 1.0 - w)
    y = min(max(cy - h / 2, 0.0), 1.0 - h)
    return Crop(x, y, w, h)


def aspect_error(crop: Crop, source: SourceGeometry, target: TargetFormat) -> float:
    """Relative difference between the crop's display aspect and the target's."""
    shown = (crop.w * source.display_width) / (crop.h * source.display_height)
    return abs(shown / float(target.ratio) - 1)


def crop_problems(crop: Crop, source: SourceGeometry, target: TargetFormat) -> list[str]:
    """Why a crop is not valid for this source and target (empty when it is)."""
    problems: list[str] = []
    error = aspect_error(crop, source, target)
    if error > ASPECT_TOLERANCE:
        problems.append(f"the crop's aspect differs from {target.name} by {error:.4f}")
    return problems


def effective_scale(crop: Crop, source: SourceGeometry, target: TargetFormat) -> float:
    """Output pixels per stored sample on the worse axis: >1 enlarges, 1 is one to one."""
    across = crop.w * source.sample_width
    down = crop.h * source.sample_height
    return max(target.width / across, target.height / down)


def interpolate(a: Crop, b: Crop, fraction: float) -> Crop:
    """The window ``fraction`` of the way from ``a`` to ``b`` (x, y, w and h move linearly)."""
    t = min(1.0, max(0.0, fraction))
    return Crop(
        a.x + (b.x - a.x) * t,
        a.y + (b.y - a.y) * t,
        a.w + (b.w - a.w) * t,
        a.h + (b.h - a.h) * t,
    )
