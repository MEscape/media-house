"""Vocabulary of asset relationships: types, statuses, grades, origins and evidence kinds.

The enum is named ``RelationshipType`` because three other modules already define a ``RelationKind``
with a different meaning.
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class RelationshipType(StrEnum):
    """What a relationship says. Lineage types are projected from authoritative records at read
    time and never stored; the others are stored claims."""

    EXACT_DUPLICATE_OF = "exact_duplicate_of"
    PROXY_OF = "proxy_of"
    DERIVED_FROM = "derived_from"
    TEMPORALLY_OVERLAPS_WITH = "temporally_overlaps_with"
    ALTERNATE_TAKE_OF = "alternate_take_of"


class RelationshipStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    #: The basis a proposal rested on is out of date; it must be re-detected before it is trusted.
    STALE = "stale"


class Grade(StrEnum):
    """How well the evidence supports a claim for its type. Ordinal, not a probability."""

    RECORDED = "recorded"  # an authoritative store states it (projections only)
    CORROBORATED = "corroborated"  # independent evidence kinds agree and none contradicts
    INDICATED = "indicated"  # one evidence kind whose semantics are defined for the type
    SUGGESTIVE = "suggestive"  # circumstantial: unverified assumptions remain


#: Strongest first.
GRADE_ORDER: tuple[Grade, ...] = (
    Grade.RECORDED,
    Grade.CORROBORATED,
    Grade.INDICATED,
    Grade.SUGGESTIVE,
)


def grades_at_least(minimum: Grade) -> frozenset[Grade]:
    """``minimum`` and every stronger grade."""
    return frozenset(GRADE_ORDER[: GRADE_ORDER.index(minimum) + 1])


class RelationshipOrigin(StrEnum):
    LIBRARY_RECORD = "library_record"  # projected from a library derivation record
    PROXY_RECORD = "proxy_record"  # projected from proxy provenance
    DETECTOR = "detector"
    USER = "user"


#: Origins a stored claim can have.
STORABLE_ORIGINS = frozenset({RelationshipOrigin.DETECTOR, RelationshipOrigin.USER})


class EvidenceKind(StrEnum):
    CHECKSUM_MATCH = "checksum_match"
    LIBRARY_DERIVATION = "library_derivation"
    PROXY_PROVENANCE = "proxy_provenance"
    WALL_CLOCK_OVERLAP = "wall_clock_overlap"
    TIMECODE_OVERLAP = "timecode_overlap"
    PRODUCTION_TAG_MATCH = "production_tag_match"
    USER_ASSERTION = "user_assertion"


class ClockDomain(StrEnum):
    #: Seconds on the container timeline (zero is the media's time origin).
    CONTAINER = "container"
    #: ``Timecode.frame_number(frame_rate) / frame_rate``; zero is 00:00:00:00.
    TIMECODE = "timecode"
    #: Seconds since the Unix epoch, UTC, from an aware timestamp only.
    WALL_CLOCK = "wall_clock"


class OffsetOrigin(StrEnum):
    USER_ASSERTED = "user_asserted"
    TIMECODE_DIFFERENCE = "timecode_difference"
    WALL_CLOCK_DIFFERENCE = "wall_clock_difference"


class Limitation(StrEnum):
    """Something a result cannot promise. Attached so absence is never read as proof."""

    #: Duplicates of an original are found only among its own derivatives.
    EXACT_DUPLICATE_REVERSE_PARTIAL = "exact_duplicate_reverse_partial"
    #: ``proxy_manager`` could not say which proxy a slot serves, so ``serving`` is ``None``.
    PROXY_STATE_UNAVAILABLE = "proxy_state_unavailable"
    #: The number of derivatives changed between two pages, so the pages may not fit together.
    #: An UNCHANGED number proves nothing: an insertion plus a deletion keeps it.
    LINEAGE_CHANGED_WHILE_PAGING = "lineage_changed_while_paging"


@dataclass(frozen=True, slots=True)
class RelationshipId:
    """Deterministic: the same type and endpoints always give the same id (idempotent upserts)."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvariantViolation("RelationshipId must not be empty")

    @classmethod
    def of(cls, relationship_type: RelationshipType, first: str, second: str) -> "RelationshipId":
        digest = hashlib.sha256(f"{relationship_type.value}|{first}|{second}".encode()).hexdigest()
        return cls(digest[:32])
