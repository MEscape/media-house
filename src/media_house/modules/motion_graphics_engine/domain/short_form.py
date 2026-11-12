"""Short-form rules: what a portrait (9:16) edit gets on top of the planned graphics.

A Short lives on attention: something new must happen every few seconds. For a portrait output
the planner therefore (deterministically, from the brief's facts only):

* opens with a hook title over the first seconds ("TOP 5" + the retailer, from the ranking the
  ordered cards carry and the products' retailer; nothing is invented);
* keeps every full-screen card short (at most ``CARD_MAX``) and after the hook, one card after
  the other (a card that no longer fits the edit is left out, recorded);
* punches every spoken product figure (a spec or price with its spoken time) as a full-screen
  fact card (the figure huge, counting up, the format's background footage playing through),
  never over another full-screen card;
* pops the current product's cut-out where nothing else has happened for ``QUIET`` seconds.

Every addition records why it was made.
"""

import re
from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.brief import GraphicsBrief, ProductFacts
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationRequest,
    GraphicContent,
    GraphicRequest,
    Placement,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.qc import issue
from media_house.modules.motion_graphics_engine.domain.text import german_number
from media_house.modules.motion_graphics_engine.domain.values import (
    GraphicKind,
    Origin,
    QcIssue,
    Severity,
)

#: The hook title's length (s) at the very start.
HOOK = Fraction(2)
#: Longest and shortest full-screen card of a Short (s).
CARD_MAX = Fraction(16, 5)
CARD_MIN = Fraction(6, 5)
#: A fact punch's length (s) and the least room kept between two full-screen cards (s).
PUNCH = Fraction(2)
GAP = Fraction(2)
#: Nothing new on screen for this long (s): the product's cut-out pops in; its length (s).
QUIET = Fraction(4)
POP = Fraction(8, 5)

_FULL_FRAME = frozenset(
    {
        (GraphicKind.RANK_CARD, "intro"),
        (GraphicKind.SPEC_CARD, "showcase"),
        (GraphicKind.PRODUCT_STICKER, "gallery"),
        (GraphicKind.CALLOUT, "punch"),
    }
)
#: Graphics that are always on (labels) or follow the words: never "something new".
_BACKDROP = frozenset({GraphicKind.SPONSOR_LABEL, GraphicKind.CAPTION})
_OF = re.compile(r"(\d+)\s*/\s*(\d+)")


def _f(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def _r(f: Fraction) -> Rational:
    return Rational(f.numerator, f.denominator)


def is_full_frame(r: GraphicRequest) -> bool:
    return (r.kind, r.content.variant) in _FULL_FRAME


def _span(r: GraphicRequest) -> tuple[Fraction, Fraction]:
    start = _f(r.timing.timeline_start) if r.timing.timeline_start is not None else Fraction(-1)
    return start, start + _f(r.timing.local_duration)


def _timed(r: GraphicRequest, start: Fraction, duration: Fraction) -> GraphicRequest:
    return replace(
        r, timing=replace(r.timing, timeline_start=_r(start), local_duration=_r(duration))
    )


def short_form(
    requests: Sequence[GraphicRequest],
    brief: GraphicsBrief,
    labels: dict[str, str],
) -> tuple[list[GraphicRequest], list[QcIssue]]:
    """``requests`` with the Short's rules applied (``labels``: spec kind -> the channel's
    label). The order of the result is the planner's concern."""
    total = _f(brief.timeline_duration) if brief.timeline_duration is not None else None
    issues: list[QcIssue] = []
    out = [r for r in requests if not is_full_frame(r)]
    hook = _hook(requests, brief)
    cursor = Fraction(0)
    if hook is not None:
        out.append(hook)
        cursor = HOOK
    cards = sorted(_one_card_per_product(requests), key=lambda r: (_span(r), r.request_id))
    placed: list[tuple[Fraction, Fraction]] = []
    for r in cards:
        start, end = _span(r)
        if start < 0:
            out.append(r)
            continue
        start = max(start, cursor)
        duration = min(end - _span(r)[0], CARD_MAX)
        if total is not None:
            duration = min(duration, total - start)
        if duration < CARD_MIN:
            issues.append(
                issue(
                    "short.card_no_room",
                    Severity.INFO,
                    f"{r.request_id}: no room left for a full-screen card in the Short",
                    r.request_id,
                )
            )
            continue
        out.append(_timed(r, start, duration))
        placed.append((start, start + duration))
        cursor = start + duration
    # every full-screen moment keeps GAP of the talking picture around it
    taken = ([(Fraction(0), HOOK)] if hook is not None else []) + placed
    for punch in _punches(brief, labels, out, total):
        a, b = _span(punch)
        if any(a < y + GAP and x < b + GAP for x, y in taken):
            continue
        out.append(punch)
        taken.append((a, b))
    out += _pops(out, total)
    return out, issues


def _one_card_per_product(requests: Sequence[GraphicRequest]) -> list[GraphicRequest]:
    """The full-screen cards; a product with an intro and a data card keeps only the data card
    (it shows the cut-out, name and price too), moved to where the product is introduced: two
    cards per item would fill most of a Short."""
    cards = [r for r in requests if is_full_frame(r)]
    data = {r.request_id.removesuffix(":data"): r for r in cards if r.request_id.endswith(":data")}
    out: list[GraphicRequest] = []
    for r in cards:
        key = r.request_id.removesuffix(":intro")
        if r.request_id.endswith(":intro") and key in data:
            continue
        intro = next(
            (c for c in cards if c.request_id == f"{key.removesuffix(':data')}:intro"), None
        )
        if r.request_id.endswith(":data") and intro is not None:
            start, end = _span(r)
            r = _timed(r, _span(intro)[0], end - start)
        out.append(r)
    return out


def _hook(requests: Sequence[GraphicRequest], brief: GraphicsBrief) -> GraphicRequest | None:
    """ "TOP N" over the opening, N the ranking's size ("4/5" on a card, else the highest rank of
    the brief's sections); the products' retailer under it. No ranking, no hook."""
    sizes = [int(m.group(2)) for r in requests if (m := _OF.search(r.content.eyebrow))]
    sizes += [s.rank for s in brief.sections if s.rank is not None]
    if not sizes or max(sizes) < 2:
        return None
    retailers = sorted({p.retailer.strip() for p in brief.products if p.retailer.strip()})
    return GraphicRequest(
        request_id="short:hook",
        kind=GraphicKind.TITLE,
        content=GraphicContent(
            headline=f"TOP {max(sizes)}", body=tuple(retailers[:1]), variant="hook"
        ),
        timing=Timing(local_duration=_r(HOOK), timeline_start=_r(Fraction(0))),
        animation=AnimationRequest(entry="pop_in", exit="pop_out"),
        priority=0.95,
        reason="short-form hook: the ranking's size over the opening seconds",
        origin=Origin.EDITORIAL,
    )


def _figures(product: ProductFacts, labels: dict[str, str]) -> list[tuple[Fraction, str, str]]:
    """(spoken at, figure, label) of every product figure with a spoken time."""
    found: list[tuple[Fraction, str, str]] = []
    for spec in product.specs:
        label = labels.get(spec.kind)
        if label is None or spec.timeline_at is None:
            continue
        number, _ = german_number(spec.value)
        found.append((_f(spec.timeline_at), f"{number} {spec.unit}".strip(), label))
    if product.price_cents is not None and product.timeline_at is not None:
        euros, _ = german_number(Rational(product.price_cents, 100))
        found.append((_f(product.timeline_at), f"{euros} €", "Preis"))
    return found


def _punches(
    brief: GraphicsBrief,
    labels: dict[str, str],
    planned: Sequence[GraphicRequest],
    total: Fraction | None,
) -> list[GraphicRequest]:
    """A fact punch per spoken figure, fully in a moment before the figure is said; the cut-out
    is the one a card of the same product already shows (prepared), else none."""
    cutouts = {
        r.content.headline.casefold(): r.content.image_asset_ids[:1]
        for r in planned
        if is_full_frame(r) and r.content.headline and r.content.image_asset_ids
    }
    out: list[GraphicRequest] = []
    for product in sorted(brief.products, key=lambda p: p.product_id):
        for at, figure, label in _figures(product, labels):
            start = max(Fraction(0), at - Fraction(1, 5))
            if total is not None and start + PUNCH > total:
                continue
            out.append(
                GraphicRequest(
                    request_id=f"short:punch:{product.product_id}:{label.casefold()}",
                    kind=GraphicKind.CALLOUT,
                    content=GraphicContent(
                        headline=figure,
                        eyebrow=label,
                        body=(product.name,) if product.name else (),
                        image_asset_ids=cutouts.get(product.name.casefold(), ()),
                        variant="punch",
                        source=product.source,
                    ),
                    timing=Timing(local_duration=_r(PUNCH), timeline_start=_r(start)),
                    priority=0.85,
                    reason=f"short-form fact punch: {label} of {product.name or product.product_id}"
                    " is said here",
                    origin=Origin.EDITORIAL,
                )
            )
    return sorted(out, key=lambda r: (_span(r), r.request_id))


def _pops(planned: Sequence[GraphicRequest], total: Fraction | None) -> list[GraphicRequest]:
    """The latest full-screen card's cut-out pops in (alternating sides) wherever nothing new
    has happened for ``QUIET`` seconds."""
    if total is None:
        return []
    events = sorted(
        (_span(r), r)
        for r in planned
        if r.kind not in _BACKDROP and r.timing.timeline_start is not None
    )
    out: list[GraphicRequest] = []
    busy_until = Fraction(0)
    image: tuple[str, ...] = ()
    edges = [*((a, b, r) for (a, b), r in events), (total, total, None)]
    for a, b, r in edges:
        t = busy_until
        while image and a - t >= QUIET and t + QUIET / 2 + POP <= total:
            start = t + QUIET / 2
            side = "callout_left" if len(out) % 2 == 0 else "callout_right"
            out.append(
                GraphicRequest(
                    request_id=f"short:pop:{len(out)}",
                    kind=GraphicKind.PRODUCT_STICKER,
                    content=GraphicContent(image_asset_ids=image, emphasis=2),
                    timing=Timing(local_duration=_r(POP), timeline_start=_r(start)),
                    placement=Placement(zone=side),
                    priority=0.6,
                    reason="short-form pattern interrupt: nothing new on screen for"
                    f" {float(QUIET):g} s, the current product pops in",
                    origin=Origin.EDITORIAL,
                )
            )
            t = start + POP
        if r is not None and is_full_frame(r) and r.content.image_asset_ids:
            image = r.content.image_asset_ids[:1]
        busy_until = max(busy_until, b)
    return out
