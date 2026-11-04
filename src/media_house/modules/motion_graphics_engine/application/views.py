"""Mapping helper for the pipeline: content_intelligence commerce facts -> ``ProductFacts``.

The facts are printed exactly as found: a price is ``Fraction`` euros upstream and becomes exact
cents here (a price that is not a whole number of cents is refused, never rounded).
"""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.content_intelligence.application.contracts import (
    CommerceFact,
    CommerceKind,
)
from media_house.modules.motion_graphics_engine.domain.brief import ProductFacts, SpecFact

_SPECS = {
    CommerceKind.SHOTS: "shots",
    CommerceKind.EFFECTS: "effects",
    CommerceKind.HEIGHT: "height",
    CommerceKind.DURATION: "duration",
    CommerceKind.CALIBER: "caliber",
    CommerceKind.WEIGHT: "weight",
}


def price_cents(value: Fraction) -> int | None:
    cents = value * 100
    return int(cents) if cents.denominator == 1 and cents >= 0 else None


def product_facts(
    product_id: str,
    facts: tuple[CommerceFact, ...],
    *,
    name: str = "",
    image_asset_ids: tuple[str, ...] = (),
    source: str = "",
) -> ProductFacts:
    """The first price, the specs (first of each kind) and the retailer among ``facts``."""
    price: int | None = None
    specs: dict[str, SpecFact] = {}
    retailer = ""
    product_name = name
    for fact in facts:
        if fact.kind is CommerceKind.PRICE and price is None and fact.value is not None:
            price = price_cents(fact.value)
        elif fact.kind in _SPECS and fact.value is not None and _SPECS[fact.kind] not in specs:
            value = Rational(fact.value.numerator, fact.value.denominator)
            specs[_SPECS[fact.kind]] = SpecFact(
                kind=_SPECS[fact.kind], value=value, unit=fact.unit, text=fact.text
            )
        elif fact.kind is CommerceKind.RETAILER and not retailer:
            retailer = fact.name or fact.text
        elif fact.kind is CommerceKind.PRODUCT and not product_name:
            product_name = fact.text
    return ProductFacts(
        product_id=product_id,
        name=product_name,
        price_cents=price,
        specs=tuple(specs.values()),
        retailer=retailer,
        image_asset_ids=image_asset_ids,
        source=source,
    )


def rank_of(facts: tuple[CommerceFact, ...]) -> int | None:
    for fact in facts:
        if (
            fact.kind is CommerceKind.RANK
            and fact.value is not None
            and fact.value.denominator == 1
        ):
            return int(fact.value)
    return None
