"""Planning from briefs (incl. the no-speech path), config metadata and deterministic HTML."""

from dataclasses import MISSING, fields, is_dataclass, replace
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from media_house.core.domain import Rational
from media_house.modules.content_intelligence.application.contracts import commerce_facts
from media_house.modules.motion_graphics_engine.application.ports import FontAsset, Scene
from media_house.modules.motion_graphics_engine.application.views import (
    price_cents,
    product_facts,
)
from media_house.modules.motion_graphics_engine.domain.brief import (
    BriefSection,
    EditorialGraphic,
    GraphicsBrief,
    ProductFacts,
    SpecFact,
    SponsoredSpan,
)
from media_house.modules.motion_graphics_engine.domain.engine import build
from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicsConfig
from media_house.modules.motion_graphics_engine.domain.model import GraphicContent
from media_house.modules.motion_graphics_engine.domain.planner import plan_requests
from media_house.modules.motion_graphics_engine.domain.profile import (
    PROFILES,
    MotionGraphicsConfig,
    MotionGraphicsProfile,
    SectionOverride,
    knob_fields,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    EditRef,
    GraphicKind,
    Intent,
    Origin,
)
from media_house.modules.motion_graphics_engine.infrastructure.composition import compose_html
from tests.unit.motion_graphics_engine.helpers import facts, metrics, request, settings

K = GraphicKind
R = Rational


def brief(**changes: object) -> GraphicsBrief:
    base = GraphicsBrief(
        anchor_asset_id="anchor",
        project_id="p1",
        edit=EditRef(edit_plan_id="e1", plan_version=1),
        frame_rate=R(25, 1),
        width=1920,
        height=1080,
        timeline_duration=R(300, 1),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def plan(
    b: GraphicsBrief, profile: str | None = None, **config: Any
) -> list[tuple[str, GraphicKind]]:
    cfg = MotionGraphicsConfig(profile=profile, **config)
    out = plan_requests(b, cfg, cfg.resolve(b.story_format), facts())
    return [(r.request_id, r.kind) for r in out.requests]


# --- planning -----------------------------------------------------------------------------


def test_no_speech_path_gets_only_the_bookends() -> None:
    b = brief(edit=None, project_id=None, sections=(), graphics=())
    assert plan(b) == [("brand:end_screen", K.END_SCREEN)]
    assert plan(b, "shorts") == []


def test_end_screen_is_appended_after_the_content_for_the_style_length() -> None:
    # Channel evidence (E2E O14): the fire end screen follows the content, it never covers it.
    cfg = MotionGraphicsConfig()
    out = plan_requests(brief(), cfg, cfg.resolve(), facts())
    end = out.requests[-1]
    assert end.origin is Origin.BRAND and "mandates" in end.reason
    assert end.timing.local_duration == R(20, 1) and end.timing.timeline_start == R(300, 1)
    assert "timing.no_room" not in [i.code for i in out.issues]


def test_editorial_price_request_becomes_a_price_tag_from_the_facts() -> None:
    product = ProductFacts(product_id="x", name="Lidl Batterie", price_cents=1599)
    g = EditorialGraphic(
        request_id="r1",
        intent=Intent.SHOW_TEXT,
        timeline_at=R(12, 1),
        priority=0.7,
        concept="Preis",
        product_id="x",
    )
    cfg = MotionGraphicsConfig()
    out = plan_requests(brief(graphics=(g,), products=(product,)), cfg, cfg.resolve(), facts())
    tag = out.requests[0]
    assert tag.kind is K.PRICE_TAG and tag.content.price_cents == 1599
    assert tag.timing.timeline_start == R(12, 1)


def test_a_products_price_is_drawn_once_however_many_requests_name_it() -> None:
    # E2E regression: two 'explain' requests of one ranking section drew '14,00 €' twice.
    product = ProductFacts(product_id="x", name="", price_cents=1400)
    requests = tuple(
        EditorialGraphic(
            request_id=f"r{n}",
            intent=Intent.EXPLAIN,
            timeline_at=R(at, 1),
            priority=0.5,
            concept="Platz 9",
            product_id="x",
        )
        for n, at in ((1, 2), (2, 15))
    )
    cfg = MotionGraphicsConfig()
    out = plan_requests(brief(graphics=requests, products=(product,)), cfg, cfg.resolve(), facts())

    tags = [r for r in out.requests if r.kind is K.PRICE_TAG]
    assert [t.request_id for t in tags] == ["r1"]
    assert any(i.code == "editorial.facts_already_shown" for i in out.issues)


def test_specs_make_a_spec_card_with_german_rows() -> None:
    specs = (
        SpecFact(kind="height", value=R(30, 1), unit="m"),
        SpecFact(kind="shots", value=R(100, 1)),
    )
    product = ProductFacts(product_id="x", name="Gigant", price_cents=4999, specs=specs)
    g = EditorialGraphic(
        request_id="r1",
        intent=Intent.EXPLAIN,
        timeline_at=R(12, 1),
        priority=0.7,
        concept="Daten",
        product_id="x",
    )
    cfg = MotionGraphicsConfig()
    card = plan_requests(
        brief(graphics=(g,), products=(product,)), cfg, cfg.resolve(), facts()
    ).requests[0]
    assert card.kind is K.SPEC_CARD
    assert [(s.label, s.value) for s in card.content.specs] == [("Schuss", "100"), ("Höhe", "30 m")]


def test_plain_concepts_become_callouts_or_panels_and_data_becomes_a_counter() -> None:
    from media_house.modules.motion_graphics_engine.domain.model import DataPoint

    graphics = (
        EditorialGraphic(
            request_id="a",
            intent=Intent.SHOW_TEXT,
            timeline_at=R(5, 1),
            priority=0.5,
            concept="Nur bei Lidl",
        ),
        EditorialGraphic(
            request_id="b",
            intent=Intent.EXPLAIN,
            timeline_at=R(50, 1),
            priority=0.5,
            concept="So zündest du richtig",
        ),
        EditorialGraphic(
            request_id="c",
            intent=Intent.EXPLAIN,
            timeline_at=R(90, 1),
            priority=0.5,
            concept="Schuss",
            data=(DataPoint(label="Schuss", value=R(100, 1)),),
        ),
    )
    kinds = dict(plan(brief(graphics=graphics)))
    assert kinds["a"] is K.CALLOUT and kinds["b"] is K.TEXT_PANEL and kinds["c"] is K.CHART


def test_ranking_profile_adds_rank_cards_and_the_next_rank_teaser() -> None:
    sections = (
        BriefSection(
            section_id="s3",
            title="Platz 3",
            purpose="rank",
            timeline_start=R(30, 1),
            timeline_end=R(80, 1),
            rank=3,
            product_id="p3",
        ),
        BriefSection(
            section_id="s2",
            title="Platz 2",
            purpose="rank",
            timeline_start=R(80, 1),
            timeline_end=R(130, 1),
            rank=2,
            product_id="p2",
        ),
    )
    products = (ProductFacts(product_id="p2", name="Vulkan", image_asset_ids=("img2",)),)
    cfg = MotionGraphicsConfig()
    b = brief(sections=sections, products=products, story_format="top_n")
    out = plan_requests(b, cfg, cfg.resolve("top_n"), facts())
    kinds = {r.request_id: r for r in out.requests}
    assert kinds["rank:s3"].content.rank == 3 and kinds["rank:s2"].kind is K.RANK_CARD
    teaser = kinds["teaser:s2"]
    assert teaser.kind is K.SILHOUETTE_TEASER and teaser.content.rank == 2
    assert teaser.timing.timeline_start is not None
    start = teaser.timing.timeline_start
    end = R(
        start.numerator * teaser.timing.local_duration.denominator
        + teaser.timing.local_duration.numerator * start.denominator,
        start.denominator * teaser.timing.local_duration.denominator,
    )
    assert end.value < 80
    assert [n.variant for n in out.images] == ["silhouette"]


def test_sponsored_parts_get_the_mandatory_label() -> None:
    spans = (SponsoredSpan(timeline_start=R(10, 1), timeline_end=R(70, 1)),)
    cfg = MotionGraphicsConfig()
    out = plan_requests(brief(sponsored=True, sponsored_spans=spans), cfg, cfg.resolve(), facts())
    label = next(r for r in out.requests if r.kind is K.SPONSOR_LABEL)
    assert label.content.headline == "Werbung" and label.timing.local_duration == R(60, 1)
    unknown = plan_requests(
        brief(sponsored=True, timeline_duration=None), cfg, cfg.resolve(), facts()
    )
    assert any(i.code == "sponsor.no_span" for i in unknown.issues)


def test_section_overrides_change_only_their_section() -> None:
    sections = (
        BriefSection(
            section_id="s1", title="t", purpose="p", timeline_start=R(0, 1), timeline_end=R(100, 1)
        ),
    )
    g = EditorialGraphic(
        request_id="a",
        intent=Intent.SHOW_TEXT,
        timeline_at=R(5, 1),
        priority=0.5,
        concept="Hi",
        section_id="s1",
    )
    base = MotionGraphicsConfig()
    slow = MotionGraphicsConfig(
        section_overrides=(SectionOverride(section_id="s1", overrides={"timing.hold_factor": 5.0}),)
    )
    b = brief(sections=sections, graphics=(g,))
    d0 = plan_requests(b, base, base.resolve(), facts()).requests[0].timing.local_duration
    d1 = plan_requests(b, slow, slow.resolve(), facts()).requests[0].timing.local_duration
    assert d1.value > d0.value


def test_commerce_facts_map_to_exact_cents() -> None:
    found = commerce_facts("Die Batterie kostet 15,99 € bei Lidl, Höhe 30 m, 100 Schuss.")
    p = product_facts("x", found, name="Batterie")
    assert p.price_cents == 1599 and p.retailer == "Lidl"
    assert {s.kind for s in p.specs} == {"height", "shots"}
    from fractions import Fraction

    assert price_cents(Fraction(1, 300)) is None


# --- configuration ------------------------------------------------------------------------


def _walk(group: object, prefix: str = "") -> list[tuple[str, Any, dict[str, Any]]]:
    out = []
    for f in fields(group):  # type: ignore[arg-type]
        value = getattr(group, f.name)
        if is_dataclass(value):
            out += _walk(value, f"{prefix}{f.name}.")
        elif f.metadata:
            out.append((f"{prefix}{f.name}", value, dict(f.metadata)))
    return out


def test_every_knob_has_a_label_and_its_range_is_enforced() -> None:
    profile = PROFILES["generic"]
    knobs = _walk(profile)
    assert {path for path, _ in knob_fields(MotionGraphicsProfile)} == {p for p, _, _ in knobs}
    for path, value, meta in knobs:
        assert meta["label"]
        if "min" in meta:
            low = meta["min"] - meta["step"]
            high = meta["max"] + meta["step"]
            if isinstance(value, int) and not isinstance(value, bool):
                low, high = int(low), int(high)
            with pytest.raises(InvalidGraphicsConfig):
                MotionGraphicsConfig(overrides={path: low}).resolve()
            with pytest.raises(InvalidGraphicsConfig):
                MotionGraphicsConfig(overrides={path: high}).resolve()
        if "choices" in meta:
            with pytest.raises(InvalidGraphicsConfig):
                MotionGraphicsConfig(overrides={path: "nonsense"}).resolve()
    for p in PROFILES.values():
        assert p.problems() == []
    assert MISSING is not None


def test_overrides_are_typed_and_named() -> None:
    assert (
        MotionGraphicsConfig(overrides={"timing.hold_factor": 3}).resolve().timing.hold_factor
        == 3.0
    )
    with pytest.raises(InvalidGraphicsConfig):
        MotionGraphicsConfig(overrides={"timing.nope": 1}).resolve()
    with pytest.raises(InvalidGraphicsConfig):
        MotionGraphicsConfig(overrides={"bookends.end_screen": "yes"}).resolve()
    assert MotionGraphicsConfig().resolve("top_n").name == "ranking"


# --- composition determinism --------------------------------------------------------------


def _scene() -> Scene:
    b = build(
        request(
            K.PRICE_TAG, GraphicContent(headline="Lidl", price_cents=1599, price_text="15,99 €")
        ),
        facts(),
        metrics(),
        {},
        settings(),
    )
    comp, anim, frames = b.parts()
    fonts = tuple(
        FontAsset(key=k, family=k, weight=700, path=Path(f"{k}.ttf"), sha256="0" * 64)
        for k in sorted(metrics())
    )
    return Scene(
        composition=comp,
        animation=anim,
        fps=frames.fps,
        frames=frames.frames,
        easings=facts().easings,
        fonts=fonts,
        images=(),
    )


def test_same_scene_gives_byte_identical_offline_html() -> None:
    first, second = compose_html(_scene()), compose_html(_scene())
    assert first == second
    assert 'data-composition-id="mg"' in first and 'data-duration="4"' in first
    assert "http://" not in first and "https://" not in first
    assert "Math.random" not in first and "Date.now" not in first
    assert ">15,99<" in first and "src: url('fonts/" in first
    assert "background: transparent" in first


def test_rational_frame_rates_get_an_exact_frame_count_duration() -> None:
    from media_house.modules.motion_graphics_engine.infrastructure.composition import (
        _duration_text,
    )

    assert _duration_text(50, R(30000, 1001)) == "1.668333"
    assert _duration_text(100, R(25, 1)) == "4"


# --- E2E regressions: cards timed to the spoken fact, every price once, no duplicate of the
# picture's own text, one card per zone at a time ------------------------------------------------
def _ranking() -> MotionGraphicsConfig:
    return MotionGraphicsConfig(profile="ranking")


def _section(**changes: object) -> BriefSection:
    base = BriefSection(
        section_id="s9",
        title="Platz 9",
        purpose="body",
        timeline_start=R(0, 1),
        timeline_end=R(40, 1),
        rank=9,
        product_id="product:s9",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_a_price_tag_and_a_rank_card_appear_when_the_fact_is_spoken() -> None:
    # The owner's ranking excerpt: "14,00 €" opened its section 33 s before the price was said.
    product = ProductFacts(
        product_id="product:s9", price_cents=1400, timeline_at=R(33, 1), section_id="s9"
    )
    cfg = _ranking()
    style = facts()
    lead = Rational(style.motion.entry_max.numerator, style.motion.entry_max.denominator)

    out = plan_requests(
        brief(sections=(_section(rank_at=R(4, 1)),), products=(product,)), cfg, cfg.resolve(), style
    )

    by_id = {r.request_id: r for r in out.requests}
    tag, card = by_id["price:product:s9"], by_id["rank:s9"]

    def at(r: Rational | None) -> Fraction:
        assert r is not None
        return Fraction(r.numerator, r.denominator)

    assert at(tag.timing.timeline_start) == 33 - at(lead)
    assert at(card.timing.timeline_start) == 4 - at(lead)


def test_every_spoken_price_of_a_section_gets_its_tag_and_one_zone_holds_one_at_a_time() -> None:
    products = tuple(
        ProductFacts(product_id=f"p{n}", price_cents=cents, timeline_at=R(at, 10), section_id="s9")
        for n, (cents, at) in enumerate(((999, 50), (399, 60), (249, 300)))
    )
    cfg = MotionGraphicsConfig(profile="shorts")

    out = plan_requests(
        brief(sections=(_section(rank=None),), products=products), cfg, cfg.resolve(), facts()
    )

    tags = sorted(
        (r for r in out.requests if r.kind is K.PRICE_TAG),
        key=lambda r: r.timing.timeline_start.numerator / r.timing.timeline_start.denominator,  # type: ignore[union-attr]
    )
    assert [t.content.price_cents for t in tags] == [999, 399, 249]
    spans = [
        (
            t.timing.timeline_start.numerator / t.timing.timeline_start.denominator,  # type: ignore[union-attr]
            t.timing.local_duration.numerator / t.timing.local_duration.denominator,
        )
        for t in tags
    ]
    assert all(a + d <= b + 1e-9 for (a, d), (b, _) in pairwise(spans))
    assert any(i.code == "layout.zone_queued" for i in out.issues)


def test_a_card_the_picture_already_shows_is_not_drawn_again_and_werbung_only_where_missing() -> (
    None
):
    from media_house.modules.motion_graphics_engine.domain.brief import OnScreenText

    shown = (
        OnScreenText(timeline_start=R(6, 1), timeline_end=R(10, 1), text="PLATZ 9"),
        OnScreenText(timeline_start=R(3, 1), timeline_end=R(300, 1), text="Werbung"),
    )
    cfg = _ranking()

    out = plan_requests(
        brief(sections=(_section(rank_at=R(5, 1)),), on_screen=shown, sponsored=True),
        cfg,
        cfg.resolve(),
        facts(),
    )

    kinds = [r.kind for r in out.requests]
    assert K.RANK_CARD not in kinds
    [label] = [r for r in out.requests if r.kind is K.SPONSOR_LABEL]
    assert label.timing.timeline_start == R(0, 1)
    assert label.timing.local_duration.numerator / label.timing.local_duration.denominator < 3
    assert [i.code for i in out.issues].count("editorial.already_on_screen") == 2


def test_a_sliver_left_after_the_shown_werbung_is_no_label_and_no_error() -> None:
    # E2E regression (B2): the source's "Werbung" ended just before the edit; the rest of the
    # sponsored span (under one frame) became an empty label and turned the plan partial.
    from media_house.modules.motion_graphics_engine.domain.brief import OnScreenText

    shown = (OnScreenText(timeline_start=R(3, 1), timeline_end=R(29848, 100), text="Werbung"),)
    cfg = _ranking()

    out = plan_requests(brief(on_screen=shown, sponsored=True), cfg, cfg.resolve(), facts())

    [label] = [r for r in out.requests if r.kind is K.SPONSOR_LABEL]
    assert label.timing.timeline_start == R(0, 1)
    assert "timing.empty" not in [i.code for i in out.issues]


def test_a_rounded_spoken_price_is_not_drawn_next_to_the_exact_price_the_picture_shows() -> None:
    # E2E regression (A): "für 14 Euro" was said while the picture showed "13,99 EUR"; our
    # "14,00 €" tag contradicted it.
    from media_house.modules.motion_graphics_engine.domain.brief import OnScreenText

    shown = (OnScreenText(timeline_start=R(34, 1), timeline_end=R(34, 1), text="13,99 EUR"),)
    product = ProductFacts(
        product_id="product:s9", price_cents=1400, timeline_at=R(33, 1), section_id="s9"
    )
    cfg = _ranking()

    out = plan_requests(
        brief(sections=(_section(),), products=(product,), on_screen=shown),
        cfg,
        cfg.resolve(),
        facts(),
    )

    assert K.PRICE_TAG not in [r.kind for r in out.requests]
    assert "editorial.already_on_screen" in [i.code for i in out.issues]
