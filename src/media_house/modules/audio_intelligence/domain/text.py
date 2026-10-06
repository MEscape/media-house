"""Word text normalisation. The canonical transcript keeps ``raw_word``; this derives from it."""

import re
import unicodedata
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizationOptions:
    lowercase: bool = False
    strip_punctuation: bool = True
    collapse_whitespace: bool = True


DEFAULT_NORMALIZATION = NormalizationOptions()


def _is_edge_noise(char: str) -> bool:
    return unicodedata.category(char)[0] in {"P", "S"}


def normalize_text(raw: str, options: NormalizationOptions = DEFAULT_NORMALIZATION) -> str:
    """``"Feuerwerk,"`` -> ``"Feuerwerk"``. Inner punctuation (``don't``, ``E-Mail``) is kept."""
    text = raw
    if options.collapse_whitespace:
        text = _WHITESPACE.sub(" ", text).strip()
    if options.strip_punctuation:
        start, end = 0, len(text)
        while start < end and _is_edge_noise(text[start]):
            start += 1
        while end > start and _is_edge_noise(text[end - 1]):
            end -= 1
        text = text[start:end]
    if options.lowercase:
        text = text.casefold()
    return text
