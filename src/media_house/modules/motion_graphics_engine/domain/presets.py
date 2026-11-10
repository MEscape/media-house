"""The preset catalogue and the orchestration surface: the ONLY choices a planner makes.

A planner (rules or a language model) never touches pixels, sizes, colours or easings. Per kind
it fills a few named, typed SLOTS with short texts and facts, picks one of a handful of
VARIANTS and an EMPHASIS level (0 subtle, 1 standard, 2 hero), and names the moment the graphic
must be fully visible (``at``: when the fact is spoken). ``compile_order`` validates the order
(unknown slot, too long, wrong format, unknown variant: rejected with every reason listed) and
turns it into a ``GraphicRequest``; the layout engine and the animation compiler do the design.

Each preset also fixes its choreography (an entry/exit pair) and its zones, so the look of a kind
is the same in every video.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicOrder
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationRequest,
    DataPoint,
    GraphicContent,
    GraphicRequest,
    MotionFacts,
    Placement,
    SpecLine,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.values import ChartKind, GraphicKind, Origin

K = GraphicKind


class SlotType(StrEnum):
    TEXT = "text"
    #: Exact price in cents (``7999`` -> the style prints ``79,99 €``).
    MONEY = "money"
    #: ``dd.mm.`` or ``dd.mm.yyyy``.
    DATE = "date"
    URL = "url"
    RANK = "rank"
    #: A library image asset id (a prepared cut-out).
    IMAGE = "image"
    #: Several short texts.
    LIST = "list"
    #: Rows of short texts (``columns`` cells each).
    TABLE = "table"


#: What a slot value may be (the typed surface: str, int, tuple[str], tuple[tuple[str]]).
type SlotValue = str | int | tuple[str, ...] | tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Slot:
    name: str
    type: SlotType
    required: bool = False
    #: Longest text (per item / per cell for lists and tables).
    max_chars: int = 0
    max_items: int = 0
    #: Cells per row of a TABLE slot (min, max).
    columns: tuple[int, int] = (0, 0)
    help: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Preset:
    kind: GraphicKind
    slots: tuple[Slot, ...]
    #: The first one is the default.
    variants: tuple[str, ...]
    #: Entry/exit pair (the style's patterns); the same for every graphic of the kind.
    entry: str
    exit: str
    #: Zones a graphic of this kind may use; the first is the default.
    zones: tuple[str, ...]
    #: Hold as a multiple of the style's ``motion.min_hold`` (before reading time).
    hold: float = 2.0
    default_emphasis: int = 1
    #: Executor and brand kinds are not ordered through this surface.
    orderable: bool = True
    note: str = ""

    def slot(self, name: str) -> Slot | None:
        return next((s for s in self.slots if s.name == name), None)


EMPHASIS_LEVELS: tuple[int, ...] = (0, 1, 2)
#: An entry pattern's natural exit (used whenever a request names no exit).
EXIT_FOR: dict[str, str] = {
    "pop_in": "pop_out",
    "slide_in": "slide_out",
    "wipe_in": "fade_out",
    "fade_in": "fade_out",
    "cut": "cut",
}
_CARD = ("pop_in", "pop_out")  # stickers and tags
_PANEL = ("slide_in", "fade_out")  # cards: the panel wipes open, rows slide in, all fade out
_TITLE = ("fade_in", "fade_out")
_CUT = ("cut", "cut")
_LEFT = ("callout_left", "callout_right")
_RIGHT = ("callout_right", "callout_left")


def _t(name: str, chars: int, required: bool = False, note: str = "") -> Slot:
    return Slot(name=name, type=SlotType.TEXT, required=required, max_chars=chars, help=note)


def _p(
    kind: GraphicKind,
    slots: tuple[Slot, ...],
    variants: tuple[str, ...],
    motion: tuple[str, str],
    zones: tuple[str, ...],
    **extra: object,
) -> Preset:
    return Preset(
        kind=kind,
        slots=slots,
        variants=variants,
        entry=motion[0],
        exit=motion[1],
        zones=zones,
        **extra,  # type: ignore[arg-type]
    )


_LABEL = _t("label", 18, note="short context label above the text, e.g. 'PREIS'")
_IMAGE = Slot(name="image", type=SlotType.IMAGE, help="prepared cut-out asset id")

PRESETS: dict[GraphicKind, Preset] = {
    p.kind: p
    for p in (
        _p(K.TITLE, (_t("title", 40, True), _t("subtitle", 40)), ("glow",), _TITLE, ("card_safe",)),
        _p(K.INTRO, (_t("title", 30, True),), ("glow",), _TITLE, ("card_safe",)),
        _p(K.SECTION_TITLE, (_t("title", 14, True),), ("outline",), _CARD, ("title_band",)),
        _p(
            K.LOWER_THIRD,
            (_t("name", 28, True), _t("role", 36)),
            ("dark", "accent"),
            _PANEL,
            ("lower_third_band",),
        ),
        _p(
            K.CALLOUT,
            (_t("text", 60, True), _LABEL),
            ("dark", "accent", "highlight"),
            _PANEL,
            _LEFT,
            note="a statement with its context label; never a bare number",
        ),
        _p(K.TEXT_PANEL, (_t("text", 60, True), _LABEL), ("dark", "black"), _PANEL, _LEFT),
        _p(K.HIGHLIGHT_PANEL, (_t("text", 40, True), _LABEL), ("highlight",), _PANEL, _LEFT),
        _p(
            K.PRICE_TAG,
            (
                Slot(name="price", type=SlotType.MONEY, required=True),
                _t("product", 40, note="what the price is for"),
                _t("label", 14, note="e.g. 'NUR', 'STATT'"),
            ),
            ("tag",),
            _CARD,
            ("callout_right", "callout_left", "lower_third_band"),
        ),
        _p(
            K.RANK_CARD,
            (Slot(name="rank", type=SlotType.RANK, required=True),),
            ("outline",),
            _CARD,
            ("lower_third_band",),
            hold=2.5,
        ),
        _p(
            K.SILHOUETTE_TEASER,
            (
                Slot(name="image", type=SlotType.IMAGE, required=True),
                Slot(name="rank", type=SlotType.RANK),
            ),
            ("silhouette",),
            _CARD,
            ("callout_right", "callout_left"),
        ),
        _p(
            K.SPEC_CARD,
            (
                _t("product", 34),
                Slot(
                    name="facts",
                    type=SlotType.TABLE,
                    required=True,
                    max_chars=12,
                    max_items=4,
                    columns=(2, 2),
                    help="(label, value) pairs, e.g. ('Schuss', '100')",
                ),
                Slot(name="price", type=SlotType.MONEY),
                _IMAGE,
            ),
            ("tiles", "rows"),
            _PANEL,
            ("card_safe",),
            hold=3.0,
        ),
        _p(
            K.COMPARISON,
            (
                _t("left", 24, True),
                _t("right", 24, True),
                Slot(name="left_image", type=SlotType.IMAGE),
                Slot(name="right_image", type=SlotType.IMAGE),
            ),
            ("versus",),
            _PANEL,
            ("card_safe",),
            hold=3.0,
        ),
        _p(
            K.PRODUCT_STICKER,
            (Slot(name="image", type=SlotType.IMAGE, required=True),),
            ("glow", "shadow"),
            _CARD,
            _LEFT,
        ),
        _p(
            K.FEATURE_LIST,
            (
                _t("title", 32),
                Slot(name="items", type=SlotType.LIST, required=True, max_chars=36, max_items=4),
            ),
            ("dark",),
            _PANEL,
            _LEFT,
            hold=3.0,
        ),
        _p(
            K.STEP_LIST,
            (
                _t("title", 32),
                Slot(name="items", type=SlotType.LIST, required=True, max_chars=36, max_items=4),
            ),
            ("dark",),
            _PANEL,
            _LEFT,
            hold=3.0,
        ),
        _p(
            K.CHART,
            (
                _t("title", 32),
                Slot(
                    name="values",
                    type=SlotType.TABLE,
                    required=True,
                    max_chars=18,
                    max_items=5,
                    columns=(2, 2),
                    help="(label, number with optional unit), e.g. ('Lidl', '1,25 €')",
                ),
                _t("best", 14, note="label of the bar to mark (default: the largest)"),
            ),
            ("bars", "columns", "line", "pie", "progress", "counter"),
            _PANEL,
            ("card_safe", "callout_right", "callout_left"),
            hold=3.0,
        ),
        _p(
            K.TIMELINE,
            (
                Slot(
                    name="events",
                    type=SlotType.TABLE,
                    required=True,
                    max_chars=22,
                    max_items=6,
                    columns=(2, 3),
                    help="(date, text) or, two-lane, (lane, date, text)",
                ),
            ),
            ("steps", "two_lane"),
            _PANEL,
            ("lower_third_band", "card_safe"),
            hold=3.0,
        ),
        _p(K.CTA, (_t("text", 20),), ("accent",), _CARD, ("lower_third_band",)),
        _p(
            K.COMPARISON_MATRIX,
            (
                Slot(name="columns", type=SlotType.LIST, required=True, max_chars=10, max_items=3),
                Slot(
                    name="rows",
                    type=SlotType.TABLE,
                    required=True,
                    max_chars=18,
                    max_items=5,
                    columns=(2, 4),
                    help="(product, value, ...) one value per column",
                ),
                _t("best", 18, note="product of the row to mark"),
                _t("title", 32),
            ),
            ("table",),
            _PANEL,
            ("card_safe",),
            hold=4.0,
        ),
        _p(
            K.STRIKE_VALUE,
            (_t("label", 18, True), _t("old", 10, True), _t("new", 14)),
            ("strike",),
            _PANEL,
            _RIGHT,
        ),
        _p(
            K.URL_CARD,
            (Slot(name="url", type=SlotType.URL, required=True, max_chars=32), _LABEL),
            ("dark", "accent"),
            _PANEL,
            ("lower_third_band",),
            hold=3.0,
        ),
        _p(
            K.DEADLINE_CHIP,
            (_t("label", 12), Slot(name="date", type=SlotType.DATE, required=True)),
            ("accent", "highlight"),
            _CARD,
            _RIGHT,
        ),
        _p(K.END_SCREEN, (), ("cards",), _CUT, ("card_safe",), orderable=False),
        _p(K.MEMBERSHIP_PLUG, (_t("text", 24),), ("plate",), _TITLE, ("card_safe",)),
        _p(K.PIP_FRAME, (), ("frame",), _TITLE, ("callout_right",)),
        _p(K.SPONSOR_LABEL, (), ("chip",), _CUT, ("sponsor_label",), orderable=False),
        _p(K.LOGO_STING, (), ("logo",), _CARD, ("card_safe",), orderable=False),
        _p(K.ARROW, (), ("plain",), _CARD, _LEFT, orderable=False, note="needs a track"),
        _p(K.TRACKING_LABEL, (), ("dark",), _CARD, _LEFT, orderable=False, note="needs a track"),
        _p(K.CAPTION, (), ("word_group",), _CUT, ("caption_band",), orderable=False),
        _p(K.TRANSITION_OVERLAY, (), ("overlay",), _CUT, (), orderable=False),
    )
}

_DATE = re.compile(r"^(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(\d{4})?$")
_URL = re.compile(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/[\w./%-]*)?$", re.IGNORECASE)
_NUMBER = re.compile(
    r"^(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*(?P<unit>[^\d\s]{0,4})$"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicOrder:
    """What a planner sends: kind, slots, variant, emphasis and WHEN the fact is spoken.

    ``at`` (timeline seconds) is the moment the graphic must be fully visible; it opens one
    entry earlier. ``duration`` is optional: by default the preset's hold, longer for long text.
    """

    kind: GraphicKind
    at: Rational
    slots: Mapping[str, SlotValue] = field(default_factory=dict)
    variant: str = ""
    emphasis: int | None = None
    duration: Rational | None = None
    request_id: str = ""
    section_id: str | None = None
    priority: float = 0.5
    zone: str = ""
    reason: str = ""


def parse_number(text: str) -> DataPoint | None:
    """``'1.299,95 €'`` -> value 1299.95, unit ``€`` (German format only; nothing guessed)."""
    match = _NUMBER.match(text.strip())
    if match is None:
        return None
    number = match.group("num").replace(".", "").replace(",", ".")
    value = Fraction(number)
    return DataPoint(
        label="", value=Rational(value.numerator, value.denominator), unit=match.group("unit")
    )


def _check(slot: Slot, value: SlotValue, problems: list[str]) -> None:
    where = f"slot {slot.name!r}"

    def text(v: object, what: str = where) -> None:
        if not isinstance(v, str) or not v.strip():
            problems.append(f"{what}: expected non-empty text")
        elif slot.max_chars and len(v.strip()) > slot.max_chars:
            problems.append(f"{what}: {len(v.strip())} characters, at most {slot.max_chars}")

    t = slot.type
    if t in (SlotType.MONEY, SlotType.RANK):
        low = 1 if t is SlotType.RANK else 0
        if not isinstance(value, int) or isinstance(value, bool) or value < low:
            unit = "a rank >= 1" if t is SlotType.RANK else "cents as a whole number >= 0"
            problems.append(f"{where}: expected {unit}")
    elif t is SlotType.DATE:
        if not isinstance(value, str) or not _DATE.match(value.strip()):
            problems.append(f"{where}: expected a date like '29.12.' or '29.12.2026'")
    elif t is SlotType.URL:
        text(value)
        if isinstance(value, str) and not _URL.match(value.strip()):
            problems.append(f"{where}: {value!r} is not a web address")
    elif t in (SlotType.TEXT, SlotType.IMAGE):
        text(value)
    elif t is SlotType.LIST:
        if not isinstance(value, tuple) or not value:
            problems.append(f"{where}: expected a non-empty list of texts")
            return
        if slot.max_items and len(value) > slot.max_items:
            problems.append(f"{where}: {len(value)} items, at most {slot.max_items}")
        for i, item in enumerate(value):
            text(item, f"{where}[{i}]")
    elif t is SlotType.TABLE:
        if not isinstance(value, tuple) or not value:
            problems.append(f"{where}: expected a non-empty table (tuple of rows)")
            return
        if slot.max_items and len(value) > slot.max_items:
            problems.append(f"{where}: {len(value)} rows, at most {slot.max_items}")
        low, high = slot.columns
        for i, row in enumerate(value):
            if not isinstance(row, tuple) or not low <= len(row) <= high:
                problems.append(f"{where}[{i}]: expected {low}-{high} cells")
                continue
            for j, cell in enumerate(row):
                text(cell, f"{where}[{i}][{j}]")


def validate_order(order: GraphicOrder) -> list[str]:
    """Every reason the order cannot be drawn (empty = valid)."""
    preset = PRESETS.get(order.kind)
    if preset is None or not preset.orderable:
        allowed = sorted(k.value for k, p in PRESETS.items() if p.orderable)
        return [f"kind {order.kind.value!r} cannot be ordered; choose one of {allowed}"]
    problems: list[str] = []
    for name in order.slots:
        if preset.slot(name) is None:
            names = [s.name for s in preset.slots]
            problems.append(f"unknown slot {name!r} for {order.kind.value}; slots: {names}")
    for slot in preset.slots:
        value = order.slots.get(slot.name)
        if value is None:
            if slot.required:
                problems.append(f"slot {slot.name!r} is required")
            continue
        _check(slot, value, problems)
    if order.variant and order.variant not in preset.variants:
        problems.append(f"variant {order.variant!r} unknown; choose one of {list(preset.variants)}")
    if order.emphasis is not None and order.emphasis not in EMPHASIS_LEVELS:
        problems.append(f"emphasis {order.emphasis} unknown; choose one of {list(EMPHASIS_LEVELS)}")
    if order.zone and order.zone not in preset.zones:
        problems.append(f"zone {order.zone!r} not used by {order.kind.value}; {list(preset.zones)}")
    if Fraction(order.at.numerator, order.at.denominator) < 0:
        problems.append("'at' must be >= 0 (timeline seconds)")
    if order.duration is not None and order.duration.numerator <= 0:
        problems.append("duration must be positive")
    variant = order.variant or preset.variants[0]
    problems += _semantic(order, variant)
    return problems


def _semantic(order: GraphicOrder, variant: str) -> list[str]:
    s, problems = order.slots, []
    if order.kind is K.CHART:
        values = s.get("values", ())
        for i, row in enumerate(values if isinstance(values, tuple) else ()):
            if isinstance(row, tuple) and len(row) == 2 and parse_number(str(row[1])) is None:
                problems.append(f"slot 'values'[{i}]: {row[1]!r} is not a number like '1,25 €'")
        count = len(values) if isinstance(values, tuple) else 0
        need = {"counter": (1, 1), "progress": (1, 2), "line": (2, 5), "pie": (2, 5)}
        low, high = need.get(variant, (2, 5))
        if not low <= count <= high:
            problems.append(f"variant {variant!r} needs {low}-{high} values, got {count}")
    if order.kind is K.TIMELINE:
        events = s.get("events", ())
        rows = events if isinstance(events, tuple) else ()
        cells = 3 if variant == "two_lane" else 2
        if any(isinstance(r, tuple) and len(r) != cells for r in rows):
            problems.append(f"variant {variant!r} needs {cells} cells per event")
        if variant == "two_lane":
            lanes = {r[0] for r in rows if isinstance(r, tuple) and r}
            if len(lanes) != 2:
                problems.append(f"two_lane needs exactly 2 lanes, got {len(lanes)}")
        elif len(rows) > 4:
            problems.append("steps shows at most 4 events")
        for i, row in enumerate(rows):
            date = row[-2] if isinstance(row, tuple) and len(row) >= 2 else ""
            if isinstance(date, str) and not _DATE.match(date.strip()):
                problems.append(f"slot 'events'[{i}]: date {date!r} is not like '09.11.'")
    if order.kind is K.COMPARISON_MATRIX:
        columns = s.get("columns", ())
        table = s.get("rows", ())
        width = len(columns) if isinstance(columns, tuple) else 0
        lines = table if isinstance(table, tuple) else ()
        for i, row in enumerate(lines):
            if isinstance(row, tuple) and len(row) != width + 1:
                problems.append(f"slot 'rows'[{i}]: {width} values expected after the product")
        best = s.get("best")
        names = {str(r[0]) for r in lines if isinstance(r, tuple) and r}
        if isinstance(best, str) and best not in names:
            problems.append(f"slot 'best': {best!r} is not a product of the table")
    text = s.get("text")
    if (
        order.kind is K.CALLOUT
        and isinstance(text, str)
        and not s.get("label")
        and bare_figure(text)
    ):
        problems.append("a bare number needs its 'label' (what is it? e.g. 'PREIS')")
    return problems


def bare_figure(text: str) -> bool:
    """A number or a date with no word of three letters ("79,99", "09.11.", "3 / 25"): alone
    it says nothing."""
    return bool(re.search(r"\d", text)) and not re.search(r"[^\W\d_]{3,}", text)


def _text(value: SlotValue | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def _content(order: GraphicOrder, variant: str) -> GraphicContent:
    s, kind = order.slots, order.kind
    emphasis = order.emphasis
    image = (_text(s.get("image")),) if _text(s.get("image")) else ()
    base = GraphicContent(variant=variant, emphasis=emphasis, eyebrow=_text(s.get("label")))
    if kind in (K.TITLE, K.INTRO, K.SECTION_TITLE):
        sub = _text(s.get("subtitle"))
        return _with(base, headline=_text(s.get("title")), body=(sub,) if sub else ())
    if kind is K.LOWER_THIRD:
        role = _text(s.get("role"))
        return _with(base, headline=_text(s.get("name")), body=(role,) if role else ())
    if kind in (K.CALLOUT, K.TEXT_PANEL, K.HIGHLIGHT_PANEL, K.CTA, K.MEMBERSHIP_PLUG):
        return _with(base, headline=_text(s.get("text")))
    if kind is K.PRICE_TAG:
        price = s.get("price")
        return _with(
            base,
            headline=_text(s.get("product")),
            price_cents=price if isinstance(price, int) else None,
        )
    if kind in (K.RANK_CARD, K.SILHOUETTE_TEASER):
        rank = s.get("rank")
        return _with(base, rank=rank if isinstance(rank, int) else None, image_asset_ids=image)
    if kind is K.SPEC_CARD:
        facts = s.get("facts", ())
        price = s.get("price")
        return _with(
            base,
            headline=_text(s.get("product")),
            specs=tuple(
                SpecLine(label=str(r[0]).strip(), value=str(r[1]).strip())
                for r in (facts if isinstance(facts, tuple) else ())
                if isinstance(r, tuple)
            ),
            price_cents=price if isinstance(price, int) else None,
            image_asset_ids=image,
        )
    if kind is K.COMPARISON:
        images = tuple(_text(s.get(n)) for n in ("left_image", "right_image"))
        return _with(
            base,
            body=(_text(s.get("left")), _text(s.get("right"))),
            image_asset_ids=images if all(images) else (),
        )
    if kind is K.PRODUCT_STICKER:
        return _with(base, image_asset_ids=image)
    if kind in (K.FEATURE_LIST, K.STEP_LIST):
        items = s.get("items", ())
        return _with(
            base,
            headline=_text(s.get("title")),
            body=tuple(str(i).strip() for i in (items if isinstance(items, tuple) else ())),
        )
    if kind is K.CHART:
        chart = {
            "bars": ChartKind.BAR,
            "columns": ChartKind.BAR,
            "line": ChartKind.LINE,
            "pie": ChartKind.PIE,
            "progress": ChartKind.PROGRESS,
            "counter": ChartKind.COUNTER,
        }[variant]
        values = s.get("values", ())
        data = []
        for row in values if isinstance(values, tuple) else ():
            if isinstance(row, tuple) and (point := parse_number(str(row[1]))) is not None:
                data.append(
                    DataPoint(label=str(row[0]).strip(), value=point.value, unit=point.unit)
                )
        best = _text(s.get("best"))
        return _with(
            base,
            headline=_text(s.get("title")),
            chart=chart,
            data=tuple(data),
            highlight=(best,) if best else (),
        )
    if kind is K.TIMELINE:
        events = s.get("events", ())
        rows = tuple(
            tuple(str(c).strip() for c in r)
            for r in (events if isinstance(events, tuple) else ())
            if isinstance(r, tuple)
        )
        return _with(base, rows=rows)
    if kind is K.COMPARISON_MATRIX:
        columns = s.get("columns", ())
        table = s.get("rows", ())
        head = ("", *(str(c).strip() for c in (columns if isinstance(columns, tuple) else ())))
        body = tuple(
            tuple(str(c).strip() for c in r)
            for r in (table if isinstance(table, tuple) else ())
            if isinstance(r, tuple)
        )
        best = _text(s.get("best"))
        return _with(
            base,
            headline=_text(s.get("title")),
            rows=(head, *body),
            highlight=(best,) if best else (),
        )
    if kind is K.STRIKE_VALUE:
        new = _text(s.get("new"))
        return _with(base, body=(_text(s.get("old")),), headline=new)
    if kind is K.URL_CARD:
        return _with(base, headline=_text(s.get("url")))
    if kind is K.DEADLINE_CHIP:
        return _with(
            base, eyebrow=_text(s.get("label")) or "NUR BIS", headline=_text(s.get("date"))
        )
    return base


def _with(base: GraphicContent, **changes: object) -> GraphicContent:
    return replace(base, **changes)  # type: ignore[arg-type]


def _letters(content: GraphicContent) -> int:
    parts = [content.eyebrow, content.headline, *content.body, content.price_text]
    parts += [s.label + s.value for s in content.specs] + [d.label for d in content.data]
    parts += [" ".join(r) for r in content.rows]
    return sum(len(p) for p in parts)


def order_duration(
    order: GraphicOrder, content: GraphicContent, motion: MotionFacts, reading_cps: float
) -> Fraction:
    """Entry + hold + exit; the hold is the preset's and grows with the text to read."""
    if order.duration is not None:
        return Fraction(order.duration.numerator, order.duration.denominator)
    preset = PRESETS[order.kind]
    minimum = Fraction(motion.min_hold.numerator, motion.min_hold.denominator)
    hold = max(
        minimum * Fraction(str(preset.hold)),
        Fraction(_letters(content)) / Fraction(str(reading_cps)),
    )
    entry = Fraction(motion.entry_max.numerator, motion.entry_max.denominator)
    exit_ = Fraction(motion.exit_max.numerator, motion.exit_max.denominator)
    return entry + hold + exit_


def compile_order(
    order: GraphicOrder, motion: MotionFacts, *, reading_cps: float = 14.0
) -> GraphicRequest:
    """A valid order -> its request (raises ``InvalidGraphicOrder`` with every problem)."""
    problems = validate_order(order)
    if problems:
        raise InvalidGraphicOrder(order.request_id or order.kind.value, problems)
    preset = PRESETS[order.kind]
    variant = order.variant or preset.variants[0]
    content = _content(order, variant)
    duration = order_duration(order, content, motion, reading_cps)
    entry = Fraction(motion.entry_max.numerator, motion.entry_max.denominator)
    at = Fraction(order.at.numerator, order.at.denominator)
    start = max(Fraction(0), at - entry)
    request_id = order.request_id or f"order:{order.kind.value}:{at.numerator}/{at.denominator}"
    return GraphicRequest(
        request_id=request_id,
        kind=order.kind,
        content=content,
        timing=Timing(
            local_duration=Rational(duration.numerator, duration.denominator),
            timeline_start=Rational(start.numerator, start.denominator),
        ),
        placement=Placement(zone=order.zone),
        animation=AnimationRequest(intensity=_INTENSITY[content.emphasis]),
        priority=order.priority,
        reason=order.reason or f"ordered {order.kind.value} ({variant})",
        origin=Origin.EDITORIAL,
        section_id=order.section_id,
    )


#: Emphasis -> animation intensity (None = the style's character).
_INTENSITY: dict[int | None, int | None] = {None: None, 0: 1, 1: None, 2: 3}


def catalogue() -> list[dict[str, object]]:
    """The orchestration surface as plain data (e.g. for a model's prompt): per orderable kind
    its slots (type, required, limits), variants, emphasis levels and zones."""
    out: list[dict[str, object]] = []
    for kind, p in PRESETS.items():
        if not p.orderable:
            continue
        out.append(
            {
                "kind": kind.value,
                "variants": list(p.variants),
                "emphasis": list(EMPHASIS_LEVELS),
                "zones": list(p.zones),
                "slots": [
                    {
                        "name": s.name,
                        "type": s.type.value,
                        "required": s.required,
                        "max_chars": s.max_chars,
                        "max_items": s.max_items,
                        "columns": list(s.columns),
                        "help": s.help,
                    }
                    for s in p.slots
                ],
                "note": p.note,
            }
        )
    return out
