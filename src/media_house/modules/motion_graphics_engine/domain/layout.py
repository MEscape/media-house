"""Layout engine: one request -> a composition of layers inside the style's zones.

Every colour, size, stroke, radius, glow and zone comes from ``StyleFacts``. Text is fitted
with the font's real widths (``text.fit``) and never drawn below the style's readability floor.
A graphic that cannot be laid out honestly (text overflow, missing image, unknown zone, a chart
without data) is refused with an ERROR; the caller then uses the safe fallback (no graphic).
"""

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import NoReturn

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.model import (
    Composition,
    Element,
    FaceMetrics,
    GraphicContent,
    GraphicRequest,
    LookFacts,
    Rect,
    StyleFacts,
    TextBlock,
    TextLine,
    TextRun,
    ZoneFacts,
)
from media_house.modules.motion_graphics_engine.domain.qc import issue
from media_house.modules.motion_graphics_engine.domain.text import (
    apply_case,
    fit,
    german_number,
    key,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    Anchor,
    ChartKind,
    ElementType,
    GraphicKind,
    OverlayKind,
    QcCategory,
    QcIssue,
    Severity,
)

K = GraphicKind

#: kind -> (style look, default zone, the style ElementKind the zone must accept, anchor)
KIND_LAYOUT: dict[GraphicKind, tuple[str, str, str, Anchor]] = {
    K.TITLE: ("opening_title", "card_safe", "title", Anchor.CENTER),
    K.INTRO: ("opening_title", "card_safe", "title", Anchor.CENTER),
    K.SECTION_TITLE: ("chapter_title", "title_band", "title", Anchor.CENTER),
    K.LOWER_THIRD: ("lower_third", "lower_third_band", "lower_third", Anchor.BOTTOM_LEFT),
    K.CALLOUT: ("callout", "callout_left", "callout", Anchor.CENTER),
    K.TEXT_PANEL: ("lower_third", "callout_left", "callout", Anchor.CENTER),
    K.HIGHLIGHT_PANEL: ("callout", "callout_left", "callout", Anchor.CENTER),
    K.PRICE_TAG: ("price_tag", "callout_right", "price_tag", Anchor.CENTER),
    K.RANK_CARD: ("chapter_title", "lower_third_band", "rank_card", Anchor.CENTER),
    K.SILHOUETTE_TEASER: ("chapter_title", "callout_right", "sticker", Anchor.CENTER),
    K.SPEC_CARD: ("spec_card", "card_safe", "full_screen", Anchor.CENTER),
    K.COMPARISON: ("chapter_title", "card_safe", "full_screen", Anchor.CENTER),
    K.PRODUCT_STICKER: ("callout", "callout_left", "sticker", Anchor.CENTER),
    K.FEATURE_LIST: ("lower_third", "callout_left", "callout", Anchor.CENTER),
    K.STEP_LIST: ("lower_third", "callout_left", "callout", Anchor.CENTER),
    K.CHART: ("lower_third", "card_safe", "full_screen", Anchor.CENTER),
    K.TIMELINE: ("lower_third", "lower_third_band", "lower_third", Anchor.CENTER),
    K.ARROW: ("callout", "callout_left", "callout", Anchor.CENTER),
    K.TRACKING_LABEL: ("callout", "callout_left", "callout", Anchor.CENTER),
    K.CTA: ("cta", "lower_third_band", "cta", Anchor.CENTER),
    K.SPONSOR_LABEL: ("sponsor_label", "sponsor_label", "sponsor_label", Anchor.TOP_RIGHT),
    K.END_SCREEN: ("end_screen_label", "card_safe", "full_screen", Anchor.CENTER),
    K.LOGO_STING: ("end_screen_label", "card_safe", "full_screen", Anchor.CENTER),
    K.MEMBERSHIP_PLUG: ("end_screen_label", "card_safe", "full_screen", Anchor.CENTER),
    K.PIP_FRAME: ("callout", "callout_right", "sticker", Anchor.CENTER),
    K.CAPTION: ("caption.word_group", "caption_band", "caption", Anchor.CENTER),
    K.TRANSITION_OVERLAY: ("callout", "", "", Anchor.CENTER),
}
#: Zone a graphic moves to when its zone is taken by a simultaneous graphic.
ALTERNATE_ZONE: dict[str, str] = {"callout_left": "callout_right", "callout_right": "callout_left"}
#: Emoji presentation selectors and the joiner: part of an emoji, no width of their own.
_JOINERS = frozenset("︎️‍")
#: Chart series colours (semantic meanings of the style), used in order.
_SERIES = ("chart_a", "chart_b", "chart_c", "chart_d")


@dataclass(frozen=True, slots=True, kw_only=True)
class LayoutInputs:
    style: StyleFacts
    metrics: Mapping[str, FaceMetrics]
    #: asset id -> (width, height) of every supplied image.
    images: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    rank_label: str = "Platz"


def default_zone(kind: GraphicKind, look: LookFacts | None = None) -> str:
    if kind is K.CAPTION and look is not None:
        return look.zone
    return KIND_LAYOUT[kind][1]


def caption_look(request: GraphicRequest, style: StyleFacts) -> str:
    name = request.content.look or style.default_caption_look
    return f"caption.{name}"


def lay_out(
    request: GraphicRequest, inputs: LayoutInputs, zone_override: str | None = None
) -> tuple[Composition | None, tuple[QcIssue, ...]]:
    builder = _Builder(request, inputs, zone_override)
    try:
        builder.setup()
        composition = builder.build()
    except _Refused:
        composition = None
    return composition, tuple(builder.issues)


class _Refused(Exception):
    pass


@dataclass(slots=True)
class _Text:
    block: TextBlock
    width: int
    height: int


class _Builder:
    def __init__(
        self, request: GraphicRequest, inputs: LayoutInputs, zone_override: str | None
    ) -> None:
        self.request = request
        self.inputs = inputs
        self.style = inputs.style
        self.g = inputs.style.graphics
        self.issues: list[QcIssue] = []
        self.elements: list[Element] = []
        self.id = request.request_id
        self.zone_override = zone_override
        self._zone: ZoneFacts | None = None
        self.full_frame = request.kind is K.TRANSITION_OVERLAY
        self.element_kind: str | None = None
        self.anchor = Anchor.CENTER

    def setup(self) -> None:
        """Look, zone and anchor of the request (refused when the zone does not take it)."""
        request, zone_override = self.request, self.zone_override
        look_name, zone_name, element_kind, anchor = KIND_LAYOUT[request.kind]
        if request.kind is K.CAPTION:
            look_name = caption_look(request, self.style)
        try:
            self.look = self.style.look(look_name)
        except KeyError:
            self.fail("style.look_missing", f"the style has no {look_name} look")
        if request.kind is K.CAPTION:
            zone_name = self.look.zone
        self.element_kind = element_kind or None
        self.anchor = request.placement.anchor or anchor
        if not self.full_frame:
            wanted = zone_override or request.placement.zone or zone_name
            zone = self.style.zone(wanted)
            if zone is None:
                self.fail("zone.unknown", f"no zone {wanted!r} in this output format")
            if zone.kind == "keep_out" or element_kind not in zone.accepts:
                self.fail(
                    "zone.wrong_kind",
                    f"{wanted} does not take a {element_kind}",
                    prop="zone",
                    expected=zone_name,
                    actual=wanted,
                )
            self._zone = zone

    # --- issues -----------------------------------------------------------------------------

    def fail(self, code: str, message: str, **detail: str) -> NoReturn:
        self.issues.append(
            issue(
                code,
                Severity.ERROR,
                message,
                self.id,
                prop=detail.get("prop", ""),
                expected=detail.get("expected", ""),
                actual=detail.get("actual", ""),
                fix=detail.get("fix", ""),
            )
        )
        raise _Refused

    def note(self, code: str, message: str, severity: Severity = Severity.INFO) -> None:
        self.issues.append(issue(code, severity, message, self.id, category=QcCategory.CREATIVE))

    # --- primitives -------------------------------------------------------------------------

    def text(
        self,
        eid: str,
        text: str,
        *,
        role: str,
        size_px: int,
        case: str,
        fill: str,
        stroke: str | None,
        stroke_px: int,
        max_width: int,
        max_lines: int,
        max_chars: int,
        align: str = "center",
        explicit: bool = False,
        highlight: str = "",
        max_height: int | None = None,
    ) -> _Text:
        kind = self.style.type(role)
        metrics = self.inputs.metrics.get(kind.face)
        if metrics is None:
            self.fail("font.metrics_missing", f"no metrics for face {kind.face}")
        shown = apply_case(text.strip(), case)
        if not shown:
            self.fail("text.empty", f"{eid}: no text to show", fix="supply the text")
        metrics, emoji = self._with_emoji(metrics, shown)
        width = max_width - 2 * stroke_px
        fitted = fit(
            shown,
            metrics=metrics,
            size_px=size_px,
            min_px=self.style.min_text_px,
            tracking_em=kind.tracking_em,
            max_width=width,
            max_lines=max_lines,
            max_chars=max_chars,
            explicit_lines=explicit,
            space_px=stroke_px,
            leading=kind.leading,
            max_height=None if max_height is None else max_height - 2 * stroke_px,
        )
        if fitted is None:
            self.fail(
                "text.overflow",
                f"{eid}: {shown!r} does not fit {max_lines} line(s) of {width} px at or above"
                f" {self.style.min_text_px} px",
                prop="text",
                expected=f"<= {max_lines} lines, <= {max_chars} chars",
                actual=shown,
                fix="shorten the text",
            )
        if fitted.size_px < size_px:
            self.note("text.shrunk", f"{eid}: {size_px} px -> {fitted.size_px} px to fit")
            # the outline token belongs to the look size: keep its proportion when shrunk
            stroke_px = max(1, round(stroke_px * fitted.size_px / size_px)) if stroke_px else 0
        marks = {key(w) for w in self.request.content.highlight}
        index = 0
        lines: list[TextLine] = []
        for words, line_width in zip(fitted.lines, fitted.widths, strict=True):
            runs = []
            for word in words:
                pure = all(c in emoji for c in word)  # drawn in the emoji face, no outline
                runs.append(
                    TextRun(
                        id=f"{eid}.w{index}", text=word, emphasis=key(word) in marks, emoji=pure
                    )
                )
                index += 1
            lines.append(TextLine(runs=tuple(runs), width_px=line_width))
        face = self.style.face(kind.face)
        line_px = round(fitted.size_px * kind.leading)
        block = TextBlock(
            role=role,
            face=kind.face,
            family=face.family,
            weight=face.weight,
            size_px=fitted.size_px,
            line_px=line_px,
            tracking_em=kind.tracking_em,
            align=align,
            fill=fill,
            highlight=highlight or self.style.hex("highlight"),
            stroke=stroke if stroke_px > 0 else None,
            stroke_px=stroke_px,
            lines=tuple(lines),
            look=self.look.name,
            emoji_face=self.style.emoji_face if emoji - _JOINERS else "",
        )
        return _Text(
            block=block,
            width=fitted.width + 2 * stroke_px,
            height=line_px * len(lines) + 2 * stroke_px,
        )

    def _with_emoji(self, metrics: FaceMetrics, text: str) -> tuple[FaceMetrics, frozenset[str]]:
        """``metrics`` extended by the emoji in ``text`` (measured with the emoji face, scaled to
        the text face's units; selectors and joiners have no width) and the emoji found."""
        face = self.inputs.metrics.get(self.style.emoji_face) if self.style.emoji_face else None
        if face is None:
            return metrics, frozenset()
        found = frozenset(c for c in text if not metrics.has(c) and (face.has(c) or c in _JOINERS))
        if not found - _JOINERS:
            return metrics, frozenset()
        scale = metrics.units_per_em / face.units_per_em
        added = {ord(c): 0 if c in _JOINERS else round(face.advance(c) * scale) for c in found}
        merged = tuple(sorted({**dict(metrics.advances), **added}.items()))
        return replace(metrics, advances=merged), found

    def look_text(
        self,
        eid: str,
        text: str,
        look: LookFacts,
        max_width: int,
        *,
        role: str | None = None,
        size_px: int | None = None,
        fill: str | None = None,
        max_lines: int | None = None,
        align: str = "center",
        explicit: bool = False,
        max_height: int | None = None,
    ) -> _Text:
        return self.text(
            eid,
            text,
            role=role or look.role,
            size_px=size_px or look.size_px,
            case=look.case,
            fill=fill or look.fill,
            stroke=look.stroke,
            stroke_px=look.stroke_px,
            max_width=max_width,
            max_lines=max_lines or look.max_lines,
            max_chars=look.max_chars,
            align=align,
            explicit=explicit,
            highlight=look.highlight,
            max_height=max_height,
        )

    def add(self, element: Element) -> Element:
        if (element.shadow_px or element.shadow_blur_px) and element.shadow_color is None:
            element = replace(element, shadow_color=self.style.hex("black"))
        self.elements.append(element)
        return element

    def add_text(self, eid: str, t: _Text, left: int, top: int, z: int, **extra: object) -> Rect:
        rect = Rect(left=left, top=top, width=t.width, height=t.height)
        self.add(Element(id=eid, type=ElementType.TEXT, rect=rect, z=z, text=t.block, **extra))  # type: ignore[arg-type]
        return rect

    def panel(
        self,
        eid: str,
        rect: Rect,
        colour: str,
        z: int,
        *,
        name: str = "",
        radius_px: int | None = None,
        **extra: object,
    ) -> None:
        self.add(
            Element(
                id=eid,
                type=ElementType.PANEL,
                rect=rect,
                z=z,
                fill=self.style.hex(colour) if not colour.startswith("#") else colour,
                colour_name=name or colour,
                radius_px=self.g.radius_px if radius_px is None else radius_px,
                **extra,  # type: ignore[arg-type]
            )
        )

    def place(self, width: int, height: int, zone: ZoneFacts | None = None) -> Rect:
        zone = zone or self.zone
        if width > zone.width or height > zone.height:
            self.fail(
                "zone.outside",
                f"{width}x{height} px does not fit {zone.name} ({zone.width}x{zone.height})",
                prop="size",
                expected=f"<= {zone.width}x{zone.height}",
                actual=f"{width}x{height}",
                fix="shorten the content",
            )
        a = self.anchor.value
        if a.endswith("left"):
            left = zone.left
        elif a.endswith("right"):
            left = zone.left + zone.width - width
        else:
            left = zone.left + (zone.width - width) // 2
        if a.startswith("top"):
            top = zone.top
        elif a.startswith("bottom"):
            top = zone.top + zone.height - height
        else:
            top = zone.top + (zone.height - height) // 2
        return Rect(left=left, top=top, width=width, height=height)

    @property
    def zone(self) -> ZoneFacts:
        if self._zone is None:
            self.fail("zone.unknown", "this graphic has no zone")
        return self._zone

    def image(self, index: int = 0) -> tuple[str, int, int]:
        found = self.optional_image(index, required=True)
        if found is None:
            self.fail("image.missing", "no image supplied")
        return found

    def optional_image(
        self, index: int = 0, *, required: bool = False
    ) -> tuple[str, int, int] | None:
        ids = self.request.content.image_asset_ids
        if index >= len(ids):
            if required:
                self.fail(
                    "image.missing",
                    "this graphic needs a prepared image (cut-out) and none was supplied",
                    fix="prepare the cut-out with visual_asset_generation",
                )
            return None
        size = self.inputs.images.get(ids[index])
        if size is None:
            self.fail("image.unknown", f"image {ids[index]} has no known size")
        return ids[index], size[0], size[1]

    def tilt(self, salt: str = "") -> float:
        """A deterministic sticker tilt inside the style's range, sign alternating by id."""
        digest = hashlib.sha256(f"{self.id}{salt}".encode()).digest()
        share = digest[0] / 255
        angle = self.g.tilt_min + (self.g.tilt_max - self.g.tilt_min) * share
        return round(-angle if digest[1] % 2 else angle, 2)

    # --- build ------------------------------------------------------------------------------

    def build(self) -> Composition:
        handler = getattr(self, f"_{self.request.kind.value}")
        handler()
        if not self.elements:
            self.fail("layout.empty", "nothing to draw")
        # a full-frame brand plate is a background, not part of the zone placement
        drawn = [e.rect for e in self.elements if not e.id.endswith(".plate")]
        rect = _union(drawn or [e.rect for e in self.elements])
        if self._zone is not None and not self.full_frame:
            zone_rect = Rect(
                left=self._zone.left,
                top=self._zone.top,
                width=self._zone.width,
                height=self._zone.height,
            )
            if not rect.inside(zone_rect):
                self.fail(
                    "zone.outside",
                    f"the graphic leaves {self._zone.name}",
                    prop="rect",
                    expected=f"inside {self._zone.name}",
                    actual=f"{rect.left},{rect.top} {rect.width}x{rect.height}",
                )
        return Composition(
            request_id=self.id,
            kind=self.request.kind,
            zone=self._zone.name if self._zone else None,
            element_kind=self.element_kind,
            rect=rect,
            elements=tuple(sorted(self.elements, key=lambda e: (e.z, e.id))),
            width=self.style.width,
            height=self.style.height,
            full_frame=self.full_frame,
        )

    # --- text graphics ----------------------------------------------------------------------

    def _headline_and_body(
        self, *, panel: str | None, accent: bool = False, fill: str | None = None
    ) -> None:
        c, look = self.request.content, self.look
        pad = self.g.padding_px if panel else 0
        bar = max(4, self.g.spacing_px // 4) if accent else 0
        width = self.zone.width - 2 * pad - bar
        blocks: list[tuple[str, _Text]] = []
        if c.headline:
            blocks.append(
                (
                    "head",
                    self.look_text(
                        f"{self.id}.head",
                        c.headline,
                        look,
                        width,
                        fill=fill,
                        align="left" if accent else "center",
                    ),
                )
            )
        if c.body:
            label = self.style.type("label")
            sub_role = "label" if accent else look.role
            blocks.append(
                (
                    "body",
                    self.text(
                        f"{self.id}.body",
                        "\n".join(c.body),
                        role=sub_role,
                        size_px=label.size_px
                        if accent
                        else max(self.style.min_text_px, round(look.size_px * 0.75)),
                        case=label.case if accent else look.case,
                        # the opening title's gold headline sits above a white line
                        fill=fill or (self.style.hex("white") if look.glow else look.fill),
                        stroke=look.stroke,
                        stroke_px=look.stroke_px,
                        max_width=width,
                        max_lines=max(look.max_lines, len(c.body)),
                        max_chars=look.max_chars,
                        align="left" if accent else "center",
                        explicit=True,
                        highlight=look.highlight,
                    ),
                )
            )
        if not blocks:
            self.fail("text.empty", "no headline or body text", fix="supply the text")
        gap = self.g.spacing_px // 3
        inner_w = max(t.width for _, t in blocks)
        inner_h = sum(t.height for _, t in blocks) + gap * (len(blocks) - 1)
        rect = self.place(inner_w + 2 * pad + bar, inner_h + 2 * pad)
        if panel:
            self.panel(f"{self.id}.panel", rect, panel, 0, opacity=self.g.panel_opacity)
        if bar:
            self.panel(
                f"{self.id}.accent",
                Rect(left=rect.left, top=rect.top, width=bar, height=rect.height),
                "accent",
                1,
                radius_px=0,
                reveal_from="top",
            )
        top = rect.top + pad
        for name, t in blocks:
            left = rect.left + bar + pad
            if not accent:
                left = rect.left + (rect.width - t.width) // 2
            extra: dict[str, object] = {}
            if look.glow and name == "head":
                extra = {
                    "glow": self.style.hex("glow"),
                    "glow_px": self.g.title_glow_px,
                    "glow_opacity": self.g.title_glow_opacity,
                }
            self.add_text(f"{self.id}.{name}", t, left, top, 2, **extra)
            top += t.height + gap

    def _title(self) -> None:
        self._headline_and_body(panel=None)

    def _intro(self) -> None:
        self._headline_and_body(panel=None)

    def _section_title(self) -> None:
        self._headline_and_body(panel=None)

    def _lower_third(self) -> None:
        self._headline_and_body(panel=self.look.panel, accent=True)

    def _callout(self) -> None:
        self._headline_and_body(panel=None)

    def _text_panel(self) -> None:
        self._headline_and_body(panel=self.look.panel)

    def _highlight_panel(self) -> None:
        self._headline_and_body(panel="highlight", fill=self.style.hex("price_text"))

    def _cta(self) -> None:
        content = self.request.content
        text = content.headline or self.style.end_screen.subscribe_label
        pad = self.g.padding_px
        t = self.look_text(f"{self.id}.head", text, self.look, self.zone.width - 2 * pad)
        rect = self.place(t.width + 2 * pad, t.height + pad)
        self.panel(f"{self.id}.panel", rect, "accent", 0, reveal_from="center")
        self.add_text(
            f"{self.id}.head",
            t,
            rect.left + pad,
            rect.top + pad // 2,
            2,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )

    def _sponsor_label(self) -> None:
        text = self.request.content.headline
        if not text:
            self.fail("text.empty", "sponsor label without text")
        t = self.look_text(f"{self.id}.head", text, self.look, self.zone.width)
        rect = self.place(t.width, t.height)
        self.add_text(f"{self.id}.head", t, rect.left, rect.top, 2)

    def _rank_card(self) -> None:
        rank = self.request.content.rank
        if rank is None or rank < 1:
            self.fail("rank.missing", "a rank card needs a rank >= 1", fix="supply the rank")
        text = f"{self.inputs.rank_label} {rank}"
        t = self.text(
            f"{self.id}.head",
            text,
            role=self.look.role,
            size_px=self.look.size_px,
            case=self.look.case,
            fill=self.look.fill,
            stroke=self.look.stroke,
            stroke_px=self.g.rank_stroke_px,
            max_width=self.zone.width,
            max_lines=1,
            max_chars=self.look.max_chars,
        )
        rect = self.place(t.width, t.height)
        self.add_text(
            f"{self.id}.head",
            t,
            rect.left,
            rect.top,
            2,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )

    def _price_tag(self) -> None:
        c = self.request.content
        if c.price_cents is None or not c.price_text:
            self.fail(
                "price.missing",
                "a price tag needs an exact price (from commerce facts) formatted by the style",
                fix="supply price_cents",
            )
        pad = self.g.padding_px
        price = self.look_text(
            f"{self.id}.price", c.price_text, self.look, self.zone.width - 2 * pad
        )
        label: _Text | None = None
        if c.headline:
            label_type = self.style.type("label")
            label = self.text(
                f"{self.id}.label",
                c.headline,
                role="label",
                size_px=label_type.size_px,
                case=label_type.case,
                fill=self.style.hex("white"),
                stroke=self.style.hex("black"),
                stroke_px=self.g.text_stroke_px // 2,
                max_width=self.zone.width,
                max_lines=2,
                max_chars=self.look.max_chars * 2,
            )
        gap = self.g.spacing_px // 3
        tag_w, tag_h = price.width + 2 * pad, price.height + pad
        total_w = max(tag_w, label.width if label else 0)
        total_h = tag_h + (label.height + gap if label else 0)
        rect = self.place(total_w, total_h)
        top = rect.top
        if label:
            self.add_text(
                f"{self.id}.label", label, rect.left + (total_w - label.width) // 2, top, 3
            )
            top += label.height + gap
        tag = Rect(left=rect.left + (total_w - tag_w) // 2, top=top, width=tag_w, height=tag_h)
        rotation = -self.g.tilt_min / 2
        self.panel(
            f"{self.id}.panel",
            tag,
            self.look.panel,
            0,
            name="price_fill",
            rotation=rotation,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )
        self.add_text(
            f"{self.id}.price", price, tag.left + pad, tag.top + pad // 2, 2, rotation=rotation
        )

    def _spec_card(self) -> None:
        """Black card: product name, rows "SCHUSS 100" ..., product cut-out left when given.
        The card fits its content (centred in the card zone), never a half-empty plate."""
        c = self.request.content
        if not c.specs and not c.price_text:
            self.fail("specs.missing", "a spec card needs facts (Schuss, Höhe, Brenndauer...)")
        zone, pad, gap = self.zone, self.g.padding_px, self.g.spacing_px
        image = self.optional_image(0)
        rows = [(s.label, s.value) for s in c.specs]
        if c.price_text:
            rows.append(("Kosten", c.price_text))
        label_type, look = self.style.type("label"), self.look
        headline = self.style.type("headline")
        room = zone.width - 2 * pad - (zone.width * 2 // 5 if image else 0)
        head: _Text | None = None
        if c.headline:
            head = self.text(
                f"{self.id}.head",
                c.headline,
                role="headline",
                size_px=headline.size_px * 2 // 3,
                case="as_written",
                fill=look.fill,
                stroke=None,
                stroke_px=0,
                max_width=room,
                max_lines=2,
                max_chars=look.max_chars,
                align="left",
            )
        labels = [
            self.text(
                f"{self.id}.l{i}",
                label,
                role="label",
                size_px=label_type.size_px,
                case=label_type.case,
                fill=self.style.hex("highlight"),
                stroke=None,
                stroke_px=0,
                max_width=room // 2,
                max_lines=1,
                max_chars=look.max_chars,
                align="left",
            )
            for i, (label, _) in enumerate(rows)
        ]
        col = max(t.width for t in labels) + gap
        values = [
            self.text(
                f"{self.id}.v{i}",
                value,
                role="number",
                size_px=max(look.size_px, headline.size_px // 2),
                case="as_written",
                fill=look.fill,
                stroke=None,
                stroke_px=0,
                max_width=room - col,
                max_lines=1,
                max_chars=look.max_chars,
                align="left",
            )
            for i, (_, value) in enumerate(rows)
        ]
        row_h = max(max(a.height, b.height) for a, b in zip(labels, values, strict=True)) + gap // 3
        text_w = max([col + v.width for v in values] + ([head.width] if head else []))
        text_h = (head.height + gap if head else 0) + row_h * len(rows)
        image_w = min(zone.width * 2 // 5, text_h * 3 // 2) if image else 0
        card = self.place(text_w + 2 * pad + (image_w + gap if image else 0), text_h + 2 * pad)
        self.panel(f"{self.id}.panel", card, look.panel, 0, opacity=self.g.panel_opacity)
        self.panel(
            f"{self.id}.accent",
            Rect(
                left=card.left,
                top=card.bottom - self.g.graphic_stroke_px,
                width=card.width,
                height=self.g.graphic_stroke_px,
            ),
            "accent",
            1,
            radius_px=0,
        )
        left = card.left + pad + (image_w + gap if image else 0)
        top = card.top + pad
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 2)
            top += head.height + gap
        for i, (a, b) in enumerate(zip(labels, values, strict=True)):
            self.add_text(f"{self.id}.l{i}", a, left, top + (row_h - a.height) // 2, 2)
            self.add_text(f"{self.id}.v{i}", b, left + col, top + (row_h - b.height) // 2, 2)
            top += row_h
        if image:
            box = Rect(
                left=card.left + pad,
                top=card.top + pad,
                width=image_w,
                height=card.height - 2 * pad,
            )
            self._image(f"{self.id}.image", image, box, 3, rotation=self.tilt())

    def _comparison(self) -> None:
        c = self.request.content
        if len(c.body) != 2:
            self.fail("comparison.sides", "a comparison needs exactly two sides (body)")
        zone, pad = self.zone, self.g.padding_px
        half = (zone.width - self.g.spacing_px) // 2
        card = self.place(zone.width, zone.height)
        for i, (side, colour) in enumerate(zip(c.body, ("chart_a", "chart_b"), strict=True)):
            box = Rect(
                left=card.left + i * (half + self.g.spacing_px),
                top=card.top,
                width=half,
                height=card.height,
            )
            self.panel(
                f"{self.id}.side{i}",
                box,
                colour,
                0,
                opacity=self.g.panel_opacity,
                reveal_from="left" if i == 0 else "right",
            )
            label = self.text(
                f"{self.id}.label{i}",
                side,
                role="headline",
                size_px=self.style.type("headline").size_px * 3 // 4,
                case="upper",
                fill=self.style.hex("white"),
                stroke=self.style.hex("black"),
                stroke_px=self.g.text_stroke_px,
                max_width=half - 2 * pad,
                max_lines=2,
                max_chars=self.look.max_chars,
            )
            self.add_text(
                f"{self.id}.label{i}", label, box.left + (half - label.width) // 2, box.top + pad, 2
            )
            image = self.optional_image(i)
            if image:
                inner = Rect(
                    left=box.left + pad,
                    top=box.top + 2 * pad + label.height,
                    width=half - 2 * pad,
                    height=box.height - 3 * pad - label.height,
                )
                self._image(f"{self.id}.image{i}", image, inner, 3, rotation=self.tilt(str(i)))
        display = self.style.type("display")
        vs = self.text(
            f"{self.id}.vs",
            "VS",
            role="display",
            size_px=display.size_px,
            case="upper",
            fill=self.style.hex("white"),
            stroke=self.style.hex("black"),
            stroke_px=self.g.text_stroke_px,
            max_width=zone.width // 3,
            max_lines=1,
            max_chars=4,
        )
        self.add_text(
            f"{self.id}.vs",
            vs,
            card.left + (card.width - vs.width) // 2,
            card.top + (card.height - vs.height) // 2,
            4,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )

    def _list(self, numbered: bool) -> None:
        c = self.request.content
        if not c.body:
            self.fail("list.empty", "a list needs items (body)")
        pad, gap, look = self.g.padding_px, self.g.spacing_px // 2, self.look
        title: _Text | None = None
        inner_w = self.zone.width - 2 * pad
        if c.headline:
            title = self.look_text(
                f"{self.id}.head",
                c.headline,
                look,
                inner_w,
                role="headline",
                size_px=max(look.size_px, self.style.type("headline").size_px // 2),
                max_lines=2,
                align="left",
            )
        bullet = round(look.size_px * 1.1)
        items = [
            self.look_text(
                f"{self.id}.i{i}", item, look, inner_w - bullet - gap, max_lines=2, align="left"
            )
            for i, item in enumerate(c.body)
        ]
        height = sum(max(t.height, bullet) for t in items) + gap * (len(items) - 1)
        height += title.height + gap if title else 0
        width = max([t.width + bullet + gap for t in items] + ([title.width] if title else []))
        rect = self.place(width + 2 * pad, height + 2 * pad)
        self.panel(f"{self.id}.panel", rect, look.panel, 0, opacity=self.g.panel_opacity)
        top, left = rect.top + pad, rect.left + pad
        if title:
            self.add_text(f"{self.id}.head", title, left, top, 2)
            top += title.height + gap
        for i, t in enumerate(items):
            row = max(t.height, bullet)
            mark = Rect(left=left, top=top + (row - bullet) // 2, width=bullet, height=bullet)
            if numbered:
                self.add(
                    Element(
                        id=f"{self.id}.b{i}",
                        type=ElementType.PANEL,
                        rect=mark,
                        z=1,
                        fill=self.style.hex("accent"),
                        colour_name="accent",
                        radius_px=bullet // 2,
                    )
                )
                num = self.text(
                    f"{self.id}.n{i}",
                    str(i + 1),
                    role="number",
                    size_px=max(self.style.min_text_px, bullet * 2 // 3),
                    case="as_written",
                    fill=self.style.hex("white"),
                    stroke=None,
                    stroke_px=0,
                    max_width=bullet,
                    max_lines=1,
                    max_chars=3,
                )
                self.add_text(
                    f"{self.id}.n{i}",
                    num,
                    mark.left + (bullet - num.width) // 2,
                    mark.top + (bullet - num.height) // 2,
                    3,
                )
            else:
                dot = bullet // 3
                self.add(
                    Element(
                        id=f"{self.id}.b{i}",
                        type=ElementType.PANEL,
                        rect=Rect(left=mark.left + dot, top=mark.top + dot, width=dot, height=dot),
                        z=1,
                        fill=self.style.hex("highlight"),
                        colour_name="highlight",
                        rotation=45.0,
                    )
                )
            self.add_text(f"{self.id}.i{i}", t, left + bullet + gap, top + (row - t.height) // 2, 2)
            top += row + gap

    def _feature_list(self) -> None:
        self._list(numbered=False)

    def _step_list(self) -> None:
        self._list(numbered=True)

    # --- image graphics ---------------------------------------------------------------------

    def _image(
        self, eid: str, image: tuple[str, int, int], box: Rect, z: int, *, rotation: float = 0.0
    ) -> Rect:
        asset_id, w, h = image
        scale = min(box.width / w, box.height / h)
        # leave room for the tilt: the rotated corners stay inside the box
        tilt = math.radians(abs(rotation))
        scale /= math.cos(tilt) + math.sin(tilt) * max(w / h, h / w)
        width, height = max(1, int(w * scale)), max(1, int(h * scale))
        rect = Rect(
            left=box.left + (box.width - width) // 2,
            top=box.top + (box.height - height) // 2,
            width=width,
            height=height,
        )
        self.add(
            Element(
                id=eid,
                type=ElementType.IMAGE,
                rect=rect,
                z=z,
                image_asset_id=asset_id,
                rotation=rotation,
            )
        )
        return rect

    def _product_sticker(self) -> None:
        image = self.image(0)
        box = self.place(self.zone.width, self.zone.height)
        self._image(f"{self.id}.image", image, box, 1, rotation=self.tilt())

    def _silhouette_teaser(self) -> None:
        image = self.image(0)
        box = self.place(self.zone.width, self.zone.height)
        rect = self._image(f"{self.id}.image", image, box, 1, rotation=self.tilt())
        rank = self.request.content.rank
        if rank is not None:
            number = self.style.type("number")
            t = self.text(
                f"{self.id}.rank",
                str(rank),
                role="number",
                size_px=number.size_px,
                case="as_written",
                fill=self.look.fill,
                stroke=self.look.stroke,
                stroke_px=self.g.rank_stroke_px,
                max_width=box.width // 2,
                max_lines=1,
                max_chars=4,
            )
            left = min(rect.right - t.width // 2, box.right - t.width)
            top = min(rect.bottom - t.height // 2, box.bottom - t.height)
            self.add_text(
                f"{self.id}.rank",
                t,
                left,
                top,
                2,
                shadow_px=self.g.hard_shadow_px,
                shadow_opacity=self.g.hard_shadow_opacity,
            )

    def _logo_sting(self) -> None:
        if not self.request.content.image_asset_ids:
            self.fail(
                "brand.asset_missing",
                "a logo sting needs the channel logo asset (slot 'logo'), which does not exist yet",
                fix="bind the logo in visual_style_engine",
            )
        image = self.image(0)
        box = self.place(self.zone.width // 2, self.zone.height // 2)
        self._image(f"{self.id}.image", image, box, 1)

    def _pip_frame(self) -> None:
        width = self.zone.width
        height = width * 9 // 16
        if height > self.zone.height:
            height, width = self.zone.height, self.zone.height * 16 // 9
        rect = self.place(width, height)
        self.add(
            Element(
                id=f"{self.id}.frame",
                type=ElementType.PANEL,
                rect=rect,
                z=1,
                stroke=self.style.hex("white"),
                stroke_px=self.g.pip_border_px,
                radius_px=self.g.radius_px,
                shadow_blur_px=self.g.soft_shadow_px,
                shadow_opacity=self.g.soft_shadow_opacity,
            )
        )

    # --- full-screen brand cards ------------------------------------------------------------

    def _plate(self, slot: str) -> None:
        plate = self.style.brand_asset(slot)
        if plate is None:
            self.issues.append(
                issue(
                    "brand.asset_missing",
                    Severity.WARNING,
                    f"brand asset {slot!r} does not exist yet: drawn without a plate",
                    self.id,
                    fix=f"bind the {slot} asset in visual_style_engine",
                )
            )
            return
        size = self.inputs.images.get(plate)
        if size is None:
            self.fail("image.unknown", f"brand plate {plate} has no known size")
        self.add(
            Element(
                id=f"{self.id}.plate",
                type=ElementType.IMAGE,
                rect=Rect(left=0, top=0, width=self.style.width, height=self.style.height),
                z=0,
                image_asset_id=plate,
            )
        )

    def _display_label(self, eid: str, text: str, max_width: int) -> _Text:
        return self.look_text(eid, text, self.look, max_width)

    def _end_screen(self) -> None:
        es, zone = self.style.end_screen, self.zone
        self._plate(es.background_slot)
        # YouTube end-screen element slots (16:9 video boxes) left and right of centre; the
        # labels sit above them, the slots themselves stay empty for the platform.
        slot_w = zone.width * 2 // 5
        slot_h = slot_w * 9 // 16
        slot_top = zone.top + (zone.height - slot_h) // 2 + slot_h // 6
        for i, label in enumerate((es.subscribe_label, es.watch_next_label)):
            t = self._display_label(f"{self.id}.label{i}", label, slot_w)
            slot_left = zone.left + (zone.width // 2 - slot_w) // 2 + i * zone.width // 2
            self.add_text(
                f"{self.id}.label{i}",
                t,
                slot_left + (slot_w - t.width) // 2,
                slot_top - t.height - self.g.spacing_px,
                2,
                shadow_px=self.g.hard_shadow_px,
                shadow_opacity=self.g.hard_shadow_opacity,
            )

    def _membership_plug(self) -> None:
        self._plate("membership_plate")
        text = self.request.content.headline or self.style.end_screen.membership_label
        t = self._display_label(f"{self.id}.head", text, self.zone.width)
        rect = self.place(t.width, t.height)
        self.add_text(
            f"{self.id}.head",
            t,
            rect.left,
            rect.top,
            2,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )

    # --- data graphics ----------------------------------------------------------------------

    def _number(self, value_index: int = 0) -> str:
        point = self.request.content.data[value_index]
        text, exact = german_number(point.value)
        if not exact:
            self.note("data.rounded", f"{point.label}: shown rounded as {text}")
        return f"{text} {point.unit}".strip()

    def _chart(self) -> None:
        c = self.request.content
        if not c.data or c.chart is None:
            self.fail(
                "chart.no_data",
                "a chart needs supplied data and a chart kind; nothing is invented",
                fix="supply data points",
            )
        if any(Fraction(p.value.numerator, p.value.denominator) < 0 for p in c.data):
            self.fail("chart.negative", "negative values are not supported")
        if c.chart is ChartKind.COUNTER:
            self._counter()
            return
        if c.chart is ChartKind.PROGRESS:
            self._progress()
            return
        zone, pad, gap = self.zone, self.g.padding_px, self.g.spacing_px
        card = self.place(zone.width, zone.height)
        self.panel(f"{self.id}.panel", card, self.look.panel, 0, opacity=self.g.panel_opacity)
        top = card.top + pad
        if c.headline:
            head = self.look_text(
                f"{self.id}.head",
                c.headline,
                self.look,
                card.width - 2 * pad,
                role="headline",
                size_px=self.style.type("headline").size_px * 2 // 3,
                max_lines=1,
            )
            self.add_text(
                f"{self.id}.head", head, card.left + (card.width - head.width) // 2, top, 2
            )
            top += head.height + gap
        plot = Rect(
            left=card.left + pad,
            top=top,
            width=card.width - 2 * pad,
            height=card.bottom - pad - top,
        )
        if c.chart is ChartKind.BAR:
            self._bars(plot)
        elif c.chart is ChartKind.LINE:
            self._line(plot)
        else:
            self._pie(plot)

    def _small(
        self, eid: str, text: str, max_width: int, role: str = "label", outlined: bool = False
    ) -> _Text:
        """Small text; on a panel it needs no outline, over footage it does (locked rule)."""
        kind = self.style.type(role)
        stroke = self.style.hex("black") if outlined else None
        return self.text(
            eid,
            text,
            role=role,
            size_px=kind.size_px,
            case=kind.case,
            fill=self.style.hex("white"),
            stroke=stroke,
            stroke_px=self.g.text_stroke_px // 2 if outlined else 0,
            max_width=max_width,
            max_lines=2,
            max_chars=40,
        )

    def _bars(self, plot: Rect) -> None:
        data = self.request.content.data
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        top_value = max(values) or Fraction(1)
        gap = self.g.spacing_px
        slot = (plot.width - gap * (len(data) - 1)) // len(data)
        labels = [self._small(f"{self.id}.l{i}", p.label, slot) for i, p in enumerate(data)]
        numbers = [
            self._small(f"{self.id}.v{i}", self._number(i), slot, role="number")
            for i in range(len(data))
        ]
        label_h = max(t.height for t in labels)
        value_h = max(t.height for t in numbers)
        base = plot.bottom - label_h - gap // 2
        room = base - plot.top - value_h - gap // 2
        if room < self.style.min_text_px:
            self.fail("chart.too_small", "the bars have no room in this zone")
        for i, value in enumerate(values):
            height = max(1, round(room * value / top_value))
            left = plot.left + i * (slot + gap)
            bar = Rect(left=left, top=base - height, width=slot, height=height)
            self.panel(
                f"{self.id}.bar{i}",
                bar,
                _SERIES[0],
                1,
                reveal_from="bottom",
                radius_px=self.g.radius_px // 2,
            )
            self.add_text(
                f"{self.id}.v{i}",
                numbers[i],
                left + (slot - numbers[i].width) // 2,
                bar.top - value_h - gap // 4,
                2,
            )
            self.add_text(
                f"{self.id}.l{i}",
                labels[i],
                left + (slot - labels[i].width) // 2,
                base + gap // 2,
                2,
            )

    def _line(self, plot: Rect) -> None:
        data = self.request.content.data
        if len(data) < 2:
            self.fail("chart.no_data", "a line chart needs at least two points")
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        top_value = max(values) or Fraction(1)
        labels = [
            self._small(f"{self.id}.l{i}", p.label, plot.width // len(data))
            for i, p in enumerate(data)
        ]
        label_h = max(t.height for t in labels)
        area = Rect(
            left=plot.left,
            top=plot.top + label_h,
            width=plot.width,
            height=plot.height - 2 * label_h - self.g.spacing_px,
        )
        step = area.width / (len(data) - 1)
        points = [
            (round(i * step), round(area.height - area.height * v / top_value))
            for i, v in enumerate(values)
        ]
        path = "M " + " L ".join(f"{x} {y}" for x, y in points)
        self.add(
            Element(
                id=f"{self.id}.line",
                type=ElementType.SHAPE,
                rect=area,
                z=1,
                stroke=self.style.hex(_SERIES[0]),
                stroke_px=self.g.graphic_stroke_px * 2,
                path=path,
                colour_name=_SERIES[0],
            )
        )
        for i, (x, y) in enumerate(points):
            value = self._small(
                f"{self.id}.v{i}", self._number(i), plot.width // len(data), role="number"
            )
            left = min(max(area.left + x - value.width // 2, plot.left), plot.right - value.width)
            self.add_text(
                f"{self.id}.v{i}",
                value,
                left,
                area.top + y - value.height - self.g.spacing_px // 3,
                2,
            )
            lab = labels[i]
            left = min(max(area.left + x - lab.width // 2, plot.left), plot.right - lab.width)
            self.add_text(f"{self.id}.l{i}", lab, left, area.bottom + self.g.spacing_px // 2, 2)

    def _pie(self, plot: Rect) -> None:
        data = self.request.content.data
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        total = sum(values)
        if total <= 0:
            self.fail("chart.no_data", "a pie chart needs a positive total")
        radius = min(plot.height, plot.width // 2) // 2
        box = Rect(
            left=plot.left + plot.width // 4 - radius,
            top=plot.top + plot.height // 2 - radius,
            width=2 * radius,
            height=2 * radius,
        )
        start = Fraction(0)
        for i, value in enumerate(values):
            end = start + value / total
            path = _wedge(radius, float(start), float(end))
            self.add(
                Element(
                    id=f"{self.id}.wedge{i}",
                    type=ElementType.SHAPE,
                    rect=box,
                    z=1,
                    fill=self.style.hex(_SERIES[i % len(_SERIES)]),
                    colour_name=_SERIES[i % len(_SERIES)],
                    path=path,
                    stroke=self.style.hex("black"),
                    stroke_px=self.g.graphic_stroke_px // 2,
                )
            )
            start = end
        legend_left = plot.left + plot.width // 2 + self.g.spacing_px
        top = plot.top
        swatch = self.style.min_text_px
        for i, p in enumerate(data):
            share, _ = german_number(_rational(values[i] * 100 / total), 1)
            t = self._small(
                f"{self.id}.l{i}",
                f"{p.label}  {share} %",
                plot.right - legend_left - swatch - self.g.spacing_px,
            )
            self.add(
                Element(
                    id=f"{self.id}.s{i}",
                    type=ElementType.PANEL,
                    rect=Rect(
                        left=legend_left,
                        top=top + (t.height - swatch) // 2,
                        width=swatch,
                        height=swatch,
                    ),
                    z=1,
                    fill=self.style.hex(_SERIES[i % len(_SERIES)]),
                    colour_name=_SERIES[i % len(_SERIES)],
                )
            )
            self.add_text(
                f"{self.id}.l{i}", t, legend_left + swatch + self.g.spacing_px // 2, top, 2
            )
            top += t.height + self.g.spacing_px // 2

    def _progress(self) -> None:
        data = self.request.content.data
        first = Fraction(data[0].value.numerator, data[0].value.denominator)
        if len(data) >= 2:
            total = Fraction(data[1].value.numerator, data[1].value.denominator)
        elif data[0].unit == "%":
            total = Fraction(100)
        else:
            self.fail("chart.no_data", "progress needs a value and a total, or a percentage")
        if total <= 0 or first > total:
            self.fail("chart.range", "progress value must lie between 0 and the total")
        pad = self.g.padding_px
        label = self._small(
            f"{self.id}.v0", self._number(0), self.zone.width - 2 * pad, role="number"
        )
        bar_h = max(self.g.spacing_px, label.height // 2)
        rect = self.place(self.zone.width, label.height + bar_h + 3 * pad)
        self.panel(f"{self.id}.panel", rect, self.look.panel, 0, opacity=self.g.panel_opacity)
        self.add_text(f"{self.id}.v0", label, rect.left + pad, rect.top + pad, 2)
        track = Rect(
            left=rect.left + pad,
            top=rect.bottom - pad - bar_h,
            width=rect.width - 2 * pad,
            height=bar_h,
        )
        self.panel(f"{self.id}.track", track, "black", 1, radius_px=bar_h // 2)
        fill_w = max(1, round(track.width * first / total))
        self.panel(
            f"{self.id}.fill",
            Rect(left=track.left, top=track.top, width=fill_w, height=bar_h),
            "positive",
            2,
            radius_px=bar_h // 2,
            reveal_from="left",
        )

    def _counter(self) -> None:
        c = self.request.content
        number = self.style.type("number")
        t = self.text(
            f"{self.id}.count",
            self._number(0),
            role="number",
            size_px=number.size_px,
            case="as_written",
            fill=self.style.hex("white"),
            stroke=self.style.hex("black"),
            stroke_px=self.g.text_stroke_px,
            max_width=self.zone.width,
            max_lines=1,
            max_chars=16,
        )
        label: _Text | None = None
        if c.data[0].label:
            label = self._small(f"{self.id}.label", c.data[0].label, self.zone.width, outlined=True)
        height = t.height + (label.height if label else 0)
        rect = self.place(max(t.width, label.width if label else 0), height)
        self.add_text(f"{self.id}.count", t, rect.left + (rect.width - t.width) // 2, rect.top, 2)
        if label:
            self.add_text(
                f"{self.id}.label",
                label,
                rect.left + (rect.width - label.width) // 2,
                rect.top + t.height,
                2,
            )

    def _timeline(self) -> None:
        c = self.request.content
        labels = [p.label for p in c.data] or list(c.body)
        if len(labels) < 2:
            self.fail("timeline.items", "a timeline needs at least two labelled moments")
        zone, gap = self.zone, self.g.spacing_px
        slot = zone.width // len(labels)
        texts = [
            self._small(f"{self.id}.l{i}", text, slot - gap, outlined=True)
            for i, text in enumerate(labels)
        ]
        dot = self.style.min_text_px
        height = dot + gap + max(t.height for t in texts)
        rect = self.place(zone.width, height)
        line_y = rect.top + dot // 2 - self.g.graphic_stroke_px // 2
        self.panel(
            f"{self.id}.line",
            Rect(
                left=rect.left + slot // 2,
                top=line_y,
                width=slot * (len(labels) - 1),
                height=self.g.graphic_stroke_px,
            ),
            "white",
            0,
            radius_px=0,
            reveal_from="left",
        )
        for i, t in enumerate(texts):
            centre = rect.left + i * slot + slot // 2
            self.add(
                Element(
                    id=f"{self.id}.d{i}",
                    type=ElementType.PANEL,
                    rect=Rect(left=centre - dot // 2, top=rect.top, width=dot, height=dot),
                    z=1,
                    fill=self.style.hex("accent"),
                    colour_name="accent",
                    radius_px=dot // 2,
                    stroke=self.style.hex("white"),
                    stroke_px=self.g.graphic_stroke_px,
                )
            )
            self.add_text(f"{self.id}.l{i}", t, centre - t.width // 2, rect.top + dot + gap, 2)

    # --- pointers and tracking --------------------------------------------------------------

    def _arrow(self) -> None:
        size = min(self.zone.width, self.zone.height) * 3 // 5
        rect = self.place(size, size)
        target = self.request.placement.track
        angle = 0.0
        if target:
            tx, ty = target[0].x * self.style.width, target[0].y * self.style.height
            cx, cy = rect.left + size / 2, rect.top + size / 2
            angle = round(math.degrees(math.atan2(ty - cy, tx - cx)), 2)
        s = size
        outline: tuple[tuple[float, float], ...] = ((0.08, 0.38), (0.55, 0.38), (0.55, 0.18))
        outline += ((0.94, 0.5), (0.55, 0.82), (0.55, 0.62), (0.08, 0.62))
        path = "M " + " L ".join(f"{s * x:.1f} {s * y:.1f}" for x, y in outline) + " Z"
        self.add(
            Element(
                id=f"{self.id}.arrow",
                type=ElementType.SHAPE,
                rect=rect,
                z=1,
                fill=self.style.hex("white"),
                colour_name="white",
                stroke=self.style.hex("black"),
                stroke_px=self.g.text_stroke_px,
                path=path,
                rotation=angle,
            )
        )

    def _tracking_label(self) -> None:
        track = self.request.placement.track
        if not track:
            self.fail(
                "track.missing",
                "a tracking label needs a supplied track (no tracking is invented)",
                fix="supply track points from video_intelligence",
            )
        c = self.request.content
        zones = [
            z for z in (self.style.zone("callout_left"), self.style.zone("callout_right")) if z
        ]
        first = track[0]
        px, py = first.x * self.style.width, first.y * self.style.height
        if not self.request.placement.zone:
            self._zone = min(
                zones, key=lambda z: abs(z.left + z.width / 2 - px) + abs(z.top + z.height / 2 - py)
            )
        pad = self.g.padding_px // 2
        t = self.look_text(f"{self.id}.head", c.headline, self.look, self.zone.width - 2 * pad)
        width, height = t.width + 2 * pad, t.height + pad
        left, top = self._clamp(px - width / 2, py - height - self.g.spacing_px, width, height)
        rect = Rect(left=left, top=top, width=width, height=height)
        self.panel(f"{self.id}.panel", rect, "panel", 0, opacity=self.g.panel_opacity)
        self.add_text(f"{self.id}.head", t, left + pad, top + pad // 2, 2)

    def _clamp(self, left: float, top: float, width: int, height: int) -> tuple[int, int]:
        z = self.zone
        cl = min(max(round(left), z.left), z.left + z.width - width)
        ct = min(max(round(top), z.top), z.top + z.height - height)
        if (cl, ct) != (round(left), round(top)):
            self.issues.append(
                issue(
                    "track.clamped", Severity.WARNING, f"the label is kept inside {z.name}", self.id
                )
            )
        return cl, ct

    # --- captions (executor for caption_engine) ---------------------------------------------

    def _caption(self) -> None:
        c, look = self.request.content, self.look
        if not c.words:
            self.fail("caption.no_words", "a caption group needs timed words from caption_engine")
        emojis = c.emojis
        if emojis and not (self.style.emoji_face and self.style.emoji_face in self.inputs.metrics):
            self.note(
                "emoji.not_available",
                "the style has no emoji face (identity v1): the group is drawn without its emoji",
                Severity.WARNING,
            )
            emojis = ()
        # (word index, None) or (None, emoji index), in reading order: an emoji follows its word
        tokens: list[tuple[int | None, int | None]] = []
        for i in range(len(c.words)):
            tokens.append((i, None))
            tokens += [(None, k) for k, e in enumerate(emojis) if e.after_word == i]
        texts = [c.words[w].text if w is not None else emojis[e or 0].emoji for w, e in tokens]
        text = " ".join(texts)
        shown = apply_case(text, look.case)
        if len(shown.split()) != len(tokens):
            self.fail("caption.words", "every timed word must be one word (no spaces inside)")
        t = self.look_text(
            f"{self.id}.text", text, look, self.zone.width, max_height=self.zone.height
        )
        order = iter(tokens)
        lines = tuple(
            TextLine(
                runs=tuple(_caption_run(self.id, r, next(order), c) for r in line.runs),
                width_px=line.width_px,
            )
            for line in t.block.lines
        )
        block = replace(t.block, lines=lines)
        rect = self.place(t.width, t.height)
        self.add(
            Element(
                id=f"{self.id}.text",
                type=ElementType.TEXT,
                rect=rect,
                z=2,
                text=block,
                shadow_blur_px=self.g.soft_shadow_px // 2,
                shadow_opacity=self.g.soft_shadow_opacity,
            )
        )

    # --- transition overlays (executor for transition_engine) -------------------------------

    def _transition_overlay(self) -> None:
        overlay = self.request.content.overlay
        if overlay is None:
            self.fail("overlay.kind", "a transition overlay needs its kind")
        w, h = self.style.width, self.style.height
        frame = Rect(left=0, top=0, width=w, height=h)
        if overlay is OverlayKind.WIPE:
            self.add(
                Element(
                    id=f"{self.id}.a",
                    type=ElementType.PANEL,
                    rect=frame,
                    z=0,
                    fill=self.style.hex("accent"),
                    colour_name="accent",
                )
            )
            self.add(
                Element(
                    id=f"{self.id}.b",
                    type=ElementType.PANEL,
                    rect=frame,
                    z=1,
                    fill=self.style.hex("panel"),
                    colour_name="panel",
                )
            )
        elif overlay in (OverlayKind.SHAPE, OverlayKind.MASK):
            d = math.ceil(math.hypot(w, h))
            circle = Rect(left=(w - d) // 2, top=(h - d) // 2, width=d, height=d)
            colour = "accent" if overlay is OverlayKind.SHAPE else "black"
            self.add(
                Element(
                    id=f"{self.id}.a",
                    type=ElementType.PANEL,
                    rect=circle,
                    z=0,
                    fill=self.style.hex(colour),
                    colour_name=colour,
                    radius_px=d // 2,
                )
            )
        elif overlay is OverlayKind.ZOOM_TUNNEL:
            d = math.ceil(math.hypot(w, h))
            for i, colour in enumerate(
                ("fire_dark", "fire_mid", "fire_hot", "fire_glow", "fire_hot", "fire_dark")
            ):
                self.add(
                    Element(
                        id=f"{self.id}.r{i}",
                        type=ElementType.PANEL,
                        rect=Rect(left=(w - d) // 2, top=(h - d) // 2, width=d, height=d),
                        z=i,
                        fill=None,
                        stroke=self.style.hex(colour),
                        stroke_px=max(8, d // 14),
                        radius_px=d // 2,
                        colour_name=colour,
                    )
                )
        else:
            rows = 9
            cell = math.ceil(h / rows)
            cols = math.ceil(w / cell)
            colours = ("fire_dark", "fire_mid", "fire_hot", "black")
            for r in range(rows):
                for col in range(cols):
                    name = colours[_hash(f"{self.id}{r}.{col}") % len(colours)]
                    self.add(
                        Element(
                            id=f"{self.id}.p{r}_{col}",
                            type=ElementType.PANEL,
                            rect=Rect(left=col * cell, top=r * cell, width=cell, height=cell),
                            z=0,
                            fill=self.style.hex(name),
                            colour_name=name,
                        )
                    )


def _caption_run(
    rid: str, run: TextRun, token: tuple[int | None, int | None], content: GraphicContent
) -> TextRun:
    """A caption run with its stable id: ``.w<i>`` = word ``i``, ``.e<k>`` = emoji ``k``."""
    word, emoji = token
    if word is None:
        return TextRun(id=f"{rid}.text.e{emoji}", text=run.text, emoji=True)
    emphasis = content.words[word].emphasis
    return TextRun(id=f"{rid}.text.w{word}", text=run.text, emphasis=emphasis, emoji=run.emoji)


def _union(rects: list[Rect]) -> Rect:
    left = min(r.left for r in rects)
    top = min(r.top for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    return Rect(left=left, top=top, width=right - left, height=bottom - top)


def _wedge(radius: int, start: float, end: float) -> str:
    """SVG path of a pie wedge (shares of a full turn, clockwise from 12 o'clock)."""
    if end - start >= 1:
        return f"M {radius} 0 A {radius} {radius} 0 1 1 {radius - 0.01} 0 Z"

    def point(share: float) -> tuple[float, float]:
        angle = 2 * math.pi * share - math.pi / 2
        return radius + radius * math.cos(angle), radius + radius * math.sin(angle)

    x0, y0 = point(start)
    x1, y1 = point(end)
    large = 1 if end - start > 0.5 else 0
    arc = f"A {radius} {radius} 0 {large} 1 {x1:.2f} {y1:.2f}"
    return f"M {radius} {radius} L {x0:.2f} {y0:.2f} {arc} Z"


def _rational(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)


def _hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
