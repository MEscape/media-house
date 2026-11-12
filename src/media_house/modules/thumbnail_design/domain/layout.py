"""Where everything goes, in reference pixels of a 1920x1080 master (the owner-approved v2 look).

Fixed slots, no search: the same brief always gives the same picture. The composition is the
channel's 2025/26 thumbnail system: headline top left, creator reaction bottom left looking into
the picture, retailer logo top right, phone and products centre right, the two silver bars in the
bottom-left and top-right corners, and nothing important where YouTube draws the duration.
"""

from dataclasses import dataclass
from typing import Final

from media_house.modules.thumbnail_design.domain.brief import ThumbnailBrief

WIDTH: Final = 1920
HEIGHT: Final = 1080
#: YouTube's duration chip (bottom right): no text, price or face may sit here.
TIMESTAMP_ZONE: Final = (1660, 970, WIDTH, HEIGHT)
#: Headline: largest size per line kind, and the width the renderer shrinks it into.
HEADLINE_ACCENT_PX: Final = 206
HEADLINE_PLAIN_PX: Final = 168
HEADLINE_SINGLE_PX: Final = 220
HEADLINE_MAX_WIDTH: Final = 860
HEADLINE_ORIGIN: Final = (52, 18)
HEADLINE_TILT: Final = 2.5
#: Bars: slope (rise per px), thickness and the two centre lines (start, end).
BAR_SLOPE: Final = 0.24
BAR_THICKNESS: Final = 58
BARS: Final = (
    ((-40.0, 955.0 - 40 * BAR_SLOPE), (900.0, 955.0 + 900 * BAR_SLOPE)),
    ((1180.0, -40.0), (2000.0, -40.0 + 820 * BAR_SLOPE)),
)


@dataclass(frozen=True, slots=True)
class Slot:
    """A picture's top-left corner and width before effects; ``rotation`` in degrees (CCW)."""

    x: int
    y: int
    width: int
    rotation: float


@dataclass(frozen=True, slots=True)
class Placed:
    asset_id: str
    slot: Slot


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    reaction: Slot
    device: Slot | None
    logo: Slot | None
    badge: Slot | None
    #: Back to front: the most important product is drawn last.
    products: tuple[Placed, ...]
    headline_sizes: tuple[int, ...]


REACTION: Final = Slot(-330, 300, 1330, 0.0)
DEVICE: Final = Slot(930, 250, 430, 9.0)
LOGO: Final = Slot(1490, 10, 360, -6.0)
BADGE: Final = Slot(880, 0, 500, 8.0)
#: Product slots by importance, with and without the phone. ``top`` is the badge's place.
_WITH_DEVICE: Final = (Slot(1350, 650, 640, 8.0), Slot(1390, 340, 540, -14.0))
_WITHOUT_DEVICE: Final = (Slot(860, 320, 760, 6.0), Slot(1400, 660, 560, -8.0))
_TOP: Final = Slot(940, 30, 440, 18.0)


def lay_out(brief: ThumbnailBrief) -> Layout:
    slots = list(_WITH_DEVICE if brief.device is not None else _WITHOUT_DEVICE)
    if brief.badge is None:
        slots.append(_TOP)
    placed = tuple(Placed(asset, slot) for asset, slot in zip(brief.products, slots, strict=False))
    lines = brief.headline.lines
    if len(lines) == 1:
        sizes: tuple[int, ...] = (HEADLINE_SINGLE_PX,)
    else:
        sizes = tuple(
            HEADLINE_ACCENT_PX if i == brief.headline.accent_line else HEADLINE_PLAIN_PX
            for i in range(len(lines))
        )
    return Layout(
        reaction=REACTION,
        device=DEVICE if brief.device is not None else None,
        logo=LOGO if brief.logo_asset_id is not None else None,
        badge=BADGE if brief.badge is not None else None,
        products=tuple(reversed(placed)),
        headline_sizes=sizes,
    )


def unplaced_products(brief: ThumbnailBrief) -> tuple[str, ...]:
    """Products the layout has no slot for (the badge takes the top slot)."""
    shown = {p.asset_id for p in lay_out(brief).products}
    return tuple(p for p in brief.products if p not in shown)
