"""Commerce facts read from a text: prices, ranks, firework specs, retailers and product names.

A pure helper for consumers that print a fact (a price tag, a rank card, a spec card) and must
print it from an exact value, never from a reworded transcript string. Every fact is FOUND in the
text: ``text`` is the verbatim match and ``start``/``end`` its character offsets. Nothing is
inferred, completed or guessed.

* ``price``: an amount with a currency next to it ("15,99 €", "2,49€", "15,99 EUR", "19 Euro",
  "€ 5"). ``value`` is the exact amount in euros (``Fraction(1599, 100)``), ``unit`` is ``EUR``.
  A dot before two digits is read as a decimal point ("5.99€" is 5.99), before three digits as a
  thousands separator ("1.299 €"). A number without a currency is not a price.
* ``rank``: "Platz 3", "auf Platz zehn" (German), "number 3" (English). ``value`` is the rank.
* ``shots`` ("25 Schuss"), ``effects`` ("5 Effekte"): counts.
* ``height`` (m), ``duration`` (s), ``caliber`` (mm), ``weight`` (g or kg): only with the spec
  word in front ("Höhe 30 m", "Steighöhe bis 40 Meter", "Brenndauer 40 Sekunden", "Kaliber 30 mm",
  "NEM 500 g"). A bare "40 Sekunden" is not a spec: it may be anything.
* ``retailer``: a retailer the channel names (Lidl, Aldi, Netto ...); ``name`` is the canonical
  spelling. ``product``: a product designation as ``designations`` finds it ("Pixel 9").

Limits, by design: spelled-out amounts ("neunzehn Euro", "neunzehn neunundneunzig") are not read;
a product name without a number ("Gargoyle WECO") is not a designation; specs and ranks are
matched for German, and ranks also for English.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Final

from media_house.modules.content_intelligence.domain.references import designations


class CommerceKind(StrEnum):
    PRICE = "price"
    RANK = "rank"
    SHOTS = "shots"
    EFFECTS = "effects"
    HEIGHT = "height"
    DURATION = "duration"
    CALIBER = "caliber"
    WEIGHT = "weight"
    RETAILER = "retailer"
    PRODUCT = "product"


@dataclass(frozen=True, slots=True, kw_only=True)
class CommerceFact:
    kind: CommerceKind
    #: The verbatim match.
    text: str
    start: int
    end: int
    #: The exact number (euros for a price); ``None`` for a retailer or a product.
    value: Fraction | None = None
    unit: str = ""
    #: The canonical name of a retailer or the designation key of a product.
    name: str = ""


_AMOUNT = r"(?P<int>\d{1,3}(?:\.\d{3})+|\d+)(?:[,.](?P<dec>\d{2})(?!\d)|,-)?"
_CURRENCY = r"(?:€|eur\b|euro\b)"
_PRICES: Final = (
    re.compile(rf"(?<![\d.,]){_AMOUNT}\s?{_CURRENCY}", re.IGNORECASE),
    re.compile(rf"(?:€|eur\b)\s?{_AMOUNT}(?![\d,.]\d)", re.IGNORECASE),
    # Spoken without the currency ("die teuerste hier für 134,99."): only with cents, only after
    # a price word, never before a unit.
    re.compile(
        r"\b(?:für|kostet|kosten)\s+(?:nur\s+)?(?P<int>\d{1,4}),(?P<dec>\d{2})(?![\d,.]\d)"
        r"(?!\s?(?:%|prozent|kg|kilo|gramm|g\b|m\b|meter|sek|mm|schuss))",
        re.IGNORECASE,
    ),
)
_NUM = r"(?P<num>\d+(?:[,.]\d+)?)"
_WORD_NUMBERS: Final = {
    "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}  # fmt: skip
#: "auf dem achten Platz": the ordinal stands before the word.
_ORDINALS: Final = {
    "ersten": 1, "zweiten": 2, "dritten": 3, "vierten": 4, "fünften": 5, "sechsten": 6,
    "siebten": 7, "siebenten": 7, "achten": 8, "neunten": 9, "zehnten": 10,
}  # fmt: skip
_RANKS: Final = {
    "de": re.compile(
        rf"\b(?:(?:auf\s+)?platz\s+(?P<num>\d{{1,3}}|{'|'.join(_WORD_NUMBERS)})"
        rf"|(?:dem|den)\s+(?P<ord>{'|'.join(_ORDINALS)})\s+platz)\b",
        re.IGNORECASE,
    ),
    "en": re.compile(r"\b(?:number|no\.)\s?(?P<num>\d{1,3})\b", re.IGNORECASE),
}
#: (kind, pattern, unit); the spec word must precede the number.
_SPECS: Final = {
    "de": (
        (
            CommerceKind.SHOTS,
            re.compile(rf"\b{_NUM}\s?(?:schuss|schüsse|schüssen|schuß)\b", re.I),
            "",
        ),
        (CommerceKind.EFFECTS, re.compile(rf"\b{_NUM}\s?effekten?\b", re.I), ""),
        (
            CommerceKind.HEIGHT,
            re.compile(rf"\b(?:steig|effekt)?höhe\b\D{{0,20}}?{_NUM}\s?(?:m|meter|metern)\b", re.I),
            "m",
        ),
        (
            CommerceKind.DURATION,
            re.compile(
                rf"\b(?:brenndauer|brennzeit|effektdauer)\b\D{{0,20}}?{_NUM}\s?"
                r"(?:s|sek\.?|sekunden)(?!\w)",
                re.I,
            ),
            "s",
        ),
        (CommerceKind.CALIBER, re.compile(rf"\bkaliber\b\D{{0,15}}?{_NUM}\s?mm\b", re.I), "mm"),
        (
            CommerceKind.WEIGHT,
            re.compile(
                rf"\b(?:nem|netto-?explosivmasse|gewicht)\b\D{{0,20}}?{_NUM}\s?(?P<unit>kg|g|gramm)\b",
                re.I,
            ),
            "g",
        ),
        (  # "fast 1,5 Kilogramm NEM", never the per-shot "6 Gramm NEM pro Schuss"
            CommerceKind.WEIGHT,
            re.compile(
                rf"\b{_NUM}\s?(?P<unit>kg|kilogramm|kilo|g|gramm)\s+(?:nem|netto-?explosivmasse)\b"
                r"(?!\s+pro\s+schuss)",
                re.I,
            ),
            "g",
        ),
    ),
}
_YEAR: Final = re.compile(r"(?:19|20)\d\d")
#: Retailers named on the channel; "Netto" before a number or a hyphen is the word "net".
_RETAILERS: Final = re.compile(
    r"\b(?P<name>lidl|aldi(?:\s+(?:nord|süd))?|netto(?!\s*-|\s*\d|\w)|kaufland|penny|norma|edeka|"
    r"rewe|jawoll|thomas\s+philipps|pyroland|pyro\s+team\s+store)\b",
    re.IGNORECASE,
)


def commerce_facts(text: str, language: str | None = "de") -> tuple[CommerceFact, ...]:
    """Every commerce fact in ``text``, in reading order. ``language`` picks the rank and spec
    words (``de`` or ``en``); prices and retailers are read in any language."""
    family = (language or "").split("-")[0].casefold()
    found: list[CommerceFact] = []
    taken: list[tuple[int, int]] = []
    for pattern in _PRICES:
        for m in pattern.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append(m.span())
            euros = Fraction(m["int"].replace(".", "")) + Fraction(int(m["dec"] or 0), 100)
            found.append(_fact(CommerceKind.PRICE, m, euros, "EUR"))
    rank = _RANKS.get(family)
    if rank is not None:
        for m in rank.finditer(text):
            if m["num"] is None:
                found.append(_fact(CommerceKind.RANK, m, Fraction(_ORDINALS[m["ord"].casefold()])))
                continue
            n = m["num"].casefold()
            found.append(_fact(CommerceKind.RANK, m, Fraction(_WORD_NUMBERS.get(n) or int(n))))
    for kind, pattern, unit in _SPECS.get(family, ()):
        for m in pattern.finditer(text):
            value = Fraction(m["num"].replace(",", "."))
            kilos = kind is CommerceKind.WEIGHT and m["unit"].casefold().startswith("k")
            found.append(_fact(kind, m, value, "kg" if kilos else unit))
    for m in _RETAILERS.finditer(text):
        name = " ".join(w.capitalize() for w in m["name"].split())
        found.append(_fact(CommerceKind.RETAILER, m, None, name=name))
    for d in designations(text):
        place = re.search(r"\s+".join(map(re.escape, d.text.split())), text)
        if place is not None and not _YEAR.fullmatch(d.value):  # "Silvester 2025" is a date
            found.append(_fact(CommerceKind.PRODUCT, place, None, name=d.key))
    return tuple(sorted(found, key=lambda f: (f.start, f.end, f.kind.value)))


def _fact(
    kind: CommerceKind,
    m: re.Match[str],
    value: Fraction | None,
    unit: str = "",
    name: str = "",
) -> CommerceFact:
    return CommerceFact(
        kind=kind, text=m.group(0), start=m.start(), end=m.end(), value=value, unit=unit, name=name
    )
