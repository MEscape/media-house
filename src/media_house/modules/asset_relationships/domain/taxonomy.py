"""What each relationship type means: direction, storage and admissible evidence.

Admissibility is the guard that keeps weak signals from becoming lineage: no evidence kind exists
for filename, duration, file size, visual or transcript similarity.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from media_house.modules.asset_relationships.domain.values import EvidenceKind, RelationshipType
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class TypeSpec:
    #: ``True``: the pair has no direction and is stored once in canonical order.
    symmetric: bool
    #: ``False``: a read-time projection of an authoritative record, never stored.
    storable: bool
    admissible: frozenset[EvidenceKind]
    #: Whether time intervals and clock offsets make sense for the type.
    intervals: bool


SPECS: Mapping[RelationshipType, TypeSpec] = MappingProxyType(
    {
        # duplicate -> original; the library allows one original per checksum
        RelationshipType.EXACT_DUPLICATE_OF: TypeSpec(
            symmetric=False,
            storable=False,
            admissible=frozenset({EvidenceKind.CHECKSUM_MATCH}),
            intervals=False,
        ),
        # proxy -> source
        RelationshipType.PROXY_OF: TypeSpec(
            symmetric=False,
            storable=False,
            admissible=frozenset({EvidenceKind.LIBRARY_DERIVATION, EvidenceKind.PROXY_PROVENANCE}),
            intervals=False,
        ),
        # derivative -> source
        RelationshipType.DERIVED_FROM: TypeSpec(
            symmetric=False,
            storable=False,
            admissible=frozenset({EvidenceKind.LIBRARY_DERIVATION}),
            intervals=False,
        ),
        RelationshipType.TEMPORALLY_OVERLAPS_WITH: TypeSpec(
            symmetric=True,
            storable=True,
            admissible=frozenset(
                {
                    EvidenceKind.WALL_CLOCK_OVERLAP,
                    EvidenceKind.TIMECODE_OVERLAP,
                    EvidenceKind.USER_ASSERTION,
                }
            ),
            intervals=True,
        ),
        RelationshipType.ALTERNATE_TAKE_OF: TypeSpec(
            symmetric=True,
            storable=True,
            admissible=frozenset({EvidenceKind.PRODUCTION_TAG_MATCH, EvidenceKind.USER_ASSERTION}),
            intervals=False,
        ),
    }
)


#: Recorded lineage: projected from authoritative records at read time, never stored.
LINEAGE_TYPES: frozenset[RelationshipType] = frozenset(
    t for t, spec in SPECS.items() if not spec.storable
)
#: Claims: stored here, with evidence and a status.
STORED_TYPES: frozenset[RelationshipType] = frozenset(
    t for t, spec in SPECS.items() if spec.storable
)


def spec_of(relationship_type: RelationshipType) -> TypeSpec:
    return SPECS[relationship_type]


def canonical_endpoints(
    relationship_type: RelationshipType, first: str, second: str
) -> tuple[str, str, bool]:
    """The endpoints as stored, and whether they were swapped.

    A directed type keeps ``(source, target)``. A symmetric type stores the lower id first, so one
    row represents the pair whichever way it was submitted.
    """
    if not first or not second:
        raise InvariantViolation("A relationship needs two asset ids")
    if first == second:
        raise InvariantViolation(
            "An asset cannot be related to itself", user_message="Choose two different assets."
        )
    if spec_of(relationship_type).symmetric and first > second:
        return second, first, True
    return first, second, False
