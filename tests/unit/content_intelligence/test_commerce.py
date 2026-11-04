"""Commerce facts (prices, ranks, firework specs) read from German text, and the rank cue."""

from fractions import Fraction

import pytest

from media_house.modules.content_intelligence.application.contracts import (
    CommerceKind,
    commerce_facts,
    designations,
)
from tests.support.ci_texts import analysis_of, lines_to_transcript

K = CommerceKind


@pytest.mark.parametrize(
    ("text", "kind", "value", "unit", "verbatim"),
    [
        ("kostet 15,99 EUR", K.PRICE, Fraction(1599, 100), "EUR", "15,99 EUR"),
        ("nur 2,49€", K.PRICE, Fraction(249, 100), "EUR", "2,49€"),
        ("für 5.99€", K.PRICE, Fraction(599, 100), "EUR", "5.99€"),
        ("kostet 19,99 Euro", K.PRICE, Fraction(1999, 100), "EUR", "19,99 Euro"),
        ("für 150€ bei Lidl", K.PRICE, Fraction(150), "EUR", "150€"),
        ("satte 1.299,00 €", K.PRICE, Fraction(1299), "EUR", "1.299,00 €"),
        ("Platz 1 ist klar", K.RANK, Fraction(1), "", "Platz 1"),
        ("und auf Platz 10 landet", K.RANK, Fraction(10), "", "auf Platz 10"),
        ("auf Platz zehn landet", K.RANK, Fraction(10), "", "auf Platz zehn"),
        # E2E (owner's ranking video): the ordinal before the word
        ("jetzt schon auf dem achten Platz eines", K.RANK, Fraction(8), "", "dem achten Platz"),
        ("hat 25 Schuss", K.SHOTS, Fraction(25), "", "25 Schuss"),
        ("mit 100 Schüssen", K.SHOTS, Fraction(100), "", "100 Schüssen"),
        ("Höhe 30 m", K.HEIGHT, Fraction(30), "m", "Höhe 30 m"),
        ("eine Steighöhe von bis zu 40 Metern", K.HEIGHT, Fraction(40), "m", None),
        ("Brenndauer 40 Sekunden", K.DURATION, Fraction(40), "s", "Brenndauer 40 Sekunden"),
        ("Brennzeit: ca. 22,5 s", K.DURATION, Fraction(45, 2), "s", None),
        ("Kaliber 30 mm", K.CALIBER, Fraction(30), "mm", "Kaliber 30 mm"),
        ("NEM 500 g", K.WEIGHT, Fraction(500), "g", "NEM 500 g"),
        ("Gewicht 1,2 kg", K.WEIGHT, Fraction(6, 5), "kg", None),
        ("mit 5 Effekten", K.EFFECTS, Fraction(5), "", "5 Effekten"),
    ],
)
def test_one_fact_is_read_exactly_and_verbatim(
    text: str, kind: CommerceKind, value: Fraction, unit: str, verbatim: str | None
) -> None:
    (fact,) = [f for f in commerce_facts(text, "de") if f.kind is kind]

    assert (fact.value, fact.unit) == (value, unit)
    assert text[fact.start : fact.end] == fact.text
    if verbatim is not None:
        assert fact.text == verbatim


@pytest.mark.parametrize(
    "text",
    [
        "neunzehn Euro",  # spelled-out amounts are not read (documented limit)
        "neunzehn neunundneunzig",
        "das kostet 15,99",  # no currency: not a price
        "in 40 Sekunden zünden wir",  # no spec word: not a burn time
        "Nettoexplosivmasse",
    ],
)
def test_nothing_is_invented(text: str) -> None:
    assert [f for f in commerce_facts(text, "de") if f.kind is not K.RETAILER] == []


def test_retailers_products_and_umlauts_are_kept() -> None:
    facts = commerce_facts("Die Größe bei LIDL: Pixel 9 für 4,99 € an Silvester 2025", "de")

    by_kind = {f.kind: f for f in facts}
    assert by_kind[K.RETAILER].name == "Lidl" and by_kind[K.RETAILER].text == "LIDL"
    assert by_kind[K.PRODUCT].name == "pixel 9"
    assert by_kind[K.PRICE].value == Fraction(499, 100)
    assert [f.kind for f in facts].count(K.PRODUCT) == 1  # "Silvester 2025" is a date
    assert [f.start for f in facts] == sorted(f.start for f in facts)
    assert commerce_facts("netto 500 g", "de") == ()


def test_ranks_and_specs_need_a_known_language_but_prices_do_not() -> None:
    assert [f.kind for f in commerce_facts("Platz 3 kostet 9,99 €", "sv")] == [K.PRICE]
    assert [f.kind for f in commerce_facts("number 3 costs 9,99 €", "en")] == [K.RANK, K.PRICE]


def test_a_rank_or_spec_word_is_not_a_product_designation() -> None:
    assert designations("Platz 10 ist die Gargoyle von Weco") == ()
    assert designations("Brenndauer 40 Sekunden, Kaliber 30 mm") == ()


def test_each_rank_starts_its_own_segment_and_is_a_list_graphic() -> None:
    ranking = (
        "Willkommen zu meinen Top drei Batterien von Lidl.",
        "Heute zeige ich euch die besten Batterien aus dem Prospekt.",
        "Alle drei habe ich selbst getestet.",
        "Auf Platz 3 haben wir die Gargoyle Batterie von Weco.",
        "Die Batterie hat 25 Schuss und kostet 15,99 Euro.",
        "Die Effekte sind goldene Palmen mit Knistern am Himmel.",
        "Auf Platz 2 kommt der Tip Tap Tube mit lauten Bomben.",
        "Der Tube schießt sehr hoch und knallt richtig laut.",
        "Die Bomben sind laut und hell mit roten Sternen.",
        "Auf Platz 1 steht die große Verbundbatterie für Silvester.",
        "Diese Verbundbatterie brennt vierzig Sekunden lang im Fächer.",
        "Der Fächer aus Kometen füllt den ganzen Himmel.",
    )
    a = analysis_of(lines_to_transcript(ranking, language="de"))

    starts = [a.unit(s.unit_ids[0]).text for s in a.segments]  # type: ignore[union-attr]
    assert [s for s in starts if "Platz" in s] == [ranking[3], ranking[6], ranking[9]]
    lists = [g.concept for s in a.segments for g in s.graphics if g.category == "list"]
    assert len(lists) == 3
