"""Technik-Slots: the domain details only the creator knows (caliber, effect type ...).

A sentence that needs an unfilled slot is dropped by the script reader, so a script never says
something nobody provided. Nothing here invents a value.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from media_house.modules.production_project.domain.brief import Fact, Source
from media_house.modules.production_project.domain.facts import ProductRow
from media_house.shared.errors import InvariantViolation

#: The details asked per product: (key stem, German label, unit, hint). The stem is also the
#: fact key stem, so ``spec_ratio_formulas`` finds ``shots_<subject>``, ``effektmasse_<subject>``.
PRODUCT_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("kaliber", "Kaliber", "mm", "Steht meist auf der Verpackung."),
    ("shots", "Anzahl Schuss", "Schuss", "Wie viele Effekte hat der Artikel?"),
    ("effekt", "Effekt", "", "Was passiert am Himmel? Farbe, Form, Höhe."),
    ("effektmasse", "Effektmasse (optional)", "g", "Nur wenn die Packung sie nennt."),
    ("hoehe", "Steighöhe (optional)", "m", "Nur wenn angegeben oder gemessen."),
)
_OPTIONAL_STEMS = frozenset({"effektmasse", "hoehe"})
_NON_NAME = re.compile(r"[^A-Z0-9]+")


@dataclass(frozen=True, slots=True)
class Slot:
    key: str
    label: str
    unit: str
    hint: str
    subject: str
    #: The stem of the fact it becomes (``kaliber``), "" for a free slot.
    stem: str
    value: str = ""
    optional: bool = False

    @property
    def name(self) -> str:
        """The slot as written in a script: ``{KALIBER_NOVA}``."""
        return "{" + self.key + "}"

    @property
    def filled(self) -> bool:
        return bool(self.value.strip())


def slot_key(stem: str, subject: str) -> str:
    """``kaliber`` + ``feuerwerksbatterie-nova`` -> ``KALIBER_FEUERWERKSBATTERIE_NOVA``."""
    raw = _NON_NAME.sub("_", f"{stem}_{subject}".upper()).strip("_")
    return raw if raw[:1].isalpha() else f"S_{raw}"


def standard_slots(rows: Sequence[ProductRow], facts: Sequence[Fact] = ()) -> tuple[Slot, ...]:
    """The form for the products: every detail the model must not invent."""
    known = {f.key for f in facts if f.value.strip()}
    return tuple(
        Slot(
            slot_key(stem, row.subject),
            f"{label} ({row.name})",
            unit,
            hint,
            row.subject,
            stem,
            optional=stem in _OPTIONAL_STEMS,
        )
        for row in rows
        for stem, label, unit, hint in PRODUCT_SPECS
        if f"{stem}_{row.subject}" not in known
    )


def fill(slots: Sequence[Slot], key: str, value: str) -> tuple[Slot, ...]:
    if not any(s.key == key for s in slots):
        raise InvariantViolation(f"No slot {key}", user_message="Dieses Feld gibt es nicht.")
    return tuple(replace(s, value=value.strip()) if s.key == key else s for s in slots)


def slot_facts(slots: Sequence[Slot]) -> tuple[Fact, ...]:
    """Filled slots as facts (unit and subject kept, never verified: the creator's own word)."""
    return tuple(
        Fact(
            key=f"{s.stem}_{s.subject}" if s.stem else s.key.lower(),
            label=s.label,
            value=s.value.strip(),
            unit=s.unit,
            source=Source("Technik-Slot", f"{s.label}: {s.value.strip()}"),
            verified=False,
            kind="spec",
            subject=s.subject,
        )
        for s in slots
        if s.filled
    )


def fill_values(slots: Sequence[Slot]) -> dict[str, str]:
    """``{KEY: value}`` for the script reader: only the filled slots."""
    return {s.key: s.value.strip() for s in slots if s.filled}


_FREE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def ensure_slots(slots: Sequence[Slot], script_text: str) -> tuple[Slot, ...]:
    """Every ``{SLOT}`` a script uses is a field of the form: free slots (``{AUFHAENGER}``) are
    added, filled ones are kept as they are."""
    known = {s.key for s in slots}
    extra = [
        Slot(key, key.replace("_", " ").capitalize(), "", "", "", "")
        for key in dict.fromkeys(_FREE.findall(script_text))
        if key not in known
    ]
    return (*slots, *extra)
