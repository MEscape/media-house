"""German numbers and text, exactly: prices and ratios are computed here, never by a model.

Values are ``Fraction`` (exact); text is formatted only at the edge. Nothing here rounds before
the last step, so a sum of prices is the exact sum.
"""

import re
import unicodedata
from fractions import Fraction

_NUMBER = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[.,]\d+)?")


def fold(text: str) -> str:
    """Lowercase ASCII text for matching: umlauts folded, punctuation spaced, one space."""
    folded = unicodedata.normalize("NFKD", text.casefold().replace("ß", "ss"))
    ascii_text = folded.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9#@%€]+", " ", ascii_text).strip()


def slugify(text: str) -> str:
    """``Würfel & Co`` -> ``wuerfel-co``: lowercase, hyphens, no umlauts or spaces."""
    swapped = (
        text.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    )
    folded = unicodedata.normalize("NFKD", swapped).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def parse_number(text: str) -> Fraction | None:
    """``15,99`` and ``1.299,50`` as German, ``15.99`` as a decimal point; None when no number."""
    match = _NUMBER.search(text.strip())
    if match is None:
        return None
    raw = match.group(0)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    return Fraction(raw)


def format_number(value: Fraction, decimals: int = 2) -> str:
    """The German form with a decimal comma and thousands points: ``1.299,50``."""
    scaled = _round_half_up(value * 10**decimals)
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled)).rjust(decimals + 1, "0")
    whole, frac = (digits[:-decimals], digits[-decimals:]) if decimals else (digits, "")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{sign}{grouped}" + (f",{frac}" if decimals else "")


def format_price(value: Fraction) -> str:
    return f"{format_number(value, 2)} €"


def _round_half_up(value: Fraction) -> int:
    floor = value.numerator // value.denominator
    return floor + 1 if value - floor >= Fraction(1, 2) else floor


def number_tokens(text: str) -> tuple[str, ...]:
    """Every number as written, in order."""
    return tuple(m.group(0) for m in _NUMBER.finditer(text))
