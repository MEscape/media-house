"""The fact table: what the video may state, where it comes from, and what code derived.

Every derived number (a sum, a ratio, a difference) is computed here from verified or stated
facts with exact fractions. A model may ask for a formula; it never writes the result.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.modules.production_project.domain.brief import Fact, Source
from media_house.modules.production_project.domain.numbers import (
    format_number,
    parse_number,
    slugify,
)
from media_house.shared.errors import InvariantViolation

_FACT_REF = re.compile(r"\{\{fact:([a-z0-9_\-]+)\}\}")

OPERATIONS = ("sum", "difference", "ratio", "product", "min", "max", "count", "mean")


@dataclass(frozen=True, slots=True)
class Formula:
    """How a derived fact is computed from other facts (checked, then run by code)."""

    key: str
    label: str
    operation: str
    inputs: tuple[str, ...]
    unit: str
    decimals: int
    approximate: bool = False
    kind: str = "derived"
    subject: str = ""

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise InvariantViolation(f"Unknown operation {self.operation!r}")
        if not self.inputs:
            raise InvariantViolation("A formula needs inputs")


def number_of(fact: Fact) -> Fraction | None:
    """The numeric value of a fact (a date or a name has none)."""
    if fact.kind in {"date", "url", "product"}:
        return None
    return parse_number(fact.value)


def derive(facts: Sequence[Fact], formulas: Iterable[Formula]) -> tuple[Fact, ...]:
    """The base facts plus every formula whose inputs exist and are numeric.

    A formula whose input is missing is skipped (reported by ``unresolved``), never guessed.
    Derived facts are verified only when every input is, and say so in ``derived_from``.
    """
    known = {f.key: f for f in facts}
    out = list(facts)
    for formula in formulas:
        value = _compute(formula, known)
        if value is None:
            continue
        inputs = [known[k] for k in formula.inputs]
        fact = Fact(
            key=formula.key,
            label=formula.label,
            value=format_number(value, formula.decimals),
            unit=formula.unit,
            source=Source("berechnet", f"{formula.operation}({', '.join(formula.inputs)})"),
            verified=all(i.verified for i in inputs),
            derived_from=formula.inputs,
            approximate=formula.approximate,
            kind=formula.kind,
            subject=formula.subject,
        )
        known[fact.key] = fact
        out = [f for f in out if f.key != fact.key] + [fact]
    return tuple(out)


def unresolved(facts: Sequence[Fact], formulas: Iterable[Formula]) -> tuple[Formula, ...]:
    known = {f.key: f for f in facts}
    return tuple(f for f in formulas if _compute(f, known) is None)


def _compute(formula: Formula, known: dict[str, Fact]) -> Fraction | None:
    if any(k not in known for k in formula.inputs):
        return None
    if formula.operation == "count":
        return Fraction(len(formula.inputs))
    numbers = [number_of(known[k]) for k in formula.inputs]
    values = [n for n in numbers if n is not None]
    if len(values) != len(numbers):
        return None
    match formula.operation:
        case "sum":
            return sum(values, Fraction(0))
        case "difference":
            return values[0] - sum(values[1:], Fraction(0))
        case "ratio":
            return None if len(values) != 2 or values[1] == 0 else values[0] / values[1]
        case "product":
            result = Fraction(1)
            for v in values:
                result *= v
            return result
        case "min":
            return min(values)
        case "max":
            return max(values)
        case "mean":
            return sum(values, Fraction(0)) / len(values)
        case _:
            return Fraction(len(values))


@dataclass(frozen=True, slots=True)
class ProductRow:
    subject: str
    name: str
    price: Fraction | None


def products(facts: Sequence[Fact]) -> tuple[ProductRow, ...]:
    """Current products in brief order, including those whose price is not available yet."""
    prices = {f.subject: f for f in facts if f.kind == "price"}
    rows: list[ProductRow] = []
    for fact in facts:
        if fact.kind != "product":
            continue
        price = prices.get(fact.subject)
        value = parse_number(price.value) if price else None
        rows.append(ProductRow(fact.subject, fact.value, value))
    return tuple(rows)


def standard_formulas(facts: Sequence[Fact]) -> tuple[Formula, ...]:
    """The computations every product campaign wants: totals, extremes, spreads."""
    rows = products(facts)
    if not rows:
        return ()
    keys = tuple(f"price_{r.subject}" for r in rows)
    complete = all(r.price is not None for r in rows)
    count = Formula(
        "product_count",
        "Anzahl der Produkte",
        "count",
        keys if complete else tuple(f"product_{r.subject}" for r in rows),
        "",
        0,
    )
    if len(rows) < 2 or not complete:
        return (count,)
    cheapest = min(rows, key=lambda r: r.price or Fraction(0))
    dearest = max(rows, key=lambda r: r.price or Fraction(0))
    return (
        Formula("price_total", "Summe aller Preise", "sum", keys, "€", 2),
        Formula("price_min", "Günstigster Preis", "min", keys, "€", 2),
        Formula("price_max", "Höchster Preis", "max", keys, "€", 2),
        Formula(
            "price_spread",
            "Preisabstand zwischen dem teuersten und günstigsten Artikel",
            "difference",
            ("price_max", "price_min"),
            "€",
            2,
        ),
        count,
        *(
            Formula(
                f"price_vs_cheapest_{r.subject}",
                f"Aufpreis {r.name} gegenüber {cheapest.name}",
                "difference",
                (f"price_{r.subject}", f"price_{cheapest.subject}"),
                "€",
                2,
            )
            for r in rows
            if r.subject != cheapest.subject and r.subject != dearest.subject
        ),
    )


def spec_ratio_formulas(facts: Sequence[Fact]) -> tuple[Formula, ...]:
    """Ratios from technical facts the creator filled in (effect mass per shot, price per shot):
    approximations, said as such."""
    by_subject: dict[str, dict[str, Fact]] = {}
    for fact in facts:
        if fact.subject and fact.kind == "spec":
            by_subject.setdefault(fact.subject, {})[fact.key.split("_")[0]] = fact
    out: list[Formula] = []
    for subject, specs in sorted(by_subject.items()):
        shots, mass = specs.get("shots"), specs.get("effektmasse")
        if shots and mass:
            out.append(
                Formula(
                    f"mass_per_shot_{subject}",
                    f"Effektmasse pro Schuss ({subject})",
                    "ratio",
                    (mass.key, shots.key),
                    "g",
                    1,
                    approximate=True,
                    subject=subject,
                )
            )
        price = next((f for f in facts if f.key == f"price_{subject}"), None)
        if shots and price:
            out.append(
                Formula(
                    f"price_per_shot_{subject}",
                    f"Preis pro Schuss ({subject})",
                    "ratio",
                    (price.key, shots.key),
                    "€",
                    2,
                    approximate=True,
                    subject=subject,
                )
            )
    return tuple(out)


def verify(facts: Sequence[Fact], key: str, source: Source) -> tuple[Fact, ...]:
    """Mark a fact verified against a live source (the source is stored with it)."""
    return tuple(replace(f, verified=True, source=source) if f.key == key else f for f in facts)


def with_value(facts: Sequence[Fact], key: str, value: str) -> tuple[Fact, ...]:
    """A person changes a fact's value: it is unverified again, and so is everything derived."""
    changed = {key}
    out = list(facts)
    for i, f in enumerate(out):
        if f.key == key:
            out[i] = replace(f, value=value, verified=False)
    growing = True
    while growing:
        growing = False
        for i, f in enumerate(out):
            if f.key not in changed and changed & set(f.derived_from):
                out[i] = replace(f, verified=False)
                changed.add(f.key)
                growing = True
    return tuple(out)


def dependants(facts: Sequence[Fact], key: str) -> tuple[str, ...]:
    """The keys of every derived fact that (transitively) rests on ``key``."""
    found: list[str] = []
    frontier = {key}
    while frontier:
        nxt = {f.key for f in facts if frontier & set(f.derived_from) and f.key not in found}
        found.extend(sorted(nxt))
        frontier = nxt
    return tuple(found)


# --- showing a fact -------------------------------------------------------------------------------


def display(fact: Fact) -> str:
    """How the fact is said or shown: ``14,99 €``, ``ca. 1,2 g``, ``12.11.``."""
    if fact.kind == "date" and "/" in fact.value:
        start, end = fact.value.split("/")
        return f"{_date_de(start)} bis {_date_de(end)}"
    if fact.kind == "date":
        return _date_de(fact.value)
    value = fact.value
    if fact.unit == "€":
        value = f"{value} €"
    elif fact.unit:
        value = f"{value} {fact.unit}"
    return f"ca. {value}" if fact.approximate else value


def _date_de(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{int(d)}.{int(m)}.{y}"


def substitute_facts(text: str, facts: Sequence[Fact]) -> tuple[str, tuple[str, ...]]:
    """``{{fact:price_nova}}`` replaced by the fact's display value; the unknown keys returned."""
    by_key = {f.key: f for f in facts}
    missing: list[str] = []

    def put(match: re.Match[str]) -> str:
        key = match.group(1)
        fact = by_key.get(key)
        if fact is None:
            missing.append(key)
            return match.group(0)
        return display(fact)

    return _FACT_REF.sub(put, text), tuple(missing)


def referenced_keys(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_FACT_REF.findall(text)))


def key_for(label: str) -> str:
    return slugify(label).replace("-", "_")
