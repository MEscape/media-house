"""Small, pure text rules for German captions (no recognition: facts come from content).

Only what segmentation needs to break lines well: which words must not end a line, which words
start a new phrase, which tokens are units that stay with their number, and how a look's case
changes the length (``ß`` becomes ``SS`` in capitals).
"""

import re
import unicodedata
from typing import Final


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


#: Words a caption line must not end on (articles, prepositions, conjunctions, auxiliaries).
DANGLING: Final = _words(
    """der die das den dem des ein eine einen einem einer eines kein keine keinen
    und oder aber sondern denn weil dass wenn als ob wie mit für von vom zu zum zur im in am an
    auf aus bei beim nach über unter vor um durch gegen ohne bis seit ab ist sind war hat haben
    wird werden kann können muss müssen ich du er sie es wir ihr man mein dein sein unser
    so sehr noch nur auch schon ganz the a an and or of to"""
)
#: Words that open a phrase: breaking BEFORE them reads naturally.
PHRASE_OPENERS: Final = _words(
    """und oder aber sondern denn weil dass wenn als ob wie damit obwohl während also dann
    deshalb trotzdem sowie"""
)
#: Words never emphasised (too common to carry meaning on their own).
STOPWORDS: Final = DANGLING | _words(
    "nicht ja nein hier da dort jetzt mal halt eben dieser diese dieses was wer"
)
#: Unit tokens that stay with the number before them ("25 Schuss", "19,99 €", "30 %").
UNITS: Final = _words(
    """€ euro eur cent % prozent mm cm m meter km kg g gramm s sek sekunden sekunde min
    minuten schuss stück stk x mal effekte"""
)
NEGATIONS: Final = _words("nicht kein keine keinen keiner nie niemals ohne kaum")
_DASHES: Final = (",", ";", ":", "-", chr(0x2013), chr(0x2014))  # en and em dash
_EDGE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_DIGIT = re.compile(r"\d")


def bare(word: str) -> str:
    """Lower-case word without surrounding punctuation (``"Preis,"`` -> ``"preis"``)."""
    return _EDGE.sub("", word).casefold()


def has_digit(word: str) -> bool:
    return bool(_DIGIT.search(word))


def is_unit(word: str) -> bool:
    w = word.strip().casefold().rstrip(".,;:!?")
    return w in UNITS or w == "€"


def ends_phrase(word: str) -> bool:
    """A comma, colon, semicolon or dash ends a phrase inside a sentence."""
    return word.rstrip().endswith(_DASHES)


def ends_sentence(word: str) -> bool:
    return word.rstrip().rstrip("\"'»«“”").endswith((".", "!", "?", "…"))


def shown(text: str, case: str) -> str:
    """The look's capitalisation (``upper`` writes ß as SS, the identity's German rule)."""
    if case == "upper":
        return text.upper()
    if case == "sentence" and text:
        return text[0].upper() + text[1:]
    return text


def strip_full_stop(word: str) -> str:
    """Drop ONE final full stop (``"Preis."`` -> ``"Preis"``); ellipses, ``!`` and ``?`` stay."""
    if word.endswith(".") and not word.endswith("..") and len(word) > 1:
        return word[:-1]
    return word


def same_text(a: str, b: str) -> bool:
    """Equal after Unicode normalisation (umlauts as one or two code points are the same)."""
    return unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b)
