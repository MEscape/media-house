"""The brief reader on the golden fixture (a synthetic reconstruction of the Lidl briefing)."""

from pathlib import Path

import pytest

from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.brief_parser import parse_brief, quote_in_brief
from media_house.modules.production_project.domain.values import ConstraintKind, ElementKind

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


@pytest.fixture(scope="module")
def text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brief(text: str) -> BriefModel:
    return parse_brief(text, reference_year=2026)


def test_header(brief: BriefModel) -> None:
    assert brief.client == "Lidl"
    assert brief.campaign == "Silvester-Feuerwerk Vorbestellung"


@pytest.mark.parametrize(
    ("rule", "kind"),
    [
        ("label.advertising", ConstraintKind.HARD),
        ("no.negative_about_client", ConstraintKind.HARD),
        ("no.competitor_comparison", ConstraintKind.HARD),
        ("no.third_party_brands", ConstraintKind.HARD),
        ("no.illegal_pyro", ConstraintKind.HARD),
        ("publish.after_approval", ConstraintKind.HARD),
        ("publish.not_before", ConstraintKind.HARD),
        ("confidential.item", ConstraintKind.CONFIDENTIAL),
        ("conditional.item", ConstraintKind.CONDITIONAL),
    ],
)
def test_constraints_are_found_with_their_kind(
    brief: BriefModel, rule: str, kind: ConstraintKind
) -> None:
    found = brief.rule(rule)
    assert found, rule
    assert {c.kind for c in found} == {kind}
    assert all(c.source.quote and c.source.ref for c in found)


def test_publishing_is_not_before_the_date_with_year_completed(brief: BriefModel) -> None:
    (rule,) = brief.rule("publish.not_before")
    assert rule.until == "2026-11-12T20:00"


def test_the_confidential_page_is_identified_by_keywords(brief: BriefModel) -> None:
    (c,) = brief.rule("confidential.item")
    assert "prospektseite" in c.keywords


def test_mandatory_elements(brief: BriefModel) -> None:
    kinds = {(e.kind, e.text) for e in brief.mandatory}
    assert (ElementKind.LABEL, "Werbung") in kinds
    assert (ElementKind.HASHTAG, "#LidlFeuerwerk") in kinds
    assert (ElementKind.HANDLE, "@lidl_de") in kinds
    (link,) = [e for e in brief.mandatory if e.kind is ElementKind.LINK]
    assert link.arrives_later


def test_the_spedition_information_is_optional_but_positive(brief: BriefModel) -> None:
    (option,) = brief.optional
    assert "Spedition" in option.text and option.phrasing == "positiv"


def test_the_five_products_with_prices_and_nothing_verified(brief: BriefModel) -> None:
    prices = {f.subject: f.value for f in brief.facts if f.kind == "price"}
    assert prices == {
        "feuerwerksbatterie-nova": "14,99",
        "raketenset-orion": "9,99",
        "verbundfeuerwerk-titan": "24,99",
        "fontaenen-box-lumen": "12,99",
        "kompaktbatterie-vega": "6,99",
    }
    assert not any(f.verified for f in brief.facts)


def test_windows_and_limits(brief: BriefModel) -> None:
    by_key = {f.key: f.value for f in brief.facts}
    assert by_key["order_window"] == "2026-11-09/2026-11-22"
    assert by_key["delivery_window"] == "2026-12-29/2026-12-31"
    assert by_key["max_per_artikel"] == "5"


def test_deadlines(brief: BriefModel) -> None:
    got = {(d.date, d.time) for d in brief.deadlines}
    assert {
        ("2026-11-10", "12:00"),
        ("2026-11-11", "12:00"),
        ("2026-11-12", "15:00"),
        ("2026-11-12", "18:00"),
    } <= got


def test_open_questions_put_the_confidential_page_first_and_ask_about_the_link(
    brief: BriefModel,
) -> None:
    texts = [q.text for q in brief.open_questions]
    assert texts[0].startswith("Darf das gezeigt werden?") or "Kurzvideos" in texts[0]
    assert any("Prospektseite" in t and t.startswith("Darf das gezeigt werden?") for t in texts)
    assert any("Tracking-Link" in t for t in texts)
    assert any("Freigabe vor" in t and "Archivmaterial" in t for t in texts)
    assert any("Kurzvideos" in t for t in texts)


def test_the_same_text_gives_the_same_model(text: str, brief: BriefModel) -> None:
    assert parse_brief(text, reference_year=2026) == brief


def test_page_numbers_become_the_source_reference() -> None:
    brief = parse_brief(
        "Intro.\fDas Video muss als Werbung gekennzeichnet sein.", reference_year=2026
    )

    assert brief.constraints[0].source.ref == "S. 2"


def test_a_model_quote_must_stand_in_the_brief(text: str) -> None:
    assert quote_in_brief("keine Vergleiche   mit anderen Anbietern", text)
    assert not quote_in_brief("Das Video muss rosa sein", text)


@pytest.mark.parametrize(
    "heading", ["Pflichtinhalte", "Video-Ziel und Pflichtinhalte", "Pflichtangaben:"]
)
def test_mandatory_sections_capture_imperatives_without_modal_words(heading: str) -> None:
    text = f"""ALDI Feuerwerk 2026/27 - Video-Briefing

{heading}

Ziel: Eine ehrliche Übersicht zum Sortiment.

- Quelle und Datum nennen: Vitrine oder Prospekt, Nord oder Süd, wann gesehen.
- Jeden Artikel zeigen, ohne Rosinenpicken: Name, Marke, Preis, Schusszahl und Kaliber.
- Umlabelungen klar von echten Neuheiten trennen.
- Unterschiede Nord und Süd erklären.

Weitere Ideen

Ein lustiger Einstieg mit einem Einkaufswagen.
"""
    brief = parse_brief(text, reference_year=2026)

    assert len(brief.constraints) == 4
    assert all(c.kind is ConstraintKind.HARD for c in brief.constraints)
    assert all(quote_in_brief(c.source.quote, text) for c in brief.constraints)
    assert not brief.rule("label.advertising")
    assert not brief.rule("no.competitor_comparison")
    assert not brief.rule("no.negative_about_client")


def test_layout_bullets_retain_wrapped_mandatory_content_and_page_reference() -> None:
    text = """Intro.\fVideo-Ziel und Pflichtinhalte

Ziel: Eine ehrliche Übersicht zum Sortiment.

     Jeden Artikel zeigen, ohne Rosinenpicken: Name, Marke (Weco, Nico, andere),
     Nord/Süd/beide, Preis, Schusszahl oder Stückzahl, Kaliber, NEM, Effekt, Kategorie.
     Hinweis, dass sich Preise bis zum Prospekt ändern; im Vorjahr gab es falsche
     Preisschilder.

Artikelliste der aktuellen Saison

Plasma Pulse ist noch ohne Preis.
"""
    brief = parse_brief(text, reference_year=2026)

    assert len(brief.constraints) == 2
    assert "Nord/Süd/beide" in brief.constraints[0].text
    assert "Preisschilder" in brief.constraints[1].text
    assert all(c.source.ref == "S. 2" for c in brief.constraints)
    assert all(quote_in_brief(c.source.quote, text) for c in brief.constraints)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Die Vitrine ist die jährliche Ausstellung und kein Leak.", "facts.no_leak"),
        ("Im Video deshalb keine Preisversprechen.", "facts.prices_pending"),
        ("Keine bestätigte Umlabelung.", "facts.identity_unconfirmed"),
        (
            "Golden Phoenix: Richtung der Umlabelung offen lassen, solange nicht belegt.",
            "facts.identity_unconfirmed",
        ),
        (
            "Vor Veröffentlichung die Verkaufstage gegen offizielle Prospekte prüfen.",
            "facts.verification_required",
        ),
        (
            "Vor dem Dreh jeden Preis am aktuellen Prospekt abgleichen.",
            "facts.verification_required",
        ),
        (
            "Fremde Bilder dürfen nicht ohne Rückfrage weiterverwendet werden.",
            "assets.permission_required",
        ),
        ("Vorab Dreherlaubnis im Markt einholen.", "assets.permission_required"),
        ("Ohne Antwort wird das Bild nicht verwendet.", "assets.permission_required"),
    ],
)
def test_editorial_uncertainty_and_rights_are_source_grounded(text: str, rule: str) -> None:
    brief = parse_brief(text, reference_year=2026)

    assert brief.rule(rule)
    assert all(quote_in_brief(c.source.quote, text) for c in brief.constraints)
    assert not brief.rule("no.negative_about_client")


def test_excluded_visual_deliverables_do_not_become_requirements_or_optional_blocks() -> None:
    text = """Pflichtinhalte

- Quelle und Datum nennen.

Thumbnail-Regeln

Ein Motiv, maximal drei Wörter, kräftiger Kontrast.
Optional können zwei Varianten entstehen. Sie müssen gegeneinander getestet werden.

Bild- und Footage-Plan mit Rechten

Fremde Bilder dürfen nicht ohne Rückfrage weiterverwendet werden.
Thumbnail: Ein Einkaufswagen muss vor einem roten Hintergrund stehen.
Optional kann Archivmaterial gezeigt werden.
"""
    brief = parse_brief(text, reference_year=2026)

    assert brief.rule("assets.permission_required")
    assert not any("Varianten" in c.text or "Hintergrund" in c.text for c in brief.constraints)
    assert len(brief.optional) == 1
    assert "Archivmaterial" in brief.optional[0].text
    assert all("thumbnail" not in c.text.casefold() for c in brief.constraints)


def test_wrapped_table_uncertainty_is_attached_to_the_product_source() -> None:
    text = """ Artikel         Art                Schuss    Kaliber    NEM      Einordnung
 Power Circus    Batterie           80        20 mm      488 g    Ähnelt einem anderen Produkt.
                                                                  Keine bestätigte
                                                                  Umlabelung.
"""
    brief = parse_brief(text, reference_year=2026)

    (uncertainty,) = brief.rule("facts.identity_unconfirmed")
    assert "Power Circus" in uncertainty.source.quote
    assert quote_in_brief(uncertainty.source.quote, text)
    facts = {f.key: f for f in brief.facts}
    assert facts["effekt_power-circus"].kind == "description"
    assert facts["effekt_power-circus"].value == "Batterie"
    assessment = facts["assessment_power-circus"]
    assert assessment.value == "Ähnelt einem anderen Produkt. Keine bestätigte Umlabelung."
    assert not assessment.verified
    assert assessment.source is not None
    assert assessment.source.ref == uncertainty.source.ref
    assert quote_in_brief(uncertainty.source.quote, assessment.source.quote)


def test_historical_product_descriptions_do_not_become_current_effects() -> None:
    text = """ Artikel         Händler     Umfang        Preis 2025/26
 Dragons Breath  Nord        160 Schuss    ca. 49,99 €
"""
    brief = parse_brief(text, reference_year=2026)

    assert not any(f.kind == "description" for f in brief.facts)
    assert not any(f.kind == "price" for f in brief.facts)
    assert brief.fact("historical_price_dragons-breath") is not None
