"""Words the compliance checks look for. Plain data: extend a list, no logic changes.

Retailers are those the channel itself covers (``channel-evidence.json``); the pyrotechnics
brands and illegal-article terms are common German market vocabulary. A project may add its own.
"""

from typing import Final

#: Retailers and shops a video must not compare the client with (the client itself is removed
#: per project).
COMPETITORS: Final[frozenset[str]] = frozenset(
    {
        "aldi",
        "aldi nord",
        "aldi süd",
        "netto",
        "kaufland",
        "penny",
        "norma",
        "edeka",
        "rewe",
        "real",
        "globus",
        "jawoll",
        "thomas philipps",
        "tedi",
        "action",
        "rossmann",
        "dm",
        "obi",
        "bauhaus",
        "hornbach",
        "toom",
        "amazon",
        "ebay",
        "otto",
    }
)

#: Pyrotechnics manufacturers and labels: a conspicuous third-party brand in picture or speech.
PYRO_BRANDS: Final[frozenset[str]] = frozenset(
    {
        "weco",
        "nico",
        "funke",
        "comet",
        "lesli",
        "jorge",
        "klasek",
        "zena",
        "xplode",
        "blackboxx",
        "pyrostar",
        "bugano",
        "flash",
        "sunsets",
    }
)

#: Articles and wording of illegal or unlicensed pyrotechnics.
ILLEGAL_PYRO_TERMS: Final[tuple[str, ...]] = (
    "polenböller",
    "polen böller",
    "polen-böller",
    "cobra",
    "kugelbombe",
    "kategorie f4",
    "f4",
    "nicht zugelassen",
    "ohne zulassung",
    "illegal",
    "ohne bam",
    "ohne ce",
    "tschechische böller",
)

#: Words that put someone "on screen": a confidential item next to one of them is being shown.
SHOWING_VERBS: Final[tuple[str, ...]] = (
    "zeig",
    "einblend",
    "im bild",
    "zu sehen",
    "vorstell",
    "filmen",
    "aufnehm",
    "screenshot",
    "gezeigt",
    "einspiel",
)

NEGATIVE_WORDS: Final[tuple[str, ...]] = (
    "schlecht",
    "mies",
    "enttäusch",
    "müll",
    "schrott",
    "abzocke",
    "unterirdisch",
    "überteuert",
    "billigkram",
    "ramsch",
    "nicht empfehlenswert",
    "nicht gut",
    "lahm",
    "langweilig",
)

#: Private-data patterns on screen recordings (what an OCR of a screen can show).
PRIVATE_DATA_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("email", r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    ("iban", r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){3,}\b"),
    ("card", r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
    ("phone", r"(?<!\d)(?:\+49|0)\s?\d{2,4}[\s/-]?\d{5,9}(?!\d)"),
    ("address", r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+\b"),
    ("login", r"(?i)\b(passwort|password|benutzername|kundennummer|bestellnummer)\b"),
)

#: Sites of stock footage: a download from one needs a licence proof.
STOCK_MARKERS: Final[tuple[str, ...]] = (
    "pexels",
    "pixabay",
    "shutterstock",
    "istock",
    "videvo",
    "mixkit",
    "storyblocks",
    "adobestock",
    "adobe stock",
    "envato",
    "videohive",
    "getty",
    "depositphotos",
    "coverr",
    "vecteezy",
    "freepik",
    "dissolve",
    "artgrid",
    "motionarray",
    "videezy",
)
