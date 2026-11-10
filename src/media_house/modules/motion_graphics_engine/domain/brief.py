"""``GraphicsBrief``: the engine's own narrow input (never the edit plan itself).

The pipeline maps the edit into it (README "Mapping from an edit"); without an edit (no-speech
path) it holds the timeline duration only: no sections, no editorial graphics, no facts.
Every number in it is a FACT found upstream (commerce facts, supplied data); the planner only
prints facts it was given.
"""

from dataclasses import dataclass

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.model import DataPoint
from media_house.modules.motion_graphics_engine.domain.values import EditRef, Intent


@dataclass(frozen=True, slots=True, kw_only=True)
class BriefSection:
    section_id: str
    title: str
    purpose: str
    timeline_start: Rational
    timeline_end: Rational
    #: The rank this section presents in a countdown ("Platz 3"), from the story structure.
    rank: int | None = None
    #: The product this section is about (``ProductFacts.product_id``).
    product_id: str | None = None
    #: When the rank is spoken (timeline clock); ``None`` = unknown (the card opens the section).
    rank_at: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SpecFact:
    """One firework spec found in the speech or on screen (``kind``: shots, effects, height,
    duration, caliber, weight) with its exact value and unit."""

    kind: str
    value: Rational
    unit: str = ""
    text: str = ""
    #: When it is spoken (timeline clock); ``None`` = unknown.
    timeline_at: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductFacts:
    product_id: str
    name: str = ""
    #: Exact price in cents (``Fraction(1599, 100)`` euros -> 1599); never rounded.
    price_cents: int | None = None
    specs: tuple[SpecFact, ...] = ()
    retailer: str = ""
    #: Library pictures of the product (photos or cut-outs) for stickers and teasers.
    image_asset_ids: tuple[str, ...] = ()
    #: Where the facts were found (verbatim provenance).
    source: str = ""
    #: When its price (else its first fact) is spoken (timeline clock); ``None`` = unknown.
    timeline_at: Rational | None = None
    #: The section it was heard in.
    section_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OnScreenText:
    """Text the picture itself already shows (video OCR, mapped onto the timeline)."""

    timeline_start: Rational
    timeline_end: Rational
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EditorialGraphic:
    """One editorial request (an edit plan GRAPHIC effect request): editing decided THAT and
    WHEN; motion decides HOW from the concept and the facts it names."""

    request_id: str
    intent: Intent
    timeline_at: Rational
    priority: float
    concept: str
    section_id: str | None = None
    duration: Rational | None = None
    product_id: str | None = None
    #: Supplied numbers for a chart/counter (never invented).
    data: tuple[DataPoint, ...] = ()
    reason: str = ""
    #: What the concept is ("PREIS", "VERKAUFSSTART"): shown above it. A bare figure (a price,
    #: a date, a count) without a label is never drawn.
    label: str = ""
    #: When the concept is spoken (timeline clock): the card is fully in then. ``None`` =
    #: unknown (``timeline_at``, the edit's moment, is used).
    spoken_at: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SponsoredSpan:
    timeline_start: Rational
    timeline_end: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicsBrief:
    anchor_asset_id: str
    project_id: str | None
    edit: EditRef | None
    frame_rate: Rational
    width: int
    height: int
    #: Length of the edit (or of the source on the no-speech path); ``None`` = unknown.
    timeline_duration: Rational | None = None
    story_format: str | None = None
    sponsored: bool = False
    #: Sponsored parts; empty with ``sponsored`` = the whole timeline.
    sponsored_spans: tuple[SponsoredSpan, ...] = ()
    sections: tuple[BriefSection, ...] = ()
    graphics: tuple[EditorialGraphic, ...] = ()
    products: tuple[ProductFacts, ...] = ()
    #: An explicit end-screen request overrides the profile (True/False); None = profile.
    end_screen: bool | None = None
    membership_plug_at: Rational | None = None
    #: Text already burned into the picture: a card showing the same is not drawn again.
    on_screen: tuple[OnScreenText, ...] = ()
