"""The store port. Writes are atomic read-modify-write, so racing writers converge on one row."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.values import (
    Grade,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)


@dataclass(frozen=True, slots=True)
class RelationshipCriteria:
    """Stored claims touching ``asset_id`` on either end. Empty collections: no restriction."""

    asset_id: str
    types: frozenset[RelationshipType] = frozenset()
    statuses: frozenset[RelationshipStatus] = frozenset()
    grades: frozenset[Grade] = frozenset()
    origins: frozenset[RelationshipOrigin] = frozenset()
    #: Keyset paging: only relationships whose id sorts after this one.
    after: str | None = None
    limit: int = 50


class RelationshipRepository(Protocol):
    def update(
        self,
        relationship_id: RelationshipId,
        change: Callable[[AssetRelationship | None], AssetRelationship | None],
    ) -> AssetRelationship | None:
        """Load the relationship (or ``None``), let ``change`` edit or create it, save atomically.

        ``change`` returns the relationship to save, or ``None`` to leave the store untouched. An
        exception raised by ``change`` writes nothing and propagates. Returns what the store holds.
        """
        ...

    def get(self, relationship_id: RelationshipId) -> AssetRelationship | None: ...

    def find(self, criteria: RelationshipCriteria) -> Sequence[AssetRelationship]:
        """At most ``criteria.limit`` claims in id order."""
        ...
