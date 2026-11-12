"""The brief as a model: every item with the page or quote it came from.

Nothing here is a guess: an item without a source quote is refused. A fact is ``verified=False``
until somebody confirmed it against a live source.
"""

from dataclasses import dataclass

from media_house.modules.production_project.domain.values import ConstraintKind, ElementKind
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Source:
    """Where in the brief an item stands (``S. 2`` or ``Zeile 14``) and the words it stands in."""

    ref: str
    quote: str

    def __post_init__(self) -> None:
        if not self.quote.strip():
            raise InvariantViolation("A brief item needs the quote it came from")


@dataclass(frozen=True, slots=True)
class Constraint:
    constraint_id: str
    kind: ConstraintKind
    text: str
    #: The machine-checkable rule this compiles to ("" = a manual check, listed as such).
    rule: str
    source: Source
    #: What it concerns ("script", "assets", "description", "all").
    applies_to: tuple[str, ...] = ("all",)
    #: For a confidential item: the words that identify it in a file name or on a picture.
    keywords: tuple[str, ...] = ()
    #: ISO date the item stops binding (a publishing embargo), "" = no date.
    until: str = ""


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    label: str
    value: str
    unit: str
    source: Source | None
    verified: bool = False
    #: The keys of the facts this one was computed from (computed by code, never by a model).
    derived_from: tuple[str, ...] = ()
    #: A derived value that is rounded or modelled: said as "ungefähr".
    approximate: bool = False
    #: What the value is about ("product", "price", "date", "count", "url", "spec").
    kind: str = "spec"
    #: The product this fact belongs to ("" = about the campaign).
    subject: str = ""


@dataclass(frozen=True, slots=True)
class Deadline:
    deadline_id: str
    label: str
    #: ISO date and the time as written ("" when only a date is given).
    date: str
    time: str
    source: Source
    #: ``approval`` (a draft is due), ``publish`` (earliest or latest release), ``campaign``.
    kind: str = "approval"


@dataclass(frozen=True, slots=True)
class MandatoryElement:
    element_id: str
    kind: ElementKind
    text: str
    #: The tracking link that "arrives after approval": a placeholder until then.
    arrives_later: bool
    source: Source


@dataclass(frozen=True, slots=True)
class OptionalElement:
    element_id: str
    text: str
    #: How it must be phrased when it is used ("positiv").
    phrasing: str
    source: Source


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    question_id: str
    text: str
    why: str
    #: 1 = decide before anything is recorded, 3 = can wait.
    priority: int
    source: Source | None = None


@dataclass(frozen=True, slots=True)
class BriefModel:
    client: str
    campaign: str
    #: A short fingerprint of the brief text it was extracted from.
    source_digest: str
    constraints: tuple[Constraint, ...]
    facts: tuple[Fact, ...]
    deadlines: tuple[Deadline, ...]
    mandatory: tuple[MandatoryElement, ...]
    optional: tuple[OptionalElement, ...]
    open_questions: tuple[OpenQuestion, ...]

    def constraints_of(self, kind: ConstraintKind) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.kind is kind)

    def rule(self, rule: str) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.rule == rule)

    def fact(self, key: str) -> Fact | None:
        return next((f for f in self.facts if f.key == key), None)
