"""Text fitting with real font widths and the layout engine inside the style's zones."""

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.layout import lay_out
from media_house.modules.motion_graphics_engine.domain.model import (
    DataPoint,
    GraphicContent,
    Placement,
    Rect,
    SpecLine,
    TimedWord,
    TrackPoint,
)
from media_house.modules.motion_graphics_engine.domain.text import (
    apply_case,
    fit,
    german_number,
    measure,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    ChartKind,
    ElementType,
    GraphicKind,
    Origin,
    OverlayKind,
    Severity,
)
from tests.unit.motion_graphics_engine.helpers import facts, inputs, metrics, request

K = GraphicKind


def codes(issues: tuple[object, ...]) -> set[str]:
    return {getattr(i, "code") for i in issues}  # noqa: B009


def zone_rect(name: str, width: int = 1920, height: int = 1080) -> Rect:
    z = facts(width, height).zone(name)
    assert z is not None
    return Rect(left=z.left, top=z.top, width=z.width, height=z.height)


# --- text ---------------------------------------------------------------------------------


def test_german_numbers_are_exact_with_comma_and_thousands_dot() -> None:
    assert german_number(Rational(1599, 100)) == ("15,99", True)
    assert german_number(Rational(129900, 100)) == ("1.299", True)
    assert german_number(Rational(25, 2)) == ("12,5", True)
    assert german_number(Rational(1, 3)) == ("0,33", False)


def test_upper_case_writes_eszett_as_ss() -> None:
    assert apply_case("Größe", "upper") == "GRÖSSE"
    assert apply_case("kanalmitglied werden", "sentence") == "Kanalmitglied werden"


def test_wider_text_measures_wider_and_tracking_adds_width() -> None:
    m = metrics()["poppins_800"]
    assert measure("WWWW", m, 50, 0.0) > measure("iiii", m, 50, 0.0)
    assert measure("ABC", m, 50, 0.1) > measure("ABC", m, 50, 0.0)


def test_fit_wraps_then_shrinks_and_refuses_below_the_floor() -> None:
    m = metrics()["poppins_700"]
    text = "Diese Batterie ist der absolute Hammer zum Jahreswechsel"
    wrapped = fit(
        text,
        metrics=m,
        size_px=44,
        min_px=28,
        tracking_em=0,
        max_width=600,
        max_lines=3,
        max_chars=40,
    )
    assert wrapped is not None
    assert 1 < len(wrapped.lines) <= 3
    assert all(w <= 600 for w in wrapped.widths)
    one_line = fit(
        text,
        metrics=m,
        size_px=44,
        min_px=28,
        tracking_em=0,
        max_width=1000,
        max_lines=1,
        max_chars=80,
    )
    assert one_line is None or one_line.size_px < 44
    assert (
        fit(
            text,
            metrics=m,
            size_px=44,
            min_px=40,
            tracking_em=0,
            max_width=300,
            max_lines=1,
            max_chars=80,
        )
        is None
    )


# --- layout -------------------------------------------------------------------------------


def test_rank_card_is_centred_in_the_lower_third_band_with_the_red_stroke() -> None:
    comp, issues = lay_out(request(K.RANK_CARD, GraphicContent(rank=3)), inputs())

    assert comp is not None, issues
    assert comp.zone == "lower_third_band"
    assert comp.rect.inside(zone_rect("lower_third_band"))
    band = zone_rect("lower_third_band")
    assert abs((comp.rect.left + comp.rect.width / 2) - (band.left + band.width / 2)) <= 1
    text = comp.elements[0].text
    assert text is not None
    assert text.text == "PLATZ 3"
    assert text.stroke == facts().hex("brand_red")
    assert text.stroke_px == facts().graphics.rank_stroke_px


def test_price_tag_shows_the_style_formatted_price_on_the_yellow_panel() -> None:
    content = GraphicContent(headline="Lidl Batterie", price_cents=1599, price_text="15,99 €")
    comp, issues = lay_out(request(K.PRICE_TAG, content), inputs())

    assert comp is not None, issues
    texts = {e.id: e.text.text for e in comp.elements if e.text}
    assert texts["g1.price"] == "15,99 €"
    panel = next(e for e in comp.elements if e.type is ElementType.PANEL)
    assert panel.fill == facts().hex("price_fill")
    assert comp.rect.inside(zone_rect("callout_right"))


def test_a_price_without_exact_cents_is_refused() -> None:
    comp, issues = lay_out(request(K.PRICE_TAG, GraphicContent(headline="X")), inputs())
    assert comp is None
    assert "price.missing" in codes(issues)


def test_text_that_cannot_fit_is_refused_never_clipped() -> None:
    long = " ".join(["Feuerwerksbatterie"] * 30)
    comp, issues = lay_out(request(K.LOWER_THIRD, GraphicContent(headline=long)), inputs())
    assert comp is None
    assert "text.overflow" in codes(issues)


def test_a_zone_that_does_not_take_the_kind_is_an_error() -> None:
    req = request(
        K.PRICE_TAG,
        GraphicContent(price_cents=100, price_text="1,00 €"),
        placement=Placement(zone="caption_band"),
    )
    comp, issues = lay_out(req, inputs())
    assert comp is None
    assert "zone.wrong_kind" in codes(issues)


def test_vertical_layout_uses_the_9_16_zones() -> None:
    content = GraphicContent(price_cents=249, price_text="2,49 €")
    comp, issues = lay_out(request(K.PRICE_TAG, content), inputs(1080, 1920))
    assert comp is not None, issues
    assert comp.rect.inside(zone_rect("callout_right", 1080, 1920))


def test_spec_card_prints_the_supplied_rows_and_the_price() -> None:
    specs = (SpecLine(label="Schuss", value="100"), SpecLine(label="Höhe", value="30 m"))
    content = GraphicContent(headline="Gigant", specs=specs, price_cents=4999, price_text="49,99 €")
    comp, issues = lay_out(request(K.SPEC_CARD, content), inputs())
    assert comp is not None, issues
    texts = [e.text.text for e in comp.elements if e.text]
    assert {"SCHUSS", "HÖHE", "KOSTEN", "100", "30 m", "49,99 €"} <= set(texts)


def test_bar_chart_bars_are_proportional_to_the_supplied_values() -> None:
    data = (DataPoint(label="A", value=Rational(10, 1)), DataPoint(label="B", value=Rational(5, 1)))
    comp, issues = lay_out(
        request(K.CHART, GraphicContent(chart=ChartKind.BAR, data=data)), inputs()
    )
    assert comp is not None, issues
    bars = {e.id: e.rect.height for e in comp.elements if ".bar" in e.id}
    assert abs(bars["g1.bar0"] - 2 * bars["g1.bar1"]) <= 1


def test_a_chart_without_data_is_refused() -> None:
    comp, issues = lay_out(request(K.CHART, GraphicContent(chart=ChartKind.BAR)), inputs())
    assert comp is None
    assert "chart.no_data" in codes(issues)


def test_tracking_label_needs_a_supplied_track_and_stays_in_its_zone() -> None:
    comp, issues = lay_out(
        request(K.TRACKING_LABEL, GraphicContent(headline="Zündschnur")), inputs()
    )
    assert comp is None
    assert "track.missing" in codes(issues)
    track = (TrackPoint(local_at=Rational(0, 1), x=0.8, y=0.4),)
    comp, issues = lay_out(
        request(
            K.TRACKING_LABEL,
            GraphicContent(headline="Zündschnur"),
            placement=Placement(track=track),
        ),
        inputs(),
    )
    assert comp is not None, issues
    assert comp.zone == "callout_right"


def test_logo_sting_without_the_logo_asset_reports_it() -> None:
    comp, issues = lay_out(request(K.LOGO_STING), inputs())
    assert comp is None
    assert "brand.asset_missing" in codes(issues)


def test_end_screen_draws_both_labels_and_reports_the_missing_fire_plate() -> None:
    comp, issues = lay_out(request(K.END_SCREEN, origin=Origin.BRAND), inputs())
    assert comp is not None, issues
    assert [e.text.text for e in comp.elements if e.text] == ["ABONNIEREN", "WEITER SCHAUEN"]
    missing = [i for i in issues if i.code == "brand.asset_missing"]
    assert missing and missing[0].severity is Severity.WARNING


def test_caption_group_keeps_one_run_per_word_with_emphasis() -> None:
    words = tuple(
        TimedWord(
            text=t, local_start=Rational(i, 4), local_end=Rational(i + 1, 4), emphasis=t == "Hammer"
        )
        for i, t in enumerate(["Das", "ist", "der", "Hammer"])
    )
    comp, issues = lay_out(
        request(K.CAPTION, GraphicContent(words=words, look="word_group"), origin=Origin.CAPTION),
        inputs(1080, 1920),
    )
    assert comp is not None, issues
    text = comp.elements[0].text
    assert text is not None
    runs = [r for line in text.lines for r in line.runs]
    assert [r.text for r in runs] == ["DAS", "IST", "DER", "HAMMER"]
    assert [r.emphasis for r in runs] == [False, False, False, True]
    assert comp.zone == "caption_band"


def test_caption_follows_the_profile_default_look() -> None:
    words = (TimedWord(text="Silvester", local_start=Rational(0, 1), local_end=Rational(1, 1)),)
    comp, issues = lay_out(
        request(K.CAPTION, GraphicContent(words=words), origin=Origin.CAPTION), inputs(1080, 1920)
    )
    assert comp is not None, issues
    assert comp.zone == "title_band"  # shorts_meme: the static sentence-case line


def test_pixel_dissolve_covers_the_whole_frame() -> None:
    comp, issues = lay_out(
        request(
            K.TRANSITION_OVERLAY,
            GraphicContent(overlay=OverlayKind.PIXEL_DISSOLVE),
            origin=Origin.TRANSITION,
        ),
        inputs(),
    )
    assert comp is not None, issues
    assert comp.full_frame
    assert comp.rect.width >= 1920 and comp.rect.height >= 1080
