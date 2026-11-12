"""Graphic planning: a ``GraphicsBrief`` -> the graphic requests (HOW, never WHETHER).

* Editorial requests (editing decided that and when) become a kind, content from the FACTS the
  brief carries (price, specs, data), a zone, a timing and the style's animation.
* Format templates the profile enables are built from the brief's structure: a "PLATZ N" card
  at every ranked section and the black-silhouette teaser of the next rank.
* Brand bookends the profile/style mandate: the end screen and the sponsor label over the
  sponsored parts. Each records why it was added.
* No-speech path (no edit, no sections, no requests): bookends only.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.brief import (
    BriefSection,
    EditorialGraphic,
    GraphicsBrief,
    OnScreenText,
    ProductFacts,
)
from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicOrder
from media_house.modules.motion_graphics_engine.domain.layout import KIND_LAYOUT
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationRequest,
    GraphicContent,
    GraphicRequest,
    SpecLine,
    StyleFacts,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.presets import bare_figure, compile_order
from media_house.modules.motion_graphics_engine.domain.profile import (
    MotionGraphicsConfig,
    MotionGraphicsProfile,
)
from media_house.modules.motion_graphics_engine.domain.qc import issue
from media_house.modules.motion_graphics_engine.domain.short_form import short_form
from media_house.modules.motion_graphics_engine.domain.text import german_number
from media_house.modules.motion_graphics_engine.domain.values import (
    ChartKind,
    GraphicKind,
    Intent,
    Origin,
    QcIssue,
    Severity,
)

#: Spec-card row labels: the channel's German wording (Schuss / Höhe / Brenndauer / Kosten).
SPEC_LABELS: dict[str, str] = {
    "shots": "Schuss",
    "effects": "Effekte",
    "height": "Höhe",
    "duration": "Brenndauer",
    "caliber": "Kaliber",
    "weight": "NEM",
}
_SPEC_ORDER = tuple(SPEC_LABELS)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageNeed:
    """A picture the planner needs prepared by visual_asset_generation (never cut out here)."""

    request_id: str
    source_asset_id: str
    variant: str  # "sticker" | "silhouette"
    #: Optional pictures (the spec card's product) are left out when no cut-out exists.
    optional: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedRequests:
    requests: tuple[GraphicRequest, ...]
    images: tuple[ImageNeed, ...]
    issues: tuple[QcIssue, ...]


def _f(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def _r(f: Fraction) -> Rational:
    return Rational(f.numerator, f.denominator)


def _seconds(value: float) -> Fraction:
    return Fraction(str(value))


class _Planner:
    def __init__(
        self,
        brief: GraphicsBrief,
        config: MotionGraphicsConfig,
        profile: MotionGraphicsProfile,
        style: StyleFacts,
    ) -> None:
        self.brief = brief
        self.config = config
        self.profile = profile
        self.style = style
        self.m = style.motion
        self.requests: list[GraphicRequest] = []
        self.images: list[ImageNeed] = []
        self.issues: list[QcIssue] = []
        #: Products whose facts (price / specs) a card already shows: never drawn twice.
        self.shown_facts: set[str] = set()
        self.products = {p.product_id: p for p in brief.products}
        self.sections = {s.section_id: s for s in brief.sections}

    def profile_for(self, section_id: str | None) -> MotionGraphicsProfile:
        return self.config.for_section(self.profile, section_id)

    def frame(self) -> Fraction:
        return Fraction(self.brief.frame_rate.denominator, self.brief.frame_rate.numerator)

    def text_duration(self, text: str, factor: float, section_id: str | None) -> Fraction:
        timing = self.profile_for(section_id).timing
        hold = max(
            _f(self.m.min_hold) * _seconds(factor),
            Fraction(len(text)) / _seconds(timing.reading_cps),
        )
        return _f(self.m.entry_max) + hold + _f(self.m.exit_max)

    def spoken(
        self, at: Rational | None, fallback: Fraction, request_id: str, what: str
    ) -> Fraction:
        """A fact card is fully in when its fact is spoken (the style's entry is the lead-in);
        with no known time it opens at ``fallback`` (section or request start), recorded."""
        if at is not None:
            return max(Fraction(0), _f(at) - _f(self.m.entry_max))
        self.issues.append(
            issue(
                "timing.unspoken",
                Severity.INFO,
                f"{request_id}: no spoken time for the {what}; placed at the section/request start",
                request_id,
            )
        )
        return fallback

    def on_screen(self, text: str, start: Fraction, duration: Fraction) -> OnScreenText | None:
        """The picture's own text that already says ``text`` around ``[start, start+duration]``."""
        window = _seconds(self.profile.timing.on_screen_window)
        want = _bare(text)
        for shown in self.brief.on_screen:
            if (
                want
                and want in _bare(shown.text)
                and _f(shown.timeline_start) - window < start + duration
                and start < _f(shown.timeline_end) + window
            ):
                return shown
        return None

    def already_shown(
        self, request_id: str, texts: tuple[str, ...], start: Fraction, duration: Fraction
    ) -> bool:
        """True (recorded as INFO) when the picture already shows any of ``texts`` then."""
        shown = next(
            (s for t in texts if (s := self.on_screen(t, start, duration)) is not None), None
        )
        if shown is not None:
            self.issues.append(
                issue(
                    "editorial.already_on_screen",
                    Severity.INFO,
                    f"{request_id}: the picture already shows {shown.text!r} at"
                    f" {float(_f(shown.timeline_start)):.1f} s: not drawn again",
                    request_id,
                )
            )
        return shown is not None

    def end_of(self, section_id: str | None) -> Fraction | None:
        section = self.sections.get(section_id or "")
        if section is not None:
            return _f(section.timeline_end)
        if self.brief.timeline_duration is not None:
            return _f(self.brief.timeline_duration)
        return None

    def add(
        self,
        request_id: str,
        kind: GraphicKind,
        content: GraphicContent,
        start: Fraction | None,
        duration: Fraction,
        *,
        reason: str,
        origin: Origin,
        priority: float = 0.5,
        section_id: str | None = None,
        animation: AnimationRequest | None = None,
        appended: bool = False,
    ) -> None:
        end = None if appended else self.end_of(section_id)
        if start is not None and end is not None and start + duration > end:
            duration = end - start
            if duration < _f(self.m.min_hold):
                self.issues.append(
                    issue(
                        "timing.no_room",
                        Severity.ERROR,
                        f"{request_id}: only {float(duration):.2f} s left before the section ends",
                        request_id,
                        fix="move the request earlier",
                    )
                )
                return
        if duration < self.frame():
            self.issues.append(
                issue("timing.empty", Severity.ERROR, f"{request_id}: no duration", request_id)
            )
            return
        self.requests.append(
            GraphicRequest(
                request_id=request_id,
                kind=kind,
                content=content,
                timing=Timing(
                    local_duration=_r(duration),
                    timeline_start=_r(start) if start is not None else None,
                ),
                animation=animation or AnimationRequest(),
                priority=priority,
                reason=reason,
                origin=origin,
                section_id=section_id,
            )
        )

    # --- editorial requests -----------------------------------------------------------------

    def editorial(self, g: EditorialGraphic) -> None:
        section = self.sections.get(g.section_id or "")
        product = self.products.get(g.product_id or (section.product_id if section else "") or "")
        start = _f(g.timeline_at)
        timing = self.profile_for(g.section_id).timing
        reason = f"editorial request ({g.intent.value}): {g.reason or g.concept}"
        has_facts = product is not None and (
            len(product.specs) >= 2 or product.price_cents is not None
        )
        if product is not None and has_facts and product.product_id in self.shown_facts:
            self.issues.append(
                issue(
                    "editorial.facts_already_shown",
                    Severity.INFO,
                    f"{g.request_id}: the facts of {product.product_id} are already on a card",
                    g.request_id,
                )
            )
            return
        if product is not None and has_facts and not g.data:
            self.shown_facts.add(product.product_id)
        if g.data:
            chart = ChartKind.COUNTER if len(g.data) == 1 else ChartKind.BAR
            content = GraphicContent(
                headline=g.concept if chart is ChartKind.BAR else "", data=g.data, chart=chart
            )
            self.add(
                g.request_id,
                GraphicKind.CHART,
                content,
                start,
                _f(g.duration)
                if g.duration
                else self.text_duration(g.concept, timing.card_factor, g.section_id),
                reason=reason,
                origin=Origin.EDITORIAL,
                priority=g.priority,
                section_id=g.section_id,
            )
            return
        if product is not None and len(product.specs) >= 2:
            content = GraphicContent(
                headline=product.name,
                specs=_spec_lines(product),
                price_cents=product.price_cents,
                image_asset_ids=product.image_asset_ids[:1],
                source=product.source,
            )
            self.need(g.request_id, product, "sticker", optional=True)
            self.add(
                g.request_id,
                GraphicKind.SPEC_CARD,
                content,
                self.spoken(product.timeline_at, start, g.request_id, "specs"),
                _f(g.duration)
                if g.duration
                else self.text_duration(
                    " ".join(s.label + s.value for s in content.specs),
                    timing.card_factor,
                    g.section_id,
                ),
                reason=reason + " -> spec card from the product facts",
                origin=Origin.EDITORIAL,
                priority=g.priority,
                section_id=g.section_id,
            )
            return
        if product is not None and product.price_cents is not None:
            content = GraphicContent(
                headline=product.name, price_cents=product.price_cents, source=product.source
            )
            start = self.spoken(product.timeline_at, start, g.request_id, "price")
            duration = (
                _f(g.duration)
                if g.duration
                else self.text_duration(product.name, timing.hold_factor, g.section_id)
            )
            if self.already_shown(g.request_id, _price_texts(product.price_cents), start, duration):
                return
            self.add(
                g.request_id,
                GraphicKind.PRICE_TAG,
                content,
                start,
                duration,
                reason=reason + " -> price tag from the product facts",
                origin=Origin.EDITORIAL,
                priority=g.priority,
                section_id=g.section_id,
            )
            return
        if product is not None and product.image_asset_ids and not g.concept.strip():
            self.need(g.request_id, product, "sticker")
            content = GraphicContent(
                image_asset_ids=product.image_asset_ids[:1], source=product.source
            )
            self.add(
                g.request_id,
                GraphicKind.PRODUCT_STICKER,
                content,
                start,
                _f(g.duration)
                if g.duration
                else _f(self.m.min_hold) * _seconds(timing.hold_factor)
                + _f(self.m.entry_max)
                + _f(self.m.exit_max),
                reason=reason + " -> product sticker",
                origin=Origin.EDITORIAL,
                priority=g.priority,
                section_id=g.section_id,
            )
            return
        if not g.concept.strip():
            self.issues.append(
                issue(
                    "editorial.empty",
                    Severity.ERROR,
                    f"{g.request_id}: no concept text and no facts",
                    g.request_id,
                )
            )
            return
        concept = g.concept.strip()
        label, spoken_at = g.label.strip(), g.spoken_at
        if bare_figure(concept) and not label:
            label, spoken_at = _figure_label(concept, self.brief.products, g)
        if bare_figure(concept) and not label:
            self.issues.append(
                issue(
                    "editorial.bare_figure",
                    Severity.WARNING,
                    f"{g.request_id}: {concept!r} alone says nothing (no label): not drawn",
                    g.request_id,
                    fix="supply the label (what the figure is) with the request",
                )
            )
            return
        if spoken_at is not None:  # fully in when it is said, not when the cut allows
            start = self.spoken(spoken_at, start, g.request_id, "concept")
        kind = GraphicKind.CALLOUT if g.intent is Intent.SHOW_TEXT else GraphicKind.TEXT_PANEL
        self.add(
            g.request_id,
            kind,
            GraphicContent(headline=concept, eyebrow=label),
            start,
            _f(g.duration)
            if g.duration
            else self.text_duration(g.concept, timing.hold_factor, g.section_id),
            reason=reason,
            origin=Origin.EDITORIAL,
            priority=g.priority,
            section_id=g.section_id,
        )

    def need(
        self, request_id: str, product: ProductFacts, variant: str, optional: bool = False
    ) -> None:
        if product.image_asset_ids:
            self.images.append(
                ImageNeed(
                    request_id=request_id,
                    source_asset_id=product.image_asset_ids[0],
                    variant=variant,
                    optional=optional,
                )
            )

    # --- format templates -------------------------------------------------------------------

    def ranks(self) -> None:
        ranked = sorted(
            (s for s in self.brief.sections if s.rank is not None),
            key=lambda s: _f(s.timeline_start),
        )
        for i, section in enumerate(ranked):
            prof = self.profile_for(section.section_id)
            if prof.templates.rank_cards:
                duration = (
                    _f(self.m.entry_max)
                    + _f(self.m.min_hold) * _seconds(prof.timing.rank_card_factor)
                    + _f(self.m.exit_max)
                )
                request_id = f"rank:{section.section_id}"
                opens = _f(section.timeline_start)
                start = max(opens, self.spoken(section.rank_at, opens, request_id, "rank"))
                label = f"{prof.templates.rank_label} {section.rank}"
                if self.already_shown(request_id, (label,), start, duration):
                    continue
                self.add(
                    request_id,
                    GraphicKind.RANK_CARD,
                    GraphicContent(rank=section.rank),
                    start,
                    duration,
                    reason=f"format profile {self.profile.name}: 'PLATZ N' card per ranked section",
                    origin=Origin.EDITORIAL,
                    priority=0.9,
                    section_id=section.section_id,
                )
            if prof.templates.rank_teaser and i + 1 < len(ranked):
                self.teaser(section, ranked[i + 1])

    def teaser(self, current: BriefSection, upcoming: BriefSection) -> None:
        product = self.products.get(upcoming.product_id or "")
        request_id = f"teaser:{upcoming.section_id}"
        if product is None or not product.image_asset_ids:
            self.issues.append(
                issue(
                    "teaser.no_image",
                    Severity.INFO,
                    f"no product picture for rank {upcoming.rank}: no teaser",
                    request_id,
                )
            )
            return
        timing = self.profile_for(current.section_id).timing
        duration = (
            _f(self.m.entry_max)
            + _f(self.m.min_hold) * _seconds(timing.teaser_factor)
            + _f(self.m.exit_max)
        )
        lead = _f(self.m.min_hold) * _seconds(timing.teaser_lead_factor)
        start = _f(upcoming.timeline_start) - lead - duration
        if start < _f(current.timeline_start):
            self.issues.append(
                issue(
                    "teaser.no_room",
                    Severity.INFO,
                    f"section {current.section_id} is too short for the teaser",
                    request_id,
                )
            )
            return
        self.need(request_id, product, "silhouette")
        self.add(
            request_id,
            GraphicKind.SILHOUETTE_TEASER,
            GraphicContent(
                rank=upcoming.rank,
                image_asset_ids=product.image_asset_ids[:1],
                source=product.source,
            ),
            start,
            duration,
            reason=f"format profile {self.profile.name}: black silhouette of the next rank",
            origin=Origin.EDITORIAL,
            priority=0.6,
            section_id=current.section_id,
        )

    def prices(self) -> None:
        """A price tag at every spoken price an editorial card did not already show."""
        for product in sorted(
            self.brief.products,
            key=lambda p: (_f(p.timeline_at) if p.timeline_at else Fraction(0), p.product_id),
        ):
            prof = self.profile_for(product.section_id)
            if (
                not prof.templates.price_tags
                or product.price_cents is None
                or product.timeline_at is None
                or product.product_id in self.shown_facts
            ):
                continue
            request_id = f"price:{product.product_id}"
            start = self.spoken(product.timeline_at, Fraction(0), request_id, "price")
            duration = self.text_duration(product.name, prof.timing.hold_factor, product.section_id)
            if self.already_shown(request_id, _price_texts(product.price_cents), start, duration):
                continue
            self.shown_facts.add(product.product_id)
            self.add(
                request_id,
                GraphicKind.PRICE_TAG,
                GraphicContent(
                    headline=product.name, price_cents=product.price_cents, source=product.source
                ),
                start,
                duration,
                reason=f"format profile {self.profile.name}: price tag when the price is spoken",
                origin=Origin.EDITORIAL,
                priority=0.5,
                section_id=product.section_id,
            )

    def one_per_zone(self) -> None:
        """No two cards in one zone at once: the higher priority keeps its time; a lower one is
        queued after it by at most one hold (inside its section), else dropped; both recorded."""
        kept: list[GraphicRequest] = []
        for r in sorted(self.requests, key=lambda r: (-r.priority, _start(r), r.request_id)):
            zone = KIND_LAYOUT[r.kind][1] if r.kind in KIND_LAYOUT else ""
            if r.timing.timeline_start is None or not zone:
                kept.append(r)
                continue
            start, length = _start(r), _f(r.timing.local_duration)
            hold = _f(self.m.min_hold) * _seconds(self.profile_for(r.section_id).timing.hold_factor)
            end = self.end_of(r.section_id)
            moved = start
            for k in sorted(kept, key=_start):
                same = k.kind in KIND_LAYOUT and KIND_LAYOUT[k.kind][1] == zone
                a = _start(k)
                b = a + _f(k.timing.local_duration)
                if (
                    same
                    and k.timing.timeline_start is not None
                    and a < moved + length
                    and moved < b
                ):
                    moved = b
            if moved == start:
                kept.append(r)
            elif moved - start <= hold and (end is None or moved + length <= end):
                kept.append(replace(r, timing=replace(r.timing, timeline_start=_r(moved))))
                self.issues.append(
                    issue(
                        "layout.zone_queued",
                        Severity.INFO,
                        f"{r.request_id}: {zone} is taken; queued by {float(moved - start):.2f} s",
                        r.request_id,
                    )
                )
            else:
                self.issues.append(
                    issue(
                        "layout.zone_busy",
                        Severity.INFO,
                        f"{r.request_id}: {zone} is taken and there is no room to queue: dropped",
                        r.request_id,
                    )
                )
        self.requests = kept

    def not_on_screen(
        self, spans: list[tuple[Fraction, Fraction]], text: str
    ) -> list[tuple[Fraction, Fraction]]:
        """The parts of a mandatory label's spans where the picture does not already show it."""
        window = _seconds(self.profile.timing.on_screen_window)
        holes = sorted(
            (_f(s.timeline_start) - window, _f(s.timeline_end) + window)
            for s in self.brief.on_screen
            if _bare(text) and _bare(text) in _bare(s.text)
        )
        out: list[tuple[Fraction, Fraction]] = []
        for span in spans:
            pieces = [span]
            for h0, h1 in holes:
                pieces = [
                    p
                    for x0, x1 in pieces
                    for p in ((x0, min(x1, h0)), (max(x0, h1), x1))
                    if p[1] - p[0] >= self.frame()  # a sliver is not a label
                ]
            out += pieces
        if out != spans:
            self.issues.append(
                issue(
                    "editorial.already_on_screen",
                    Severity.INFO,
                    f"the picture already shows {text!r}: the label covers only the rest",
                    "brand:sponsor",
                )
            )
        return out

    # --- brand bookends ---------------------------------------------------------------------

    def bookends(self) -> None:
        total = (
            _f(self.brief.timeline_duration) if self.brief.timeline_duration is not None else None
        )
        end_screen = (
            self.brief.end_screen
            if self.brief.end_screen is not None
            else self.profile.bookends.end_screen
        )
        if end_screen:
            seconds = _f(self.style.end_screen.seconds)
            if total is None:
                self.issues.append(
                    issue(
                        "end_screen.unplaced",
                        Severity.INFO,
                        "the edit length is unknown: the end screen is rendered unplaced",
                        "brand:end_screen",
                    )
                )
                start: Fraction | None = None
            else:
                start = (
                    total  # appended after the content (channel: the end screen never covers it)
                )
            why = (
                "explicitly requested"
                if self.brief.end_screen
                else f"format profile {self.profile.name} mandates the end screen"
            )
            self.add(
                "brand:end_screen",
                GraphicKind.END_SCREEN,
                GraphicContent(),
                start,
                seconds,
                reason=why,
                origin=Origin.BRAND,
                priority=1.0,
                appended=True,
            )
        if self.brief.sponsored and self.profile.bookends.sponsor_label:
            spans = [(_f(s.timeline_start), _f(s.timeline_end)) for s in self.brief.sponsored_spans]
            if not spans and total is not None:
                spans = [(Fraction(0), total)]
            if not spans:
                self.issues.append(
                    issue(
                        "sponsor.no_span",
                        Severity.ERROR,
                        "sponsored, but neither the sponsored parts nor the edit length are"
                        " known: the mandatory 'Werbung' label cannot be placed",
                        "brand:sponsor",
                        fix="supply sponsored_spans or timeline_duration",
                    )
                )
            spans = self.not_on_screen(spans, self.profile.bookends.sponsor_text)
            for i, (a, b) in enumerate(spans):
                self.add(
                    f"brand:sponsor:{i}",
                    GraphicKind.SPONSOR_LABEL,
                    GraphicContent(headline=self.profile.bookends.sponsor_text),
                    a,
                    b - a,
                    reason="style rule: 'Werbung' for the whole sponsored part (mandatory)",
                    origin=Origin.BRAND,
                    priority=1.0,
                    animation=AnimationRequest(entry="cut", exit="cut"),
                )
        if self.brief.membership_plug_at is not None:
            duration = (
                _f(self.m.entry_max)
                + _f(self.m.min_hold) * _seconds(self.profile.timing.card_factor)
                + _f(self.m.exit_max)
            )
            self.add(
                "brand:membership",
                GraphicKind.MEMBERSHIP_PLUG,
                GraphicContent(),
                _f(self.brief.membership_plug_at),
                duration,
                reason="membership plug requested by the edit",
                origin=Origin.BRAND,
                priority=0.8,
            )

    def ordered(self) -> None:
        """The brief's orders, each through its preset (an invalid one is reported, not drawn)."""
        for order in self.brief.orders:
            cps = self.profile_for(order.section_id).timing.reading_cps
            try:
                request = compile_order(order, self.m, reading_cps=cps)
            except InvalidGraphicOrder as exc:
                self.issues.append(
                    issue(
                        "order.invalid",
                        Severity.ERROR,
                        "; ".join(exc.problems),
                        order.request_id or order.kind.value,
                    )
                )
                continue
            end = self.brief.timeline_duration
            start = _start(request)
            if end is not None and start + _f(request.timing.local_duration) > _f(end):
                continue
            self.requests.append(request)

    def give_way(self) -> None:
        """A full-screen product card already shows the product's price and facts: the cards
        planned from the same speech (within 2 s of it) give way to it."""
        shows = [
            (_start(r), _start(r) + _f(r.timing.local_duration))
            for r in self.requests
            if r.kind is GraphicKind.SPEC_CARD and r.content.variant == "showcase"
        ]
        ordered = {o.request_id for o in self.brief.orders}
        self.requests = [
            r
            for r in self.requests
            if r.request_id in ordered
            or r.kind not in (GraphicKind.PRICE_TAG, GraphicKind.SPEC_CARD)
            or not any(
                a - 2 < _start(r) + _f(r.timing.local_duration) and _start(r) < b + 2
                for a, b in shows
            )
        ]

    def run(self) -> PlannedRequests:
        for g in sorted(self.brief.graphics, key=lambda x: (_f(x.timeline_at), x.request_id)):
            self.editorial(g)
        self.ordered()
        self.ranks()
        self.prices()
        self.give_way()
        if self.style.height > self.style.width:  # a Short: the short-form rules
            self.requests, found = short_form(self.requests, self.brief, SPEC_LABELS)
            self.issues += found
        self.bookends()
        self.one_per_zone()
        requests = tuple(
            sorted(
                self.requests,
                key=lambda r: (
                    _f(r.timing.timeline_start) if r.timing.timeline_start else Fraction(-1),
                    r.request_id,
                ),
            )
        )
        return PlannedRequests(
            requests=requests, images=tuple(self.images), issues=tuple(self.issues)
        )


def _start(r: GraphicRequest) -> Fraction:
    return _f(r.timing.timeline_start) if r.timing.timeline_start is not None else Fraction(-1)


def _bare(text: str) -> str:
    return re.sub(r"[^0-9a-zäöüß,€-]", "", text.casefold())


def _price_texts(cents: int) -> tuple[str, ...]:
    """How the price may already be written on screen ('14,00', '14€', '14 euro'); a whole-euro
    price is often the spoken rounding of the exact one the picture shows ('13,99')."""
    euros, rest = divmod(cents, 100)
    exact = f"{euros},{rest:02d}"
    if rest:
        return (exact,)
    rounded = (f"{euros - 1},9",) if euros > 0 else ()
    return (exact, f"{euros},-", f"{euros}€", f"{euros}euro", *rounded)


def _figure_label(
    concept: str, products: Sequence[ProductFacts], g: EditorialGraphic
) -> tuple[str, Rational | None]:
    """What a bare figure is, from the facts heard where it was requested: '79,99' spoken as the
    price of the section's product is its 'Preis'; ('', None) when no fact says so."""
    digits = re.sub(r"\D", "", concept)
    for product in products:
        if product.section_id != g.section_id and product.product_id != g.product_id:
            continue
        if product.price_cents is not None:
            euros, rest = divmod(product.price_cents, 100)
            if digits in {f"{euros}{rest:02d}", str(euros) if not rest else ""}:
                return "Preis", product.timeline_at
        for spec in product.specs:
            if spec.kind in SPEC_LABELS and digits == re.sub(
                r"\D", "", german_number(spec.value)[0]
            ):
                return SPEC_LABELS[spec.kind], spec.timeline_at
    return "", None


def _spec_lines(product: ProductFacts) -> tuple[SpecLine, ...]:
    lines = []
    for spec in sorted(
        product.specs, key=lambda s: _SPEC_ORDER.index(s.kind) if s.kind in _SPEC_ORDER else 99
    ):
        label = SPEC_LABELS.get(spec.kind)
        if label is None:
            continue
        number, _ = german_number(spec.value)
        lines.append(SpecLine(label=label, value=f"{number} {spec.unit}".strip()))
    return tuple(lines)


def plan_requests(
    brief: GraphicsBrief,
    config: MotionGraphicsConfig,
    profile: MotionGraphicsProfile,
    style: StyleFacts,
) -> PlannedRequests:
    return _Planner(brief, config, profile, style).run()
