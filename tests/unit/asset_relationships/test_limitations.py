"""Duplicate discovery is complete from a duplicate and partial from an original, and says so."""

import pytest

from media_house.modules.asset_relationships.domain.limitations import (
    exact_duplicate_limitations,
)
from media_house.modules.asset_relationships.domain.values import Limitation, RelationshipType

PARTIAL = (Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL,)
DUPLICATE_TYPE = RelationshipType.EXACT_DUPLICATE_OF


@pytest.mark.parametrize(
    ("is_original", "types", "expected"),
    [
        (True, {DUPLICATE_TYPE}, PARTIAL),
        (True, set(RelationshipType), PARTIAL),
        (True, {RelationshipType.PROXY_OF}, ()),
        (True, set(), ()),
        (False, {DUPLICATE_TYPE}, ()),
        (False, set(RelationshipType), ()),
    ],
)
def test_the_limitation_applies_to_an_original_whenever_duplicates_are_in_scope(
    is_original: bool, types: set[RelationshipType], expected: tuple[Limitation, ...]
) -> None:
    assert (
        exact_duplicate_limitations(queried_is_original=is_original, types_in_scope=types)
        == expected
    )


def test_an_empty_answer_is_still_flagged() -> None:
    """The flag does not depend on whether any duplicate was found: it depends on the query."""
    flagged = exact_duplicate_limitations(queried_is_original=True, types_in_scope={DUPLICATE_TYPE})

    assert flagged == PARTIAL
