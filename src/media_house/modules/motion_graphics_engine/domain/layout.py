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
from media_house.modules.motion_graphics_engine.domain.design import CARD_COLOURS, Design, design
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
from media_house.modules.motion_graphics_engine.domain.presets import bare_figure
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
    K.COMPARISON_MATRIX: ("lower_third", "card_safe", "full_screen", Anchor.CENTER),
    K.STRIKE_VALUE: ("callout", "callout_right", "callout", Anchor.CENTER),
    K.URL_CARD: ("lower_third", "lower_third_band", "lower_third", Anchor.BOTTOM_LEFT),
    K.DEADLINE_CHIP: ("callout", "callout_right", "callout", Anchor.TOP),
    K.CAPTION: ("caption.word_group", "caption_band", "caption", Anchor.CENTER),
    K.TRANSITION_OVERLAY: ("callout", "", "", Anchor.CENTER),
}
#: Zone a graphic moves to when its zone is taken by a simultaneous graphic.
ALTERNATE_ZONE: dict[str, str] = {"callout_left": "callout_right", "callout_right": "callout_left"}
#: Emoji presentation selectors and the joiner: part of an emoji, no width of their own.
_JOINERS = frozenset("︎️‍")
#: Chart series colours (semantic meanings of the style), used in order.
_SERIES = ("chart_a", "chart_b", "chart_c", "chart_d")
#: Refusals a smaller emphasis can cure (the graphic is too big for its zone).
_SHRINKABLE = frozenset({"zone.outside", "text.overflow"})
#: Compact charts (a number, a bar) sit beside the subject, not over the whole frame.
_COMPACT_CHARTS = frozenset({ChartKind.COUNTER, ChartKind.PROGRESS})
#: Card text parts: (type role, ``Design`` size attribute).
_PARTS: dict[str, tuple[str, str]] = {
    "eyebrow": ("label", "eyebrow_px"),
    "small": ("body", "eyebrow_px"),
    "title": ("headline", "title_px"),
    "body": ("body", "body_px"),
    "value": ("number", "value_px"),
    "hero": ("number", "hero_px"),
}


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
    """The composition, or ``None`` with the reason. A preset that does not fit its zone at the
    ordered emphasis is laid out again one level smaller (hero -> standard -> subtle) before it
    is refused: a planner's emphasis can never push a graphic out of its zone."""
    emphasis = 1 if request.content.emphasis is None else request.content.emphasis
    notes: list[QcIssue] = []
    while True:
        builder = _Builder(request, inputs, zone_override)
        try:
            builder.setup()
            return builder.build(), (*notes, *builder.issues)
        except _Refused:
            codes = {i.code for i in builder.issues if i.severity is Severity.ERROR}
            if emphasis <= 0 or not codes <= _SHRINKABLE:
                return None, (*notes, *builder.issues)
        emphasis -= 1
        request = replace(request, content=replace(request.content, emphasis=emphasis))
        notes.append(
            issue(
                "preset.emphasis_lowered",
                Severity.INFO,
                f"emphasis {emphasis + 1} does not fit its zone: drawn at {emphasis}",
                request.request_id,
                category=QcCategory.CREATIVE,
            )
        )


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
        self.d: Design = design(inputs.style)
        content = request.content
        self.variant = content.variant
        self.emphasis = 1 if content.emphasis is None else min(max(content.emphasis, 0), 2)

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
        if request.kind is K.CHART and request.content.chart in _COMPACT_CHARTS:
            zone_name, element_kind = "callout_right", "callout"
        if request.kind is K.TIMELINE and request.content.variant == "two_lane":
            zone_name, element_kind = "card_safe", "full_screen"
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
            balance=self.request.kind is not K.CAPTION,
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

    # --- the card system (design tokens; every informational overlay is one card) -----------

    def colourway(self, default: str = "dark") -> tuple[str, str, str]:
        """(panel, text, eyebrow) colour names of the card variant."""
        return CARD_COLOURS.get(self.variant, CARD_COLOURS[default])

    def part(
        self,
        eid: str,
        text: str,
        part: str,
        width: int,
        colour: str,
        *,
        lines: int = 2,
        align: str = "left",
        size_px: int | None = None,
    ) -> _Text:
        """Card text on a panel (no outline): its type role and size come from the token."""
        role, size_name = _PARTS[part]
        kind = self.style.type(role)
        size = size_px or getattr(self.d, size_name)
        if part != "eyebrow":
            size = self.d.scaled(size, self.emphasis)
        return self.text(
            eid,
            text,
            role=role,
            size_px=size,
            case=kind.case,
            fill=self.style.hex(colour),
            stroke=None,
            stroke_px=0,
            max_width=width,
            max_lines=lines,
            max_chars=80,
            align=align,
        )

    def inner_width(self, zone: ZoneFacts | None = None, accent: bool = True) -> int:
        """Room for card text inside ``zone`` (padding and the accent spine taken off)."""
        z = zone or self.zone
        return z.width - 2 * self.d.lg - (self.d.bar if accent else 0)

    def card_frame(
        self, inner_w: int, inner_h: int, colour: str, *, accent: bool = True, pad: int = 0
    ) -> tuple[Rect, int, int]:
        """Panel (+ accent spine on the reading edge, soft shadow) around ``inner_w x inner_h``;
        returns the card and where its content starts."""
        d = self.d
        px, py = (pad or d.lg), (pad or d.md)
        bar = d.bar if accent else 0
        rect = self.place(inner_w + 2 * px + bar, inner_h + 2 * py)
        opaque = colour in ("panel", "black")
        if bar:
            spine = "highlight" if colour == "accent" else "accent"
            self.panel(
                f"{self.id}.accent",
                Rect(left=rect.left, top=rect.top, width=bar + 2 * d.radius, height=rect.height),
                spine,
                0,
                radius_px=d.radius,
                reveal_from="top",
            )
        body = Rect(left=rect.left + bar, top=rect.top, width=rect.width - bar, height=rect.height)
        self.panel(
            f"{self.id}.panel",
            body,
            colour,
            1,
            opacity=self.g.panel_opacity if opaque else 1.0,
            radius_px=d.radius,
            shadow_blur_px=d.shadow_blur,
            shadow_opacity=d.shadow_opacity,
            reveal_from="left",
        )
        return rect, body.left + px, rect.top + py

    def stack(self, rows: list[tuple[str, _Text]], colour: str, *, accent: bool = True) -> Rect:
        """A card of stacked text rows (eyebrow, title, body...), on the baseline grid."""
        gap = self.d.xs
        inner_w = max(t.width for _, t in rows)
        inner_h = sum(t.height for _, t in rows) + gap * (len(rows) - 1)
        rect, left, top = self.card_frame(inner_w, inner_h, colour, accent=accent)
        for name, t in rows:
            self.add_text(f"{self.id}.{name}", t, left, top, 3)
            top += t.height + gap
        return rect

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

    def _statement(self, *, default: str = "dark") -> None:
        """Callout / text panel / highlight panel: an optional eyebrow and the statement on one
        card. Short statements are set a size larger (a punchline), long ones at body size."""
        c = self.request.content
        panel, ink, brow = self.colourway(default)
        width = self.inner_width()
        text = c.headline or "\n".join(c.body)
        if not text.strip():
            self.fail("text.empty", "no headline or body text", fix="supply the text")
        rows: list[tuple[str, _Text]] = []
        if c.eyebrow:
            rows.append(
                (
                    "eyebrow",
                    self.part(f"{self.id}.eyebrow", c.eyebrow, "eyebrow", width, brow, lines=1),
                )
            )
        if bare_figure(text) and not c.eyebrow:
            self.note(
                "text.bare_figure",
                f"{text!r} has no label: a figure alone says nothing (order it with a label)",
                Severity.WARNING,
            )
        part = "title" if len(text) <= 24 else "body"
        rows.append(("head", self.part(f"{self.id}.head", text, part, width, ink, lines=3)))
        if c.headline:
            for i, line in enumerate(c.body):
                rows.append((f"body{i}", self.part(f"{self.id}.body{i}", line, "body", width, ink)))
        self.stack(rows, panel)

    def _lower_third(self) -> None:
        c = self.request.content
        if not c.headline:
            self.fail("text.empty", "a lower third needs a name", fix="supply the headline")
        panel, ink, brow = self.colourway()
        width = self.inner_width()
        rows = [("head", self.part(f"{self.id}.head", c.headline, "title", width, ink, lines=1))]
        for line in c.body[:1]:
            rows.append(
                ("body", self.part(f"{self.id}.body", line, "eyebrow", width, brow, lines=1))
            )
        self.stack(rows, panel)

    def _callout(self) -> None:
        self._statement()

    def _text_panel(self) -> None:
        self._statement()

    def _highlight_panel(self) -> None:
        self._statement(default="highlight")

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
        """The mandatory 'Werbung' label: on a black chip, at the legibility floor for a phone."""
        text = self.request.content.headline
        if not text:
            self.fail("text.empty", "sponsor label without text")
        d = self.d
        t = self.text(
            f"{self.id}.head",
            text,
            role=self.look.role,
            size_px=max(self.look.size_px, d.video_floor_px),
            case=self.look.case,
            fill=self.look.fill,
            stroke=None,
            stroke_px=0,
            max_width=self.zone.width - 2 * d.sm,
            max_lines=1,
            max_chars=self.look.max_chars,
        )
        rect = self.place(t.width + 2 * d.sm, t.height + 2 * d.xs)
        self.panel(f"{self.id}.panel", rect, "black", 1, opacity=0.8, radius_px=d.radius_sm)
        self.add_text(f"{self.id}.head", t, rect.left + d.sm, rect.top + d.xs, 2)

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
        """The channel's yellow tag (slight tilt, hard shadow) with what it costs above it on a
        black chip: the price never floats without its product."""
        c = self.request.content
        if c.price_cents is None or not c.price_text:
            self.fail(
                "price.missing",
                "a price tag needs an exact price (from commerce facts) formatted by the style",
                fix="supply price_cents",
            )
        d = self.d
        room = self.zone.width - 2 * d.lg
        rows: list[tuple[str, _Text]] = []
        if c.eyebrow:
            rows.append(
                (
                    "eyebrow",
                    self.part(f"{self.id}.eyebrow", c.eyebrow, "eyebrow", room, "black", lines=1),
                )
            )
        rows.append(
            ("price", self.part(f"{self.id}.price", c.price_text, "hero", room, "black", lines=1))
        )
        tag_w = max(t.width for _, t in rows) + 2 * d.lg
        tag_h = sum(t.height for _, t in rows) + 2 * d.sm
        chip: _Text | None = None
        if c.headline:
            chip = self.part(
                f"{self.id}.label", c.headline, "eyebrow", self.zone.width - 2 * d.sm, "white"
            )
        chip_w = chip.width + 2 * d.sm if chip else 0
        chip_h = chip.height + 2 * d.xs if chip else 0
        total_w = max(tag_w, chip_w)
        rect = self.place(total_w, tag_h + (chip_h + d.xs if chip else 0))
        top = rect.top
        if chip:
            box = Rect(left=rect.left, top=top, width=chip_w, height=chip_h)
            self.panel(
                f"{self.id}.chip", box, "black", 0, radius_px=d.radius_sm, reveal_from="left"
            )
            self.add_text(f"{self.id}.label", chip, box.left + d.sm, box.top + d.xs, 3)
            top += chip_h + d.xs
        tag = Rect(left=rect.left, top=top, width=tag_w, height=tag_h)
        rotation = -self.g.tilt_min / 2
        self.panel(
            f"{self.id}.panel",
            tag,
            self.look.panel,
            1,
            name="price_fill",
            rotation=rotation,
            radius_px=d.radius,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )
        y = tag.top + d.sm
        for name, t in rows:
            left = tag.left + (tag.width - t.width) // 2
            self.add_text(f"{self.id}.{name}", t, left, y, 3, rotation=rotation)
            y += t.height

    def _spec_card(self) -> None:
        """Data card: product name, then one tile per fact (big value over its label), the
        price tile marked; the cut-out (when prepared) stands left of the card."""
        c = self.request.content
        rows = [(s.label, s.value) for s in c.specs]
        if c.price_text:
            rows.append(("Kosten", c.price_text))
        if not rows:
            self.fail("specs.missing", "a data card needs facts (Schuss, Höhe, Brenndauer...)")
        if self.variant == "rows":
            self._spec_rows(rows)
            return
        d = self.d
        image = self.optional_image(0)
        rows = rows[:4]
        avail = self.inner_width(accent=False) - (self.zone.width // 4 if image else 0)
        tile_w = min((avail - d.lg * (len(rows) - 1)) // len(rows), d.value_px * 4)
        values = [
            self.part(
                f"{self.id}.v{i}",
                value,
                "value",
                tile_w,
                "highlight" if label == "Kosten" else "white",
                lines=1,
            )
            for i, (label, value) in enumerate(rows)
        ]
        labels = [
            self.part(f"{self.id}.l{i}", label, "eyebrow", tile_w, "silver_mid", lines=1)
            for i, (label, _) in enumerate(rows)
        ]
        cols = [max(v.width, lb.width) for v, lb in zip(values, labels, strict=True)]
        tiles_w = sum(cols) + d.xl * (len(cols) - 1)
        head = None
        if c.headline:
            head = self.part(
                f"{self.id}.head",
                c.headline,
                "title",
                max(tiles_w, avail * 2 // 3),
                "white",
                lines=1,
            )
        tile_h = max(v.height for v in values) + d.xs + max(lb.height for lb in labels)
        inner_w = max(tiles_w, head.width if head else 0)
        inner_h = (head.height + d.md if head else 0) + tile_h
        image_w = min(self.zone.width // 4, inner_h * 4 // 3) if image else 0
        card, left, top = self.card_frame(
            inner_w + (image_w + d.lg if image else 0), inner_h, "black", accent=False
        )
        if image:
            box = Rect(left=left, top=card.top + d.sm, width=image_w, height=card.height - 2 * d.sm)
            self._image(f"{self.id}.image", image, box, 4, rotation=self.tilt(), sticker=True)
            left += image_w + d.lg
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 3)
            top += head.height + d.sm
            self.panel(
                f"{self.id}.rule",
                Rect(left=left, top=top, width=inner_w, height=d.rule),
                "accent",
                2,
                radius_px=0,
                reveal_from="left",
            )
            top += d.rule + d.sm
        x = left
        for i, (v, lb) in enumerate(zip(values, labels, strict=True)):
            self.add_text(f"{self.id}.v{i}", v, x, top, 3)
            self.add_text(f"{self.id}.l{i}", lb, x, top + v.height + d.xs, 3)
            if i:
                self.panel(
                    f"{self.id}.sep{i}",
                    Rect(left=x - d.xl // 2, top=top, width=d.rule, height=tile_h),
                    "silver_dark",
                    2,
                    radius_px=0,
                    opacity=0.6,
                )
            x += cols[i] + d.xl

    def _spec_rows(self, rows: list[tuple[str, str]]) -> None:
        """The data card as label/value rows (many or long facts)."""
        c, d = self.request.content, self.d
        width = self.inner_width()
        labels = [
            self.part(f"{self.id}.l{i}", a, "eyebrow", width // 2, "highlight", lines=1)
            for i, (a, _) in enumerate(rows)
        ]
        col = max(t.width for t in labels) + d.lg
        values = [
            self.part(f"{self.id}.v{i}", b, "body", width - col, "white", lines=1)
            for i, (_, b) in enumerate(rows)
        ]
        head = (
            self.part(f"{self.id}.head", c.headline, "title", width, "white", lines=2)
            if c.headline
            else None
        )
        row_h = max(max(a.height, b.height) for a, b in zip(labels, values, strict=True))
        inner_w = max([col + v.width for v in values] + ([head.width] if head else []))
        inner_h = (head.height + d.sm if head else 0) + row_h * len(rows) + d.xs * (len(rows) - 1)
        _, left, top = self.card_frame(inner_w, inner_h, "black")
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 3)
            top += head.height + d.sm
        for i, (a, b) in enumerate(zip(labels, values, strict=True)):
            self.add_text(f"{self.id}.l{i}", a, left, top + (row_h - a.height) // 2, 3)
            self.add_text(f"{self.id}.v{i}", b, left + col, top + (row_h - b.height) // 2, 3)
            top += row_h + d.xs

    def _comparison(self) -> None:
        """Versus: two equal cards (name, cut-out when prepared) and a red VS badge between."""
        c, d = self.request.content, self.d
        if len(c.body) != 2:
            self.fail("comparison.sides", "a comparison needs exactly two sides (body)")
        zone = self.zone
        badge = round(self.style.type("display").size_px * 1.5)
        half = min((zone.width - badge - 2 * d.md) // 2, zone.width * 2 // 5)
        names = [
            self.part(
                f"{self.id}.label{i}",
                side,
                "title",
                half - 2 * d.lg,
                "white",
                lines=2,
                align="center",
            )
            for i, side in enumerate(c.body)
        ]
        images = [self.optional_image(i) for i in range(2)] if c.image_asset_ids else [None, None]
        name_h = max(t.height for t in names)
        image_h = half * 3 // 4 if any(images) else 0
        card_h = name_h + 2 * d.md + (image_h + d.md if image_h else 0)
        total = self.place(2 * half + badge + 2 * d.md, card_h)
        for i, (t, colour) in enumerate(zip(names, ("chart_a", "chart_b"), strict=True)):
            box = Rect(
                left=total.left + i * (half + badge + 2 * d.md),
                top=total.top,
                width=half,
                height=card_h,
            )
            self.panel(
                f"{self.id}.side{i}",
                box,
                colour,
                1,
                radius_px=d.radius,
                shadow_blur_px=d.shadow_blur,
                shadow_opacity=d.shadow_opacity,
                reveal_from="right" if i == 0 else "left",
            )
            self.add_text(
                f"{self.id}.label{i}",
                t,
                box.left + (half - t.width) // 2,
                box.bottom - d.md - t.height,
                3,
            )
            image = images[i]
            if image is not None:
                inner = Rect(
                    left=box.left + d.md, top=box.top + d.md, width=half - 2 * d.md, height=image_h
                )
                self._image(
                    f"{self.id}.image{i}", image, inner, 4, rotation=self.tilt(str(i)), sticker=True
                )
        centre = total.left + total.width // 2
        circle = Rect(
            left=centre - badge // 2,
            top=total.top + (card_h - badge) // 2,
            width=badge,
            height=badge,
        )
        self.panel(
            f"{self.id}.badge",
            circle,
            "black",
            5,
            radius_px=badge // 2,
            stroke=self.style.hex("white"),
            stroke_px=d.rule,
        )
        display = self.style.type("display")
        vs = self.text(
            f"{self.id}.vs",
            "VS",
            role="display",
            size_px=display.size_px * 2 // 3,
            case="upper",
            fill=self.style.hex("white"),
            stroke=None,
            stroke_px=0,
            max_width=badge,
            max_lines=1,
            max_chars=4,
        )
        self.add_text(
            f"{self.id}.vs", vs, centre - vs.width // 2, circle.top + (badge - vs.height) // 2, 6
        )

    def _list(self, numbered: bool) -> None:
        """Feature / step list: an eyebrow title and up to four items, each with its marker."""
        c, d = self.request.content, self.d
        if not c.body:
            self.fail("list.empty", "a list needs items (body)")
        width = self.inner_width()
        title = (
            self.part(f"{self.id}.head", c.headline, "eyebrow", width, "highlight", lines=1)
            if c.headline
            else None
        )
        mark = round(d.body_px * 0.9)
        items = [
            self.part(f"{self.id}.i{i}", item, "body", width - mark - d.sm, "white")
            for i, item in enumerate(c.body)
        ]
        rows_h = [max(t.height, mark) for t in items]
        inner_h = sum(rows_h) + d.sm * (len(items) - 1) + (title.height + d.sm if title else 0)
        inner_w = max([t.width + mark + d.sm for t in items] + ([title.width] if title else []))
        _, left, top = self.card_frame(inner_w, inner_h, "panel")
        if title:
            self.add_text(f"{self.id}.head", title, left, top, 3)
            top += title.height + d.sm
        first_line = round(d.scaled(d.body_px, self.emphasis) * self.style.type("body").leading)
        for i, (t, row) in enumerate(zip(items, rows_h, strict=True)):
            box = Rect(left=left, top=top + (first_line - mark) // 2, width=mark, height=mark)
            if numbered:
                self.panel(f"{self.id}.b{i}", box, "accent", 2, radius_px=mark // 2)
                num = self.text(
                    f"{self.id}.n{i}",
                    str(i + 1),
                    role="number",
                    size_px=max(self.style.min_text_px, mark * 2 // 3),
                    case="as_written",
                    fill=self.style.hex("white"),
                    stroke=None,
                    stroke_px=0,
                    max_width=mark,
                    max_lines=1,
                    max_chars=3,
                )
                self.add_text(
                    f"{self.id}.n{i}",
                    num,
                    box.left + (mark - num.width) // 2,
                    box.top + (mark - num.height) // 2,
                    3,
                )
            else:
                dot = mark // 3
                self.panel(
                    f"{self.id}.b{i}",
                    Rect(left=box.left + dot, top=box.top + dot, width=dot, height=dot),
                    "highlight",
                    2,
                    radius_px=0,
                    rotation=45.0,
                )
            self.add_text(f"{self.id}.i{i}", t, left + mark + d.sm, top, 3)
            top += row + d.sm

    def _feature_list(self) -> None:
        self._list(numbered=False)

    def _step_list(self) -> None:
        self._list(numbered=True)

    # --- image graphics ---------------------------------------------------------------------

    def _image(
        self,
        eid: str,
        image: tuple[str, int, int],
        box: Rect,
        z: int,
        *,
        rotation: float = 0.0,
        sticker: bool = False,
        glow: bool = True,
    ) -> Rect:
        """``sticker``: the identity's cut-out look (white outer glow) and a soft shadow that
        lifts it off the footage; without ``glow`` only the shadow."""
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
                glow=self.style.hex("glow") if sticker and glow else None,
                glow_px=self.g.sticker_glow_px if sticker and glow else 0,
                glow_opacity=self.g.sticker_glow_opacity if sticker and glow else 0.0,
                shadow_blur_px=self.d.shadow_blur if sticker else 0,
                shadow_opacity=self.d.shadow_opacity if sticker else 0.0,
            )
        )
        return rect

    def _product_sticker(self) -> None:
        image = self.image(0)
        share = (0.7, 0.85, 1.0)[self.emphasis]
        margin = self.g.sticker_glow_px + self.d.shadow_blur
        box = self.place(
            round(self.zone.width * share) - 2 * margin,
            round(self.zone.height * share) - 2 * margin,
        )
        self._image(
            f"{self.id}.image",
            image,
            box,
            1,
            rotation=self.tilt(),
            sticker=True,
            glow=self.variant != "shadow",
        )

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
        t = self.look_text(f"{self.id}.head", text, self.look, self.zone.width, max_lines=2)
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
        elif c.chart is ChartKind.PROGRESS:
            self._progress()
        elif c.chart is ChartKind.BAR and self.variant == "columns":
            self._columns()
        elif c.chart is ChartKind.BAR:
            self._bars()
        elif c.chart is ChartKind.LINE:
            self._line()
        else:
            self._pie()

    def _best(self) -> int:
        """The marked data point: the one named in ``highlight``, else the largest value."""
        c = self.request.content
        marks = {key(w) for w in c.highlight}
        named = [i for i, p in enumerate(c.data) if key(p.label) in marks]
        if named:
            return named[0]
        values = [Fraction(p.value.numerator, p.value.denominator) for p in c.data]
        return values.index(max(values))

    def _chart_width(self) -> int:
        """Charts are compact cards: never wider than ~60 % of the frame."""
        return min(self.inner_width(accent=False), self.style.width * 3 // 5)

    def _chart_title(self, width: int) -> _Text | None:
        head = self.request.content.headline
        return (
            self.part(f"{self.id}.head", head, "title", width, "white", lines=1) if head else None
        )

    def _bars(self) -> None:
        """Comparison bars: label | bar | value per row, one shared scale, the best bar marked."""
        data, d = self.request.content.data, self.d
        width = self._chart_width()
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        top_value = max(values) or Fraction(1)
        best = self._best()
        labels = [
            self.part(f"{self.id}.l{i}", p.label, "body", width // 3, "white", lines=1)
            for i, p in enumerate(data)
        ]
        numbers = [
            self.part(
                f"{self.id}.v{i}",
                self._number(i),
                "body",
                width // 4,
                "highlight" if i == best else "white",
                lines=1,
            )
            for i in range(len(data))
        ]
        label_w = max(t.width for t in labels)
        value_w = max(t.width for t in numbers)
        track_w = width - label_w - value_w - 2 * d.md
        row_h = max(max(t.height for t in labels), d.lg)
        head = self._chart_title(width)
        inner_h = (head.height + d.md if head else 0) + row_h * len(data) + d.sm * (len(data) - 1)
        _, left, top = self.card_frame(width, inner_h, "panel", accent=False)
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 3)
            top += head.height + d.md
        bar_h = d.lg
        for i, value in enumerate(values):
            mid = top + row_h // 2
            self.add_text(f"{self.id}.l{i}", labels[i], left, mid - labels[i].height // 2, 3)
            x = left + label_w + d.md
            self.panel(
                f"{self.id}.track{i}",
                Rect(left=x, top=mid - bar_h // 2, width=track_w, height=bar_h),
                "silver_dark",
                2,
                radius_px=bar_h // 2,
                opacity=0.25,
            )
            length = max(bar_h, round(track_w * value / top_value))
            self.panel(
                f"{self.id}.bar{i}",
                Rect(left=x, top=mid - bar_h // 2, width=length, height=bar_h),
                "accent" if i == best else "silver_mid",
                3,
                radius_px=bar_h // 2,
                reveal_from="left",
            )
            n = numbers[i]
            self.add_text(f"{self.id}.v{i}", n, left + width - n.width, mid - n.height // 2, 3)
            top += row_h + d.sm

    def _columns(self) -> None:
        """Vertical bars (few items, short labels): value above, label below, best marked."""
        data, d = self.request.content.data, self.d
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        top_value = max(values) or Fraction(1)
        best = self._best()
        width = self._chart_width()
        slot = (width - d.lg * (len(data) - 1)) // len(data)
        labels = [
            self.part(f"{self.id}.l{i}", p.label, "eyebrow", slot, "white", lines=1)
            for i, p in enumerate(data)
        ]
        numbers = [
            self.part(
                f"{self.id}.v{i}",
                self._number(i),
                "body",
                slot,
                "highlight" if i == best else "white",
                lines=1,
            )
            for i in range(len(data))
        ]
        head = self._chart_title(width)
        plot_h = d.xl * 5
        value_h = max(t.height for t in numbers)
        label_h = max(t.height for t in labels)
        inner_h = (head.height + d.md if head else 0) + value_h + d.xs + plot_h + d.sm + label_h
        _, left, top = self.card_frame(width, inner_h, "panel", accent=False)
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 3)
            top += head.height + d.md
        base = top + value_h + d.xs + plot_h
        bar_w = min(slot, d.xl * 3)
        for i, value in enumerate(values):
            x = left + i * (slot + d.lg)
            height = max(d.xs, round(plot_h * value / top_value))
            bar = Rect(left=x + (slot - bar_w) // 2, top=base - height, width=bar_w, height=height)
            self.panel(
                f"{self.id}.bar{i}",
                bar,
                "accent" if i == best else "silver_mid",
                2,
                reveal_from="bottom",
                radius_px=d.radius_sm,
            )
            n = numbers[i]
            self.add_text(
                f"{self.id}.v{i}", n, x + (slot - n.width) // 2, bar.top - d.xs - n.height, 3
            )
            lb = labels[i]
            self.add_text(f"{self.id}.l{i}", lb, x + (slot - lb.width) // 2, base + d.sm, 3)

    def _line(self) -> None:
        """A trend: the line with a dot per point; only the first and the last value are
        printed (the last one marked), so labels never collide with the line or each other."""
        data, d = self.request.content.data, self.d
        if len(data) < 2:
            self.fail("chart.no_data", "a line chart needs at least two points")
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        low, high = min(values), max(values)
        span = (high - low) or Fraction(1)
        width = self._chart_width()
        head = self._chart_title(width)
        labels = [
            self.part(
                f"{self.id}.l{i}", p.label, "eyebrow", width // len(data), "silver_mid", lines=1
            )
            for i, p in enumerate(data)
        ]
        ends = {0: "white", len(data) - 1: "highlight"}
        numbers = {
            i: self.part(f"{self.id}.v{i}", self._number(i), "body", width // 3, colour, lines=1)
            for i, colour in ends.items()
        }
        value_h = max(t.height for t in numbers.values())
        plot_h = d.xl * 4
        label_h = max(t.height for t in labels)
        inner_h = (head.height + d.md if head else 0) + value_h + d.sm + plot_h + d.sm + label_h
        _, left, top = self.card_frame(width, inner_h, "panel", accent=False)
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 3)
            top += head.height + d.md
        dot = d.md
        area = Rect(left=left + dot, top=top + value_h + d.sm, width=width - 2 * dot, height=plot_h)
        step = area.width / (len(data) - 1)
        points = [
            (round(i * step), round(area.height - area.height * (v - low) / span * Fraction(4, 5)))
            for i, v in enumerate(values)
        ]
        path = "M " + " L ".join(f"{x} {y}" for x, y in points)
        self.add(
            Element(
                id=f"{self.id}.line",
                type=ElementType.SHAPE,
                rect=area,
                z=2,
                stroke=self.style.hex("accent"),
                stroke_px=d.bar,
                path=path,
                colour_name="accent",
                reveal_from="left",
            )
        )
        for i, (x, y) in enumerate(points):
            self.panel(
                f"{self.id}.d{i}",
                Rect(
                    left=area.left + x - dot // 2,
                    top=area.top + y - dot // 2,
                    width=dot,
                    height=dot,
                ),
                "highlight" if i == len(points) - 1 else "white",
                3,
                radius_px=dot // 2,
            )
            lb = labels[i]
            lx = min(max(area.left + x - lb.width // 2, left), left + width - lb.width)
            self.add_text(f"{self.id}.l{i}", lb, lx, area.bottom + d.sm, 3)
        for i, n in numbers.items():
            x, y = points[i]
            nx = min(max(area.left + x - n.width // 2, left), left + width - n.width)
            self.add_text(f"{self.id}.v{i}", n, nx, max(top, area.top + y - dot - n.height), 4)

    def _pie(self) -> None:
        data, d = self.request.content.data, self.d
        values = [Fraction(p.value.numerator, p.value.denominator) for p in data]
        total = sum(values)
        if total <= 0:
            self.fail("chart.no_data", "a pie chart needs a positive total")
        width = self._chart_width()
        head = self._chart_title(width)
        radius = d.xl * 3
        legend_w = width - 2 * radius - d.xl
        swatch = d.md
        legend = []
        for i, p in enumerate(data):
            share, _ = german_number(_rational(values[i] * 100 / total), 1)
            legend.append(
                self.part(
                    f"{self.id}.l{i}",
                    f"{p.label}  {share} %",
                    "body",
                    legend_w - swatch - d.sm,
                    "white",
                    lines=1,
                )
            )
        legend_h = sum(t.height for t in legend) + d.sm * (len(legend) - 1)
        inner_h = (head.height + d.md if head else 0) + max(2 * radius, legend_h)
        inner_w = 2 * radius + d.xl + swatch + d.sm + max(t.width for t in legend)
        _, left, top = self.card_frame(inner_w, inner_h, "panel", accent=False)
        if head:
            self.add_text(f"{self.id}.head", head, left, top, 3)
            top += head.height + d.md
        box = Rect(left=left, top=top, width=2 * radius, height=2 * radius)
        start = Fraction(0)
        for i, value in enumerate(values):
            end = start + value / total
            colour = _SERIES[i % len(_SERIES)]
            self.add(
                Element(
                    id=f"{self.id}.wedge{i}",
                    type=ElementType.SHAPE,
                    rect=box,
                    z=2,
                    fill=self.style.hex(colour),
                    colour_name=colour,
                    path=_wedge(radius, float(start), float(end)),
                    stroke=self.style.hex("panel"),
                    stroke_px=d.rule,
                )
            )
            start = end
        y = top + (2 * radius - legend_h) // 2
        x = left + 2 * radius + d.xl
        for i, t in enumerate(legend):
            colour = _SERIES[i % len(_SERIES)]
            self.panel(
                f"{self.id}.s{i}",
                Rect(left=x, top=y + (t.height - swatch) // 2, width=swatch, height=swatch),
                colour,
                3,
                radius_px=d.radius_sm,
            )
            self.add_text(f"{self.id}.l{i}", t, x + swatch + d.sm, y, 3)
            y += t.height + d.sm

    def _progress(self) -> None:
        data, d = self.request.content.data, self.d
        first = Fraction(data[0].value.numerator, data[0].value.denominator)
        if len(data) >= 2:
            total = Fraction(data[1].value.numerator, data[1].value.denominator)
        elif data[0].unit == "%":
            total = Fraction(100)
        else:
            self.fail("chart.no_data", "progress needs a value and a total, or a percentage")
        if total <= 0 or first > total:
            self.fail("chart.range", "progress value must lie between 0 and the total")
        width = self.inner_width()
        rows: list[tuple[str, _Text]] = []
        label = data[0].label or self.request.content.eyebrow
        if label:
            rows.append(
                (
                    "label",
                    self.part(f"{self.id}.label", label, "eyebrow", width, "highlight", lines=1),
                )
            )
        rows.append(
            ("v0", self.part(f"{self.id}.v0", self._number(0), "value", width, "white", lines=1))
        )
        bar_h = d.md
        text_h = sum(t.height for _, t in rows) + d.xs * (len(rows) - 1)
        _, left, top = self.card_frame(width, text_h + d.sm + bar_h, "panel")
        for name, t in rows:
            self.add_text(f"{self.id}.{name}", t, left, top, 3)
            top += t.height + d.xs
        track = Rect(left=left, top=top - d.xs + d.sm, width=width, height=bar_h)
        self.panel(f"{self.id}.track", track, "black", 2, radius_px=bar_h // 2)
        self.panel(
            f"{self.id}.fill",
            Rect(
                left=track.left,
                top=track.top,
                width=max(bar_h, round(track.width * first / total)),
                height=bar_h,
            ),
            "positive",
            3,
            radius_px=bar_h // 2,
            reveal_from="left",
        )

    def _counter(self) -> None:
        """A total counting up (cart total): its label above, the number big."""
        c = self.request.content
        width = self.inner_width()
        rows: list[tuple[str, _Text]] = []
        label = c.data[0].label or c.eyebrow
        if label:
            rows.append(
                (
                    "label",
                    self.part(f"{self.id}.label", label, "eyebrow", width, "highlight", lines=1),
                )
            )
        rows.append(
            (
                "count",
                self.part(f"{self.id}.count", self._number(0), "hero", width, "white", lines=1),
            )
        )
        self.stack(rows, "panel")

    def _timeline(self) -> None:
        c = self.request.content
        rows = c.rows or tuple((p.label,) for p in c.data) or tuple((b,) for b in c.body)
        if self.variant == "two_lane":
            self._two_lanes(rows)
            return
        events = [_split_event(r) for r in rows]
        if len(events) < 2:
            self.fail("timeline.items", "a timeline needs at least two dated moments")
        self._steps(events)

    def _steps(self, events: list[tuple[str, str]]) -> None:
        """Dated steps on one rail (fits the lower-third band): a dot per step, then the date
        (marked) and what happens on one line."""
        d = self.d
        width = self.inner_width()
        col = (width - d.lg * (len(events) - 1)) // len(events)
        dates = [
            self.part(f"{self.id}.d{i}", date, "body", col // 2, "highlight", lines=1)
            for i, (date, _) in enumerate(events)
        ]
        texts = [
            self.part(
                f"{self.id}.l{i}", text, "body", col - dates[i].width - d.sm, "white", lines=1
            )
            if text
            else None
            for i, (_, text) in enumerate(events)
        ]
        dot = d.md
        line_h = max(t.height for t in [*dates, *(t for t in texts if t)])
        used = max(dates[i].width + (d.sm + t.width if t else 0) for i, t in enumerate(texts))
        col = min(col, used + d.xl)
        width = col * len(events) + d.lg * (len(events) - 1)
        _, left, top = self.card_frame(width, dot + d.xs + line_h, "panel", pad=d.md)
        self.panel(
            f"{self.id}.rail",
            Rect(
                left=left + dot // 2,
                top=top + (dot - d.rule) // 2,
                width=width - dot,
                height=d.rule,
            ),
            "silver_mid",
            2,
            radius_px=0,
            reveal_from="left",
        )
        for i, (date, text) in enumerate(zip(dates, texts, strict=True)):
            x = left + i * (col + d.lg)
            self.panel(
                f"{self.id}.dot{i}",
                Rect(left=x, top=top, width=dot, height=dot),
                "accent",
                3,
                radius_px=dot // 2,
                stroke=self.style.hex("white"),
                stroke_px=d.rule,
            )
            y = top + dot + d.xs
            self.add_text(f"{self.id}.d{i}", date, x, y + line_h - date.height, 3)
            if text:
                self.add_text(
                    f"{self.id}.l{i}", text, x + date.width + d.sm, y + line_h - text.height, 3
                )

    def _two_lanes(self, rows: tuple[tuple[str, ...], ...]) -> None:
        """Before vs now: two rails on one calendar axis, the same day aligned in both lanes."""
        d = self.d
        lanes: list[str] = []
        for r in rows:
            if len(r) != 3:
                self.fail("timeline.lanes", "a two-lane timeline needs (lane, date, text) rows")
            if r[0] not in lanes:
                lanes.append(r[0])
        if len(lanes) != 2:
            self.fail("timeline.lanes", f"a two-lane timeline needs 2 lanes, got {len(lanes)}")
        days = sorted({_day(r[1]) for r in rows})
        width = self.inner_width(accent=False)
        heads = [
            self.part(
                f"{self.id}.lane{i}",
                lane,
                "title",
                width // 5,
                "highlight" if i else "silver_mid",
                lines=1,
            )
            for i, lane in enumerate(lanes)
        ]
        head_w = max(t.width for t in heads) + d.lg
        col = (width - head_w) // max(1, len(days))
        dot = d.md
        cells: dict[tuple[int, int], tuple[int, _Text, _Text | None]] = {}
        for k, (lane, date, text) in enumerate(rows):
            li, di = lanes.index(lane), days.index(_day(date))
            if (li, di) in cells:
                self.fail("timeline.duplicate", f"{lane} has two events on {date}")
            colour = "highlight" if li else "white"
            cells[(li, di)] = (
                k,
                self.part(f"{self.id}.d{k}", date, "body", col - d.sm, colour, lines=1),
                self.part(f"{self.id}.l{k}", text, "small", col - d.sm, "white", lines=2)
                if text
                else None,
            )
        lane_h = (
            dot + d.xs + max(a.height + (b.height + d.xs if b else 0) for _, a, b in cells.values())
        )
        _, left, top = self.card_frame(width, 2 * lane_h + d.lg, "panel", accent=False)
        for li, head in enumerate(heads):
            y = top + li * (lane_h + d.lg)
            self.add_text(
                f"{self.id}.lane{li}", head, left, y + (dot - head.height) // 2 + dot // 2, 3
            )
            self.panel(
                f"{self.id}.rail{li}",
                Rect(
                    left=left + head_w,
                    top=y + (dot - d.rule) // 2,
                    width=width - head_w,
                    height=d.rule,
                ),
                "accent" if li else "silver_mid",
                2,
                radius_px=0,
                reveal_from="left",
            )
            for di in range(len(days)):
                cell = cells.get((li, di))
                if cell is None:
                    continue
                x = left + head_w + di * col
                self.panel(
                    f"{self.id}.dot{li}_{di}",
                    Rect(left=x, top=y, width=dot, height=dot),
                    "accent" if li else "silver_mid",
                    3,
                    radius_px=dot // 2,
                    stroke=self.style.hex("white"),
                    stroke_px=d.rule,
                )
                k, when, what = cell
                self.add_text(f"{self.id}.d{k}", when, x, y + dot + d.xs, 3)
                if what:
                    self.add_text(
                        f"{self.id}.l{k}", what, x, y + dot + d.xs + when.height + d.xs, 3
                    )

    # --- presets of the product-ranking format ----------------------------------------------

    def _comparison_matrix(self) -> None:
        """Up to five products x up to three facts; header row, hairlines, the best row on red."""
        c, d = self.request.content, self.d
        if len(c.rows) < 2:
            self.fail("matrix.rows", "a matrix needs a header row and at least one product row")
        head, *body = c.rows
        cols = len(head)
        if len(body) > 5 or not 2 <= cols <= 4 or any(len(r) != cols for r in body):
            self.fail("matrix.shape", f"a matrix takes 1-5 rows of {cols} cells (2-4 columns)")
        marks = {key(w) for w in c.highlight}
        best = next((i for i, r in enumerate(body) if key(r[0]) in marks), None)
        width = min(self.inner_width(accent=False), self.style.width * 3 // 5)
        name_w = width * 2 // 5
        cell_w = (width - name_w) // (cols - 1)
        heads = [
            self.part(
                f"{self.id}.h{j}",
                h,
                "eyebrow",
                cell_w - d.sm,
                "silver_mid",
                lines=1,
                align="center",
            )
            for j, h in enumerate(head[1:], start=1)
        ]
        cells = [
            [
                self.part(
                    f"{self.id}.c{i}_{j}",
                    text,
                    "body",
                    (name_w if j == 0 else cell_w) - d.md,
                    "white" if j == 0 or i == best else "silver_light",
                    lines=1,
                    align="left" if j == 0 else "center",
                )
                for j, text in enumerate(row)
            ]
            for i, row in enumerate(body)
        ]
        title = self._chart_title(width)
        head_h = max(t.height for t in heads)
        row_h = max(t.height for row in cells for t in row) + d.sm
        inner_h = (title.height + d.md if title else 0) + head_h + d.sm + row_h * len(body)
        _, left, top = self.card_frame(width, inner_h, "panel", accent=False)
        if title:
            self.add_text(f"{self.id}.head", title, left, top, 3)
            top += title.height + d.md
        for j, t in enumerate(heads, start=1):
            x = left + name_w + (j - 1) * cell_w
            self.add_text(f"{self.id}.h{j}", t, x + (cell_w - t.width) // 2, top, 3)
        top += head_h + d.sm
        for i, row in enumerate(cells):
            y = top + i * row_h
            if i == best:
                self.panel(
                    f"{self.id}.best",
                    Rect(left=left - d.sm, top=y, width=width + 2 * d.sm, height=row_h),
                    "accent",
                    2,
                    radius_px=d.radius_sm,
                    reveal_from="left",
                )
            elif i:
                self.panel(
                    f"{self.id}.rule{i}",
                    Rect(left=left, top=y, width=width, height=max(1, d.rule // 2)),
                    "silver_dark",
                    2,
                    radius_px=0,
                    opacity=0.6,
                )
            for j, t in enumerate(row):
                x = left + (d.sm if j == 0 else name_w + (j - 1) * cell_w + (cell_w - t.width) // 2)
                self.add_text(f"{self.id}.c{i}_{j}", t, x, y + (row_h - t.height) // 2, 3)

    def _strike_value(self) -> None:
        """A value that no longer holds, struck through in red, and what replaces it."""
        c, d = self.request.content, self.d
        old = c.body[0] if c.body else ""
        if not old:
            self.fail("strike.old", "a struck value needs the old value (body)")
        width = self.inner_width()
        rows: list[tuple[str, _Text]] = []
        if c.eyebrow:
            rows.append(
                (
                    "eyebrow",
                    self.part(
                        f"{self.id}.eyebrow", c.eyebrow, "eyebrow", width, "highlight", lines=1
                    ),
                )
            )
        was = self.part(f"{self.id}.old", old, "value", width, "silver_mid", lines=1)
        now = None
        inline = False
        if c.headline:  # beside the old value when it fits at full size, else below it
            now = self.part(f"{self.id}.new", c.headline, "value", width, "white", lines=1)
            inline = (
                was.width + d.lg + now.width <= width and now.block.size_px == was.block.size_px
            )
            if not inline:
                now = self.part(f"{self.id}.new", c.headline, "title", width, "white", lines=2)
        line_w = was.width + (d.lg + now.width if now and inline else 0)
        below = now.height + d.xs if now and not inline else 0
        brow_h = sum(t.height + d.xs for _, t in rows)
        inner_w = max([line_w, now.width if now else 0] + [t.width for _, t in rows])
        _, left, top = self.card_frame(inner_w, brow_h + was.height + below, "panel")
        for name, t in rows:
            self.add_text(f"{self.id}.{name}", t, left, top, 3)
            top += t.height + d.xs
        self.add_text(f"{self.id}.old", was, left, top, 3)
        self.panel(
            f"{self.id}.strike",
            Rect(
                left=left - d.xs,
                top=top + was.height // 2 - d.bar // 2,
                width=was.width + 2 * d.xs,
                height=d.bar,
            ),
            "accent",
            4,
            radius_px=d.bar // 2,
            rotation=-6.0,
            reveal_from="left",
        )
        if now and inline:
            self.add_text(f"{self.id}.new", now, left + was.width + d.lg, top, 3)
        elif now:
            self.add_text(f"{self.id}.new", now, left, top + was.height + d.xs, 3)

    def _url_card(self) -> None:
        c = self.request.content
        if not c.headline:
            self.fail("text.empty", "a URL card needs the address", fix="supply the headline")
        panel, ink, brow = self.colourway()
        width = self.inner_width()
        rows = [
            (
                "eyebrow",
                self.part(
                    f"{self.id}.eyebrow", c.eyebrow or "LINK", "eyebrow", width, brow, lines=1
                ),
            ),
            ("head", self.part(f"{self.id}.head", c.headline, "title", width, ink, lines=1)),
        ]
        self.stack(rows, panel)

    def _deadline_chip(self) -> None:
        """A pill: 'NUR BIS' + the date, in one line."""
        c, d = self.request.content, self.d
        if not c.headline:
            self.fail("text.empty", "a deadline chip needs its date", fix="supply the headline")
        panel, ink, _ = self.colourway("accent")
        width = self.zone.width - 2 * d.lg
        label = self.part(
            f"{self.id}.eyebrow", c.eyebrow or "NUR BIS", "eyebrow", width // 2, ink, lines=1
        )
        date = self.part(
            f"{self.id}.head", c.headline, "title", width - label.width - d.sm, ink, lines=1
        )
        height = date.height + 2 * d.sm
        rect = self.place(label.width + d.sm + date.width + 2 * d.lg, height)
        self.panel(
            f"{self.id}.panel",
            rect,
            panel,
            1,
            radius_px=height // 2,
            shadow_px=self.g.hard_shadow_px,
            shadow_opacity=self.g.hard_shadow_opacity,
        )
        base = rect.top + d.sm
        self.add_text(
            f"{self.id}.eyebrow",
            label,
            rect.left + d.lg,
            base + (date.height - label.height) // 2,
            3,
        )
        self.add_text(f"{self.id}.head", date, rect.left + d.lg + label.width + d.sm, base, 3)

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
        pad = self.d.sm
        t = self.part(f"{self.id}.head", c.headline, "body", self.zone.width - 2 * pad, "white")
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


def _split_event(row: tuple[str, ...]) -> tuple[str, str]:
    """(date, text) of a timeline row; a single cell "09.11. Prospekt" is split after the date."""
    if len(row) >= 2:
        return row[-2], row[-1]
    date, _, text = row[0].partition(" ")
    return date, text


def _day(date: str) -> tuple[int, int]:
    """(month, day) of "dd.mm." / "dd.mm.yyyy" (lanes compare the same day of their years)."""
    parts = [p for p in date.strip().split(".") if p]
    try:
        return int(parts[1]), int(parts[0])
    except (IndexError, ValueError):
        return 99, 99


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
