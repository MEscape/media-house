"""Short-form (9:16) rules: hook, short full-screen cards, fact punches, pops and the portrait
cards' full-bleed design and motion."""

from dataclasses import replace
from fractions import Fraction
from itertools import pairwise

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.application.ports import Scene
from media_house.modules.motion_graphics_engine.domain.brief import (
    GraphicsBrief,
    ProductFacts,
    SpecFact,
)
from media_house.modules.motion_graphics_engine.domain.engine import build
from media_house.modules.motion_graphics_engine.domain.layout import lay_out
from media_house.modules.motion_graphics_engine.domain.model import (
    Element,
    GraphicContent,
    GraphicRequest,
    Rect,
    SpecLine,
)
from media_house.modules.motion_graphics_engine.domain.planner import plan_requests
from media_house.modules.motion_graphics_engine.domain.presets import GraphicOrder
from media_house.modules.motion_graphics_engine.domain.profile import MotionGraphicsConfig
from media_house.modules.motion_graphics_engine.domain.short_form import (
    CARD_MAX,
    HOOK,
    is_full_frame,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    EditRef,
    ElementType,
    GraphicKind,
    Prop,
)
from media_house.modules.motion_graphics_engine.infrastructure.composition import compose_html
from tests.unit.motion_graphics_engine.helpers import facts, inputs, metrics, request, settings

K = GraphicKind
R = Rational
CUT = "cut"


def short_brief(**changes: object) -> GraphicsBrief:
    product = ProductFacts(
        product_id="p1",
        name="Produkt Eins",
        retailer="Markt",
        specs=(SpecFact(kind="shots", value=R(120, 1), timeline_at=R(16, 1)),),
    )
    intro = GraphicOrder(
        kind=K.RANK_CARD,
        at=R(1, 1),
        slots={"rank": 4, "label": "NR. 4/5", "product": "Produkt Eins", "image": CUT},
        variant="intro",
        duration=R(13, 5),
        request_id="card:0:intro",
    )
    data = GraphicOrder(
        kind=K.SPEC_CARD,
        at=R(4, 1),
        slots={
            "product": "Produkt Eins",
            "label": "NR. 4/5",
            "facts": (("Schuss", "120"), ("Höhe", "50 m")),
            "image": CUT,
        },
        variant="showcase",
        duration=R(5, 1),
        request_id="card:0:data",
    )
    base = GraphicsBrief(
        anchor_asset_id="anchor",
        project_id="p1",
        edit=EditRef(edit_plan_id="e1", plan_version=1),
        frame_rate=R(25, 1),
        width=1080,
        height=1920,
        timeline_duration=R(40, 1),
        products=(product,),
        orders=(intro, data),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def planned(b: GraphicsBrief, width: int = 1080, height: int = 1920) -> list[GraphicRequest]:
    cfg = MotionGraphicsConfig(profile="shorts")
    out = plan_requests(b, cfg, cfg.resolve(b.story_format), facts(width, height))
    return list(out.requests)


def span(r: GraphicRequest) -> tuple[Fraction, Fraction]:
    assert r.timing.timeline_start is not None
    a = Fraction(r.timing.timeline_start.numerator, r.timing.timeline_start.denominator)
    d = r.timing.local_duration
    return a, a + Fraction(d.numerator, d.denominator)


class TestPlanning:
    def test_a_short_opens_with_the_hook_and_cards_follow_it_short(self) -> None:
        requests = planned(short_brief())
        by_id = {r.request_id: r for r in requests}
        hook = by_id["short:hook"]
        assert hook.kind is K.TITLE and hook.content.variant == "hook"
        assert hook.content.headline == "TOP 5" and hook.content.body == ("Markt",)
        assert span(hook) == (Fraction(0), HOOK)
        cards = sorted(
            span(r) for r in requests if is_full_frame(r) and r.content.variant != "punch"
        )
        assert cards[0][0] >= HOOK  # never under the hook
        assert all(b - a <= CARD_MAX for a, b in cards)
        assert all(prev[1] <= nxt[0] for prev, nxt in pairwise(cards))

    def test_a_product_gets_one_card_at_its_introduction(self) -> None:
        requests = planned(short_brief())
        ids = {r.request_id for r in requests}
        assert "card:0:intro" not in ids
        (data,) = [r for r in requests if r.request_id == "card:0:data"]
        assert span(data)[0] == HOOK  # the intro's time (1 s), pushed past the hook

    def test_a_spoken_figure_is_punched_full_screen_with_the_products_cut_out(self) -> None:
        requests = planned(short_brief())
        (punch,) = [r for r in requests if r.content.variant == "punch"]
        assert punch.kind is K.CALLOUT
        assert (punch.content.headline, punch.content.eyebrow) == ("120", "Schuss")
        assert punch.content.image_asset_ids == (CUT,)
        assert span(punch)[0] == Fraction(16) - Fraction(1, 5)

    def test_quiet_stretches_get_the_current_products_cut_out(self) -> None:
        requests = planned(short_brief())
        pops = [r for r in requests if r.request_id.startswith("short:pop:")]
        assert pops and all(r.content.image_asset_ids == (CUT,) for r in pops)
        moments = sorted(span(r) for r in requests if r.kind is not K.SPONSOR_LABEL)
        ends = [Fraction(0)]
        for a, b in moments:  # nothing new for at most QUIET (4 s) until the end screen
            assert a - max(ends) <= 4 + Fraction(8, 5)
            ends.append(b)

    def test_landscape_plans_are_unchanged(self) -> None:
        requests = planned(short_brief(width=1920, height=1080), 1920, 1080)
        assert not [r for r in requests if r.request_id.startswith("short:")]


class TestPortraitCards:
    def test_portrait_cards_are_opaque_full_bleed_frames(self) -> None:
        content = GraphicContent(
            headline="Produkt", rank=4, eyebrow="NR. 4/5", image_asset_ids=(CUT,), variant="intro"
        )
        comp, _ = lay_out(request(K.RANK_CARD, content), inputs(1080, 1920, cut=(1000, 1000)))
        assert comp is not None and comp.full_frame
        ground = next(e for e in comp.elements if e.id.endswith(".ground"))
        assert (ground.rect.width, ground.rect.height, ground.opacity) == (1080, 1920, 1.0)
        assert any(e.id.endswith(".rank") for e in comp.elements)  # the big "#4" badge

    def test_landscape_cards_stay_translucent(self) -> None:
        content = GraphicContent(headline="Produkt", rank=4, variant="intro")
        comp, _ = lay_out(request(K.RANK_CARD, content), inputs())
        assert comp is not None
        ground = next(e for e in comp.elements if e.id.endswith(".ground"))
        assert ground.opacity < 1.0
        assert not any(e.id.endswith(".rank") for e in comp.elements)

    def test_a_fact_punch_counts_its_figure_up(self) -> None:
        content = GraphicContent(headline="120", eyebrow="Schuss", variant="punch")
        b = build(
            request(K.CALLOUT, content, seconds=2), facts(1080, 1920), metrics(), {}, settings()
        )
        assert b.composition is not None and b.composition.full_frame
        assert b.animation is not None
        counts = [t for t in b.animation.tracks if t.prop is Prop.COUNT]
        assert [t.target for t in counts] == ["g1.value.count"]
        assert counts[0].keyframes[-1].value == 120.0

    def test_the_showcase_figures_count_up_in_a_short(self) -> None:
        content = GraphicContent(
            headline="Produkt",
            specs=(SpecLine(label="Schuss", value="120"), SpecLine(label="Höhe", value="50 m")),
            variant="showcase",
        )
        b = build(request(K.SPEC_CARD, content), facts(1080, 1920), metrics(), {}, settings())
        assert b.animation is not None
        assert {t.target for t in b.animation.tracks if t.prop is Prop.COUNT} == {
            "g1.v0.count",
            "g1.v1.count",
        }

    def test_a_held_card_in_a_short_punches_once_halfway(self) -> None:
        content = GraphicContent(
            headline="Produkt",
            specs=(SpecLine(label="Schuss", value="120"), SpecLine(label="Höhe", value="50 m")),
            price_cents=7999,
            price_text="79,99 €",
            variant="showcase",
        )
        b = build(request(K.SPEC_CARD, content), facts(1080, 1920), metrics(), {}, settings())
        assert b.animation is not None
        (price,) = [
            t for t in b.animation.tracks if t.target == "g1.v2.count" and t.prop is Prop.SCALE
        ]
        assert max(k.value for k in price.keyframes) > 1.1  # the pop above its resting size

    def test_the_hook_title_is_drawn_large(self) -> None:
        plain = GraphicContent(headline="TOP 5")
        hook = GraphicContent(headline="TOP 5", variant="hook")
        small, _ = lay_out(request(K.TITLE, plain), inputs(1080, 1920))
        large, _ = lay_out(request(K.TITLE, hook), inputs(1080, 1920))
        assert small is not None and large is not None
        assert large.rect.height > small.rect.height


def test_a_brand_plate_covers_the_frame_whatever_its_aspect() -> None:
    content = GraphicContent(
        headline="P", specs=(SpecLine(label="A", value="1"),), variant="showcase"
    )
    comp, anim, _ = build(
        request(K.SPEC_CARD, content), facts(1080, 1920), metrics(), {}, settings()
    ).parts()
    frame = Rect(left=0, top=0, width=1080, height=1920)
    picture = Rect(left=10, top=10, width=100, height=100)
    elements = (
        Element(id="g1.plate", type=ElementType.IMAGE, rect=frame, z=0, image_asset_id="plate"),
        Element(id="g1.image", type=ElementType.IMAGE, rect=picture, z=1, image_asset_id="cut"),
    )
    scene = Scene(
        composition=replace(comp, elements=elements),
        animation=replace(anim, tracks=()),
        fps=R(25, 1),
        frames=10,
        easings=facts().easings,
        fonts=(),
        images=(),
    )
    html = compose_html(scene)
    assert html.count("object-fit:cover") == 1
    assert 'plate.png" style="object-fit:cover"' in html
