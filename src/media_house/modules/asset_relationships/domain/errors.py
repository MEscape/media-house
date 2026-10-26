"""Domain errors of asset relationships.

Expected failures are returned by use cases as ``Err``; the domain and repository raise these and
the use cases translate them.
"""

from media_house.shared.errors import DomainError, NotFoundError, PersistenceError


class IllegalTransition(DomainError):
    """The status change is not allowed from the relationship's current status."""

    code = "relationships.illegal_transition"


class CorruptClaim(PersistenceError):
    """A stored claim cannot be turned back into a valid relationship (the store, not the library,
    is damaged). Reconciliation skips such a claim instead of failing the whole sweep."""

    code = "relationships.corrupt_claim"


class RelationshipNotFound(NotFoundError):
    code = "relationships.not_found"

    def __init__(self, relationship_id: str) -> None:
        super().__init__(
            f"Relationship not found: {relationship_id}",
            user_message="That relationship does not exist.",
            details={"relationship_id": relationship_id},
        )
