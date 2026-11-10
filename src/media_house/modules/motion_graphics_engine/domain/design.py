"""Design tokens: the one spacing grid, type scale, radii, strokes and shadows every preset uses.

Derived from the style (``StyleFacts``), never invented: the grid unit is a third of the style's
spacing, card type sizes are the style's type roles, colours are the style's semantic names.
A preset only picks token NAMES (``space.md``, ``type.value``), so two graphics of the same
video always share margins, sizes and corner radii, whatever planner ordered them.
"""

from dataclasses import dataclass

from media_house.modules.motion_graphics_engine.domain.model import StyleFacts

#: Grid steps in units: xs, sm, md, lg, xl.
_STEPS = (1, 2, 3, 4, 6)
#: Size factor of each emphasis level (0 subtle, 1 standard, 2 hero) on card type and width.
EMPHASIS_SCALE: tuple[float, float, float] = (0.85, 1.0, 1.2)
#: Text straight over footage (no panel) is never smaller than this share of the short side:
#: on a phone a 1080p label below ~3 % is unreadable over a busy picture.
_VIDEO_FLOOR = 0.032

#: Card colourways: (panel colour, text colour, eyebrow colour), all style colour names. Every
#: pair is one of the identity's approved combinations or clears its contrast floor.
CARD_COLOURS: dict[str, tuple[str, str, str]] = {
    "dark": ("panel", "white", "highlight"),
    "black": ("black", "white", "highlight"),
    "accent": ("accent", "white", "white"),
    "highlight": ("highlight", "black", "black"),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Design:
    unit: int
    xs: int
    sm: int
    md: int
    lg: int
    xl: int
    radius: int
    radius_sm: int
    #: Accent bar of a card (its left edge) and the strike line of a struck value.
    bar: int
    #: Hairlines: dividers, timeline rails.
    rule: int
    #: Soft drop shadow that lifts a card off busy footage.
    shadow_blur: int
    shadow_opacity: float
    eyebrow_px: int
    body_px: int
    title_px: int
    value_px: int
    hero_px: int
    #: Smallest text drawn straight over footage (outlined, no panel).
    video_floor_px: int

    def scaled(self, size_px: int, emphasis: int) -> int:
        return round(size_px * EMPHASIS_SCALE[min(max(emphasis, 0), 2)])


def design(style: StyleFacts) -> Design:
    g = style.graphics
    unit = max(4, g.spacing_px // 3)
    xs, sm, md, lg, xl = (unit * s for s in _STEPS)
    return Design(
        unit=unit,
        xs=xs,
        sm=sm,
        md=md,
        lg=lg,
        xl=xl,
        radius=g.radius_px,
        radius_sm=max(2, g.radius_px // 2),
        bar=unit,
        rule=max(2, g.graphic_stroke_px // 2),
        shadow_blur=g.soft_shadow_px * 2 // 3,
        shadow_opacity=g.soft_shadow_opacity,
        eyebrow_px=style.type("label").size_px,
        body_px=style.type("body").size_px,
        title_px=style.type("headline").size_px * 2 // 3,
        value_px=style.type("number").size_px * 3 // 4,
        hero_px=style.type("number").size_px,
        video_floor_px=max(style.min_text_px, round(min(style.width, style.height) * _VIDEO_FLOOR)),
    )
