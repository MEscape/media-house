"""The preset system: orchestration surface validation, every preset built on the real MEsc v2
style without a defect, text fitting, choreography, zone allocation and the still fast path."""

from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from functools import cache

import pytest

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.application.style import style_facts, usage
from media_house.modules.motion_graphics_engine.domain.brief import (
    EditorialGraphic,
    ProductFacts,
    SpecFact,
)
from media_house.modules.motion_graphics_engine.domain.engine import (
    build,
    changing_frames,
    held_frames,
    resample,
    resolve_collisions,
)
from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicOrder
from media_house.modules.motion_graphics_engine.domain.layout import KIND_LAYOUT
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationRequest,
    FaceMetrics,
    GraphicContent,
    GraphicRequest,
    StyleFacts,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.planner import plan_requests
from media_house.modules.motion_graphics_engine.domain.presets import (
    PRESETS,
    GraphicOrder,
    SlotValue,
    catalogue,
    compile_order,
    validate_order,
)
from media_house.modules.motion_graphics_engine.domain.profile import MotionGraphicsConfig
from media_house.modules.motion_graphics_engine.domain.text import Fitted, fit
from media_house.modules.motion_graphics_engine.domain.values import (
    ElementType,
    GraphicKind,
    Intent,
    Severity,
)
from media_house.modules.motion_graphics_engine.infrastructure.font_metrics import read_metrics
from media_house.modules.visual_style_engine.domain.drift import validate_usage
from media_house.modules.visual_style_engine.domain.identity import MESC_IDENTITY_V2
from media_house.modules.visual_style_engine.domain.model import ResolvedStyle
from media_house.modules.visual_style_engine.domain.profile import StyleConfig
from media_house.modules.visual_style_engine.domain.resolve import resolve_style
from media_house.modules.visual_style_engine.infrastructure.font_files import FONT_DIR
from tests.unit.motion_graphics_engine.helpers import settings
from tests.unit.motion_graphics_engine.test_planner_profile_composition import brief

K = GraphicKind
R = Rational
AT = R(12, 1)
STICKER = "img:sticker"


@cache
def v2(width: int = 1920, height: int = 1080) -> ResolvedStyle:
    return resolve_style(
        MESC_IDENTITY_V2,
        StyleConfig(profile="ranking"),
        created_at=datetime(2026, 11, 9, tzinfo=UTC),
        width=width,
        height=height,
    )


@cache
def v2_facts(width: int = 1920, height: int = 1080) -> StyleFacts:
    return style_facts(v2(width, height))


@cache
def v2_metrics() -> dict[str, FaceMetrics]:
    return {
        f.key: read_metrics(f.key, (FONT_DIR / f.resource).read_bytes())
        for f in MESC_IDENTITY_V2.faces
    }


#: One realistic order per orderable preset (German product-ranking video).
SAMPLES: dict[GraphicKind, tuple[dict[str, SlotValue], tuple[str, ...]]] = {
    K.TITLE: ({"title": "Die 10 besten Batterien 2026", "subtitle": "Discounter-Ranking"}, ()),
    K.INTRO: ({"title": "Silvester-Check 2026"}, ()),
    K.SECTION_TITLE: ({"title": "Preis-Leistung"}, ()),
    K.LOWER_THIRD: ({"name": "Marvin Esch", "role": "Feuerwerks-Tester seit 2012"}, ("accent",)),
    K.CALLOUT: ({"text": "79,99 €", "label": "Preis im Prospekt"}, ("accent", "highlight")),
    K.TEXT_PANEL: ({"text": "Verkaufsstart 29.12. in allen Filialen"}, ("black",)),
    K.HIGHLIGHT_PANEL: ({"text": "Bester Preis im Test"}, ()),
    K.PRICE_TAG: ({"price": 7999, "product": "Lidl Feuerwerksbatterie Sternenzauber"}, ()),
    K.RANK_CARD: ({"rank": 3}, ()),
    K.SILHOUETTE_TEASER: ({"image": STICKER, "rank": 2}, ()),
    K.SPEC_CARD: (
        {
            "product": "Megablitz Verbundfeuerwerk",
            "facts": (("Schuss", "100"), ("Höhe", "35 m"), ("Brenndauer", "60 s")),
            "price": 7999,
            "image": STICKER,
        },
        ("rows",),
    ),
    K.COMPARISON: (
        {
            "left": "Lidl Sternenzauber",
            "right": "Aldi Himmelsfeuer",
            "left_image": STICKER,
            "right_image": STICKER,
        },
        (),
    ),
    K.PRODUCT_STICKER: ({"image": STICKER}, ("shadow",)),
    K.FEATURE_LIST: (
        {"title": "Darauf achten", "items": ("Kategorie F2", "Schusszahl pro Euro")},
        (),
    ),
    K.STEP_LIST: (
        {"title": "So klappt's", "items": ("App installieren", "Am 29.12. um 7 Uhr da sein")},
        (),
    ),
    K.CHART: (
        {"title": "Schuss pro Euro", "values": (("Lidl", "1,25"), ("Aldi", "0,98"))},
        ("columns", "line", "pie"),
    ),
    K.TIMELINE: ({"events": (("09.11.", "Prospekt"), ("29.12.", "Verkaufsstart"))}, ()),
    K.CTA: ({"text": "Jetzt abonnieren"}, ()),
    K.COMPARISON_MATRIX: (
        {
            "title": "Die Top 5",
            "columns": ("Schuss", "Preis"),
            "rows": tuple((f"Batterie {i}", str(20 * i), f"{10 * i},99 €") for i in range(1, 6)),
            "best": "Batterie 3",
        },
        (),
    ),
    K.STRIKE_VALUE: ({"label": "Wecker", "old": "04:30", "new": "ausschlafen"}, ()),
    K.URL_CARD: ({"url": "mesc.de/silvester", "label": "Alle Links"}, ("accent",)),
    K.DEADLINE_CHIP: ({"date": "29.12."}, ("highlight",)),
    K.MEMBERSHIP_PLUG: ({}, ()),
    K.PIP_FRAME: ({}, ()),
}


def orders() -> list[GraphicOrder]:
    out = []
    for kind, (slots, variants) in SAMPLES.items():
        for variant in ("", *variants):
            for emphasis in (0, 2):
                out.append(
                    GraphicOrder(
                        kind=kind,
                        at=AT,
                        slots=slots,
                        variant=variant,
                        emphasis=emphasis,
                        request_id=f"{kind.value}.{variant or 'default'}.{emphasis}",
                    )
                )
    return out


def built_request(order: GraphicOrder) -> GraphicRequest:
    request = compile_order(order, v2_facts().motion)
    if request.content.price_cents is not None:
        price = v2().format_price(request.content.price_cents)
        request = replace(request, content=replace(request.content, price_text=price))
    return request


# --- the orchestration surface ------------------------------------------------------------


def test_every_orderable_kind_has_a_sample_and_every_kind_a_preset() -> None:
    assert set(PRESETS) == set(GraphicKind)
    orderable = {k for k, p in PRESETS.items() if p.orderable}
    assert orderable == set(SAMPLES)
    for kind, preset in PRESETS.items():
        if preset.zones:  # the default zone is the layout engine's
            assert preset.zones[0] == KIND_LAYOUT[kind][1], kind
    assert {c["kind"] for c in catalogue()} == {k.value for k in orderable}


@pytest.mark.parametrize("order", orders(), ids=lambda o: o.request_id)
def test_every_preset_variant_and_emphasis_draws_cleanly(order: GraphicOrder) -> None:
    """Whatever a planner picks inside the surface: drawn, inside its zone, legible, no style
    drift ERROR, every text on a panel or outlined, and a stable choreography pair."""
    request = built_request(order)
    sizes = {STICKER: (900, 760)}
    b = build(request, v2_facts(), v2_metrics(), sizes, settings())
    errors = [i for i in b.issues if i.severity in (Severity.ERROR, Severity.CRITICAL)]
    assert b.usable and not errors, errors
    composition, animation, _ = b.parts()
    drift = validate_usage(v2(), usage(v2(), composition, animation))
    assert not [d for d in drift if d.severity.value in ("error", "critical")], drift
    texts = [e for e in composition.elements if e.text is not None]
    for e in texts:
        assert e.text is not None and e.text.size_px >= v2_facts().min_text_px
    preset = PRESETS[order.kind]
    assert (animation.motion.entry, animation.motion.exit) in {
        (preset.entry, preset.exit),
        ("fade_in", "fade_out"),  # emphasis 0 = intensity 1: the calm pair
        ("cut", "cut"),
    }


def test_orders_outside_their_preset_are_rejected_with_every_reason() -> None:
    bad = GraphicOrder(
        kind=K.CALLOUT,
        at=AT,
        slots={"txt": "x", "text": "y" * 61},
        variant="neon",
        emphasis=5,
        zone="card_safe",
    )
    problems = validate_order(bad)
    assert len(problems) == 5, problems
    assert any("unknown slot 'txt'" in p for p in problems)
    assert any("at most 60" in p for p in problems)
    assert any("variant 'neon'" in p for p in problems)
    assert any("emphasis 5" in p for p in problems)
    assert any("zone 'card_safe'" in p for p in problems)
    with pytest.raises(InvalidGraphicOrder) as caught:
        compile_order(bad, v2_facts().motion)
    assert len(caught.value.problems) == 5


@pytest.mark.parametrize(
    ("kind", "slots", "variant", "fragment"),
    [
        (K.CALLOUT, {"text": "79,99"}, "", "bare number"),
        (K.PRICE_TAG, {"price": "79,99"}, "", "cents"),
        (K.DEADLINE_CHIP, {"date": "Dienstag"}, "", "date like"),
        (K.URL_CARD, {"url": "nicht eine adresse"}, "", "web address"),
        (K.CHART, {"values": (("Lidl", "viel"),)}, "counter", "not a number"),
        (K.CHART, {"values": (("Lidl", "1"),)}, "bars", "needs 2-5 values"),
        (K.TIMELINE, {"events": (("2025", "28.12.", "a"),)}, "two_lane", "2 lanes"),
        (K.COMPARISON_MATRIX, {"columns": ("A",), "rows": (("x", "1", "2"),)}, "", "values"),
        (K.SPEC_CARD, {"facts": (("Schuss",),)}, "", "2-2 cells"),
        (K.SPONSOR_LABEL, {}, "", "cannot be ordered"),
    ],
)
def test_wrong_formats_are_rejected(
    kind: GraphicKind, slots: dict[str, SlotValue], variant: str, fragment: str
) -> None:
    problems = validate_order(GraphicOrder(kind=kind, at=AT, slots=slots, variant=variant))
    assert any(fragment in p for p in problems), problems


def test_an_order_is_fully_in_when_its_fact_is_spoken_and_long_text_stays_longer() -> None:
    motion = v2_facts().motion
    short = compile_order(
        GraphicOrder(kind=K.CALLOUT, at=AT, slots={"text": "Nur bei Lidl"}), motion
    )
    long = compile_order(
        GraphicOrder(kind=K.CALLOUT, at=AT, slots={"text": "Nur bei Lidl " * 4}), motion
    )
    entry = Fraction(motion.entry_max.numerator, motion.entry_max.denominator)
    start = short.timing.timeline_start
    assert start is not None and Fraction(start.numerator, start.denominator) == 12 - entry
    assert Fraction(
        long.timing.local_duration.numerator, long.timing.local_duration.denominator
    ) > (Fraction(short.timing.local_duration.numerator, short.timing.local_duration.denominator))


# --- text fitting -------------------------------------------------------------------------


def test_lines_are_balanced_and_shrunk_only_within_bounds() -> None:
    face = v2_metrics()["poppins_700"]

    def fitted(text: str, size: int, width: int, lines: int, balance: bool = True) -> Fitted | None:
        return fit(
            text,
            metrics=face,
            size_px=size,
            min_px=28,
            tracking_em=0.0,
            max_width=width,
            max_lines=lines,
            max_chars=80,
            balance=balance,
        )

    greedy = fitted("Schusszahl pro Euro", 44, 420, 2, balance=False)
    even = fitted("Schusszahl pro Euro", 44, 420, 2)
    assert greedy is not None and even is not None
    assert len(greedy.lines) == len(even.lines) == 2
    assert max(even.widths) <= max(greedy.widths)
    assert min(len(line) for line in even.lines) >= 1 and even.lines[-1] != ("Euro",)
    word = "Verbundfeuerwerksbatterie"
    shrunk = fitted(word, 88, 500, 1)
    assert shrunk is not None and 28 <= shrunk.size_px < 88 and shrunk.width <= 500
    assert fitted(word, 88, 200, 1) is None  # never clipped


# --- zone allocation --------------------------------------------------------------------


def test_at_most_three_overlays_and_none_overlap() -> None:
    kinds = (K.CALLOUT, K.TEXT_PANEL, K.PRICE_TAG, K.STRIKE_VALUE, K.DEADLINE_CHIP)
    requests = [
        built_request(
            GraphicOrder(
                kind=kind,
                at=AT,
                slots=SAMPLES[kind][0],
                request_id=f"o{i}",
                priority=0.9 - i * 0.1,
            )
        )
        for i, kind in enumerate(kinds)
    ]
    sizes = {STICKER: (900, 760)}
    built = [build(r, v2_facts(), v2_metrics(), sizes, settings()) for r in requests]
    built = resolve_collisions(built, v2_facts(), v2_metrics(), sizes, settings())
    drawn = [b for b in built if b.usable]
    assert len(drawn) <= v2_facts().graphics.max_simultaneous
    rects = [b.parts()[0].rect for b in drawn]
    assert not any(a.overlaps(b) for i, a in enumerate(rects) for b in rects[i + 1 :])


# --- the planner: no bare figures, timed to speech ---------------------------------------


def test_a_bare_figure_is_not_drawn_and_a_labelled_one_opens_when_spoken() -> None:
    graphics = (
        EditorialGraphic(
            request_id="bare",
            intent=Intent.SHOW_TEXT,
            timeline_at=R(40, 1),
            priority=0.5,
            concept="79,99",
        ),
        EditorialGraphic(
            request_id="labelled",
            intent=Intent.SHOW_TEXT,
            timeline_at=R(40, 1),
            priority=0.5,
            concept="09.11.",
            label="Prospekt ab",
            spoken_at=R(83, 1),
        ),
    )
    cfg = MotionGraphicsConfig()
    out = plan_requests(brief(graphics=graphics), cfg, cfg.resolve(), v2_facts())
    ids = {r.request_id: r for r in out.requests}
    assert "bare" not in ids
    assert any(i.code == "editorial.bare_figure" for i in out.issues)
    card = ids["labelled"]
    assert card.content.eyebrow == "Prospekt ab"
    start = card.timing.timeline_start
    entry = v2_facts().motion.entry_max
    assert start is not None
    assert Fraction(start.numerator, start.denominator) == 83 - Fraction(
        entry.numerator, entry.denominator
    )


def test_a_bare_figure_heard_as_a_fact_of_its_section_is_labelled_by_that_fact() -> None:
    product = ProductFacts(
        product_id="p1",
        price_cents=7999,
        specs=(SpecFact(kind="shots", value=R(187, 1), timeline_at=R(52, 1)),),
        timeline_at=R(47, 1),
        section_id="s1",
    )
    graphics = tuple(
        EditorialGraphic(
            request_id=rid,
            intent=Intent.SHOW_TEXT,
            timeline_at=R(at, 1),
            priority=0.5,
            concept=concept,
            section_id="s1",
        )
        for rid, concept, at in (("price", "79,99", 45), ("shots", "187", 50), ("odd", "42", 60))
    )
    cfg = MotionGraphicsConfig()
    out = plan_requests(
        brief(graphics=graphics, products=(product,)), cfg, cfg.resolve(), v2_facts()
    )
    ids = {r.request_id: r for r in out.requests}
    assert ids["price"].content.eyebrow == "Preis"
    assert ids["shots"].content.eyebrow == "Schuss"
    assert "odd" not in ids  # no fact says what 42 is


# --- the still fast path ------------------------------------------------------------------


def test_a_small_card_floats_while_held_and_a_full_frame_card_only_its_pictures() -> None:
    order = GraphicOrder(kind=K.CALLOUT, at=AT, slots=SAMPLES[K.CALLOUT][0], duration=R(6, 1))
    b = build(built_request(order), v2_facts(), v2_metrics(), {}, settings())
    _, animation, _ = b.parts()
    lifted = [t for t in animation.tracks if t.prop.value == "y"]
    assert lifted and all(max(abs(k.value) for k in t.keyframes) <= 8 for t in lifted)
    assert all(t.keyframes[-1].value == 0 for t in lifted)  # back in place before it leaves


def test_a_still_card_draws_its_entry_one_hold_frame_and_its_exit() -> None:
    # a card that does not float while held (small cards float, so every frame changes)
    order = GraphicOrder(kind=K.TIMELINE, at=AT, slots=SAMPLES[K.TIMELINE][0], duration=R(6, 1))
    b = build(built_request(order), v2_facts(), v2_metrics(), {}, settings())
    _, animation, frames = b.parts()
    samples = changing_frames(animation, frames.frames, frames.fps)
    assert samples[0] == 0 and len(samples) < frames.frames // 3
    shown = held_frames(samples, frames.frames)
    assert len(shown) == frames.frames
    for f, k in enumerate(shown):  # frame f shows the last drawn frame at or before it
        assert samples[k] == max(s for s in samples if s <= f)
    # resampled: drawn frame k is placed at frame k with the keyframes it had at samples[k]
    moved = resample(animation, samples, frames.frames, frames.fps)
    assert moved.duration == Rational(len(samples), 25)
    exit_ = [t for t in moved.tracks if len({k.value for k in t.keyframes}) > 1]
    assert exit_ and all(
        k.local_at.numerator * 25 // k.local_at.denominator <= len(samples)
        for t in exit_
        for k in t.keyframes
    )


def test_a_cut_in_cut_out_label_is_drawn_once() -> None:
    label = GraphicRequest(
        request_id="werbung",
        kind=K.SPONSOR_LABEL,
        content=GraphicContent(headline="Werbung"),
        timing=Timing(local_duration=R(85, 1), timeline_start=R(10, 1)),
        animation=AnimationRequest(entry="cut", exit="cut"),
    )
    b = build(label, v2_facts(), v2_metrics(), {}, settings())
    _, animation, frames = b.parts()
    assert changing_frames(animation, frames.frames, frames.fps) == (0,)
    assert held_frames((0,), frames.frames) == (0,) * frames.frames


def test_the_sponsor_label_sits_on_a_legible_chip() -> None:
    label = GraphicRequest(
        request_id="werbung",
        kind=K.SPONSOR_LABEL,
        content=GraphicContent(headline="Werbung"),
        timing=Timing(local_duration=R(5, 1), timeline_start=R(10, 1)),
        animation=AnimationRequest(entry="cut", exit="cut"),
    )
    composition = build(label, v2_facts(), v2_metrics(), {}, settings()).parts()[0]
    text = next(e for e in composition.elements if e.text is not None)
    chip = next(e for e in composition.elements if e.type is ElementType.PANEL)
    assert text.text is not None and text.text.size_px >= 34 and text.rect.inside(chip.rect)


def test_a_gallery_needs_its_pictures_and_a_sticker_its_picture() -> None:
    from media_house.modules.motion_graphics_engine.domain.presets import validate_order

    gallery = GraphicOrder(
        kind=K.PRODUCT_STICKER, at=AT, slots={"title": "Bisher"}, variant="gallery"
    )
    sticker = GraphicOrder(kind=K.PRODUCT_STICKER, at=AT, slots={})
    shown = replace(gallery, slots={"images": ("a", "b"), "captions": ("2024", "2025")})

    assert any("'images'" in p for p in validate_order(gallery))
    assert any("'image'" in p for p in validate_order(sticker))
    assert validate_order(shown) == []
