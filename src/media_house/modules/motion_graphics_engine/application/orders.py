"""The orchestration surface for planners outside this module (rules or a language model).

A planner only fills a ``GraphicOrder`` (kind, slots, variant, emphasis, spoken time); every
design decision is the preset's. ``order_request`` validates the order against its preset and
returns the ``GraphicRequest`` to render (the style prints prices at render time), or
``InvalidGraphicOrder`` listing EVERY problem (so a model can repair its order in one round).
"""

from media_house.modules.motion_graphics_engine.application.style import style_facts
from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicOrder
from media_house.modules.motion_graphics_engine.domain.model import GraphicRequest
from media_house.modules.motion_graphics_engine.domain.presets import GraphicOrder, compile_order
from media_house.modules.motion_graphics_engine.domain.profile import Timing
from media_house.modules.visual_style_engine.application.contracts import ResolvedStyle
from media_house.shared.errors import Err, Ok, Result

_READING_CPS = Timing().reading_cps


def order_request(
    order: GraphicOrder, style: ResolvedStyle, *, reading_cps: float = _READING_CPS
) -> Result[GraphicRequest, InvalidGraphicOrder]:
    """``order`` -> its request, timed with the style's motion tokens (fully in at ``at``)."""
    try:
        return Ok(compile_order(order, style_facts(style).motion, reading_cps=reading_cps))
    except InvalidGraphicOrder as exc:
        return Err(exc)
