"""What a result cannot promise, decided by one rule so every query path states it the same way."""

from collections.abc import Set as AbstractSet

from media_house.modules.asset_relationships.domain.values import Limitation, RelationshipType


def exact_duplicate_limitations(
    *, queried_is_original: bool, types_in_scope: AbstractSet[RelationshipType]
) -> tuple[Limitation, ...]:
    """The limitation of duplicate discovery for a query.

    From an original only its own derivatives can be checked (the library cannot list every asset
    sharing the original's bytes), so the limitation is attached whenever the query covers
    ``exact_duplicate_of`` for an original, whether or not any duplicate was found: an empty answer
    must not read as exhaustive. From a non-original the original is found exactly, so none applies.
    """
    if queried_is_original and RelationshipType.EXACT_DUPLICATE_OF in types_in_scope:
        return (Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL,)
    return ()
