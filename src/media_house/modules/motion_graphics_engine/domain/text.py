"""Text measuring and fitting from the font's real advance widths (no browser guessing).

Lines are broken HERE and drawn with ``white-space: pre``, so the renderer never wraps on its
own: what QC measured is what is drawn. A word is never split; text that does not fit within
the look's line and character limits at or above the readability floor is refused.
"""

import re
from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.model import FaceMetrics

#: Safety margin on measured widths (kerning, hinting, stroke joins).
_MARGIN = 1.04
#: Size steps when shrinking to fit (fraction of the look size).
_SHRINK = 0.04
_WORD = re.compile(r"[\w€%]+", re.UNICODE)


def apply_case(text: str, case: str) -> str:
    """The look's capitalisation (``upper`` writes ß as SS, the identity's German rule)."""
    if case == "upper":
        return text.upper()
    if case == "sentence" and text:
        return text[0].upper() + text[1:]
    return text


def measure(
    text: str, metrics: FaceMetrics, size_px: float, tracking_em: float, space_px: int = 0
) -> float:
    """Drawn width; ``space_px`` is extra word spacing (outlined text keeps words apart)."""
    advance = sum(metrics.advance(c) for c in text)
    width = advance / metrics.units_per_em * size_px
    if len(text) > 1:
        width += tracking_em * size_px * (len(text) - 1)
    return width * _MARGIN + space_px * text.count(" ")


@dataclass(frozen=True, slots=True, kw_only=True)
class Fitted:
    size_px: int
    lines: tuple[tuple[str, ...], ...]  # words per line
    widths: tuple[int, ...]

    @property
    def width(self) -> int:
        return max(self.widths, default=0)


def wrap(
    words: list[str],
    metrics: FaceMetrics,
    size_px: int,
    tracking_em: float,
    max_width: int,
    max_chars: int,
    space_px: int = 0,
) -> list[list[str]] | None:
    """Greedy line breaking by measured width and the look's characters per line."""
    lines: list[list[str]] = []
    current: list[str] = []
    for word in words:
        if measure(word, metrics, size_px, tracking_em) > max_width:
            return None
        trial = " ".join([*current, word])
        fits = measure(trial, metrics, size_px, tracking_em, space_px) <= max_width
        if current and (not fits or len(trial) > max_chars):
            lines.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(current)
    return lines


def fit(
    text: str,
    *,
    metrics: FaceMetrics,
    size_px: int,
    min_px: int,
    tracking_em: float,
    max_width: int,
    max_lines: int,
    max_chars: int,
    explicit_lines: bool = False,
    space_px: int = 0,
    leading: float = 1.0,
    max_height: int | None = None,
    balance: bool = True,
) -> Fitted | None:
    """The largest size (look size down to ``min_px``) at which ``text`` fits, or ``None``.

    ``explicit_lines`` keeps the caller's line breaks (``\\n``) and only shrinks. ``balance``
    evens the lines out at the same line count ("Schusszahl / pro Euro", not "Schusszahl pro /
    Euro"); caption groups keep the greedy break (caption_engine reads them word by word).
    """
    size = size_px
    while size >= min_px:
        if explicit_lines:
            lines: list[list[str]] | None = [ln.split() for ln in text.split("\n") if ln.strip()]
            if lines is not None and any(
                measure(" ".join(ln), metrics, size, tracking_em, space_px) > max_width
                for ln in lines
            ):
                lines = None
        else:
            lines = wrap(text.split(), metrics, size, tracking_em, max_width, max_chars, space_px)
            if lines is not None and balance:
                lines = _balanced(lines, metrics, size, tracking_em, max_chars, space_px)
        allowed = max_lines
        if max_height is not None:
            allowed = min(max_lines, max_height // max(1, round(size * leading)))
        if lines is not None and len(lines) <= allowed:
            widths = tuple(round(measure(" ".join(ln), metrics, size, tracking_em)) for ln in lines)
            return Fitted(size_px=size, lines=tuple(tuple(ln) for ln in lines), widths=widths)
        step = max(1, round(size_px * _SHRINK))
        if size == min_px:
            break
        size = max(min_px, size - step)
    return None


def _balanced(
    lines: list[list[str]],
    metrics: FaceMetrics,
    size_px: int,
    tracking_em: float,
    max_chars: int,
    space_px: int,
) -> list[list[str]]:
    """The narrowest wrap that keeps the greedy line count (binary search on the width)."""
    if len(lines) < 2:
        return lines
    words = [w for line in lines for w in line]
    low = max(measure(w, metrics, size_px, tracking_em) for w in words)
    high = max(measure(" ".join(ln), metrics, size_px, tracking_em, space_px) for ln in lines)
    best = lines
    for _ in range(12):
        if high - low < 1:
            break
        mid = (low + high) / 2
        trial = wrap(words, metrics, size_px, tracking_em, int(mid) + 1, max_chars, space_px)
        if trial is not None and len(trial) <= len(lines):
            best, high = trial, mid
        else:
            low = mid
    return best


def key(word: str) -> str:
    """Comparable form of a word (highlight matching): letters/digits only, case-folded."""
    return "".join(_WORD.findall(word)).casefold()


def german_number(value: Rational, max_decimals: int = 2) -> tuple[str, bool]:
    """``value`` with a decimal comma and thousands dots; ``exact`` is False when rounded."""
    exact_value = Fraction(value.numerator, value.denominator)
    for decimals in range(max_decimals + 1):
        scaled = exact_value * 10**decimals
        if scaled.denominator == 1:
            return _format(int(scaled), decimals), True
    scaled = round(exact_value * 10**max_decimals)
    return _format(scaled, max_decimals), False


def _format(scaled: int, decimals: int) -> str:
    whole, rest = divmod(scaled, 10**decimals) if decimals else (scaled, 0)
    text = f"{whole:,}".replace(",", ".")
    return f"{text},{rest:0{decimals}d}" if decimals else text
