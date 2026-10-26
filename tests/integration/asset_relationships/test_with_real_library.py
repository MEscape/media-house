"""Claims about REAL library assets, through the module's real wiring and its own database."""

import pytest

from media_house.modules.asset_relationships.application.contracts import (
    Basis,
    EndpointState,
    EvidenceKind,
    EvidenceSubmission,
    Grade,
    ProposeRelationshipCommand,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.shared.errors import Err, NotFoundError, Ok
from tests.integration.asset_relationships.conftest import Env

pytestmark = pytest.mark.integration

TAGS = {"scene_a": "12", "scene_b": "12", "take_a": "1", "take_b": "2"}


def claim(a: str, b: str) -> ProposeRelationshipCommand:
    return ProposeRelationshipCommand(
        RelationshipType.ALTERNATE_TAKE_OF,
        a,
        b,
        RelationshipOrigin.DETECTOR,
        (EvidenceSubmission(EvidenceKind.PRODUCTION_TAG_MATCH, "tag-detector", "1", TAGS),),
        Basis("tag-detector", "1"),
    )


def test_a_claim_between_two_real_assets_is_stored_in_its_own_database(env: Env) -> None:
    first = env.import_media(env.samples.cfr())
    second = env.import_media(env.samples.cfr_25())

    proposed = env.editor.propose(claim(second, first))

    assert isinstance(proposed, Ok), proposed
    assert (proposed.value.status, proposed.value.grade) == (
        RelationshipStatus.PROPOSED,
        Grade.INDICATED,
    )
    assert {proposed.value.source_asset_id, proposed.value.target_asset_id} == {first, second}
    assert (env.paths.data_dir / "asset_relationships.db").is_file()
    for end in (first, second):
        page = env.catalog.relationships_of(end)
        assert isinstance(page, Ok)
        assert [r.relationship_id for r in page.value.items] == [proposed.value.relationship_id]


def test_an_asset_that_is_not_in_the_library_cannot_be_related(env: Env) -> None:
    first = env.import_media(env.samples.cfr())

    result = env.editor.propose(claim(first, "not-in-the-library"))

    assert isinstance(result, Err)
    assert isinstance(result.error, NotFoundError)


def test_the_library_is_asked_not_copied_endpoint_state_follows_it(env: Env) -> None:
    first = env.import_media(env.samples.cfr())
    second = env.import_media(env.samples.cfr_25())
    proposed = env.editor.propose(claim(first, second))
    assert isinstance(proposed, Ok)
    relationship_id = proposed.value.relationship_id

    # the library saves a change only when updated_at moves, and a fixed clock never moves it
    env.clock.advance(seconds=1)
    assert isinstance(env.library.archive(second), Ok)
    archived = env.catalog.get(relationship_id)
    assert isinstance(env.library.delete(second), Ok)
    deleted = env.catalog.get(relationship_id)

    assert isinstance(archived, Ok)
    assert archived.value.endpoint_states[second] is EndpointState.ARCHIVED
    assert isinstance(deleted, Ok)
    assert deleted.value.endpoint_states[second] is EndpointState.MISSING
    assert deleted.value.endpoint_states[first] is EndpointState.ACTIVE
    # reconciliation of orphaned claims is a later slice: the claim is still there, and says so
    assert deleted.value.status is RelationshipStatus.PROPOSED


def test_a_person_confirms_and_the_decision_survives_a_new_assertion(env: Env) -> None:
    first = env.import_media(env.samples.cfr())
    second = env.import_media(env.samples.cfr_25())
    proposed = env.editor.propose(claim(first, second))
    assert isinstance(proposed, Ok)
    assert isinstance(env.editor.confirm(proposed.value.relationship_id, "same scene"), Ok)
    env.clock.advance(hours=1)

    again = env.editor.propose(claim(second, first))

    assert isinstance(again, Ok)
    assert again.value.relationship_id == proposed.value.relationship_id
    assert again.value.status is RelationshipStatus.CONFIRMED
    assert again.value.outdated_since is None  # identical assertion: nothing changed


def test_the_library_is_not_changed_by_relationships(env: Env) -> None:
    first = env.import_media(env.samples.cfr())
    second = env.import_media(env.samples.cfr_25())
    before = {a.id: a for a in env.library.get_many([first, second])}

    env.editor.propose(claim(first, second))

    assert {a.id: a for a in env.library.get_many([first, second])} == before
