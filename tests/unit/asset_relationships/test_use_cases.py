"""Propose, decide and query against fakes: Ok, every Err, idempotence, paging, endpoint state."""

from dataclasses import dataclass, replace

import pytest

from media_house.modules.asset_relationships.application.commands import (
    DecideRelationshipCommand,
    Decision,
    EvidenceSubmission,
    ProposeRelationshipCommand,
    RelationshipQuery,
)
from media_house.modules.asset_relationships.application.decide_relationship import (
    DecideRelationship,
)
from media_house.modules.asset_relationships.application.dto import (
    EndpointState,
    RelationshipDto,
)
from media_house.modules.asset_relationships.application.editing import RelationshipEditing
from media_house.modules.asset_relationships.application.lineage_projection import (
    LineageProjection,
)
from media_house.modules.asset_relationships.application.propose_relationship import (
    ProposeRelationship,
)
from media_house.modules.asset_relationships.application.query_relationships import (
    QueryRelationships,
)
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.values import (
    ClockDomain,
    EvidenceKind,
    Grade,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.media_library.application.contracts import AssetStatus
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, ValidationError
from tests.support.fakes import FixedClock
from tests.support.relationship_fakes import (
    CONTAINER,
    WALL,
    FakeAssetLookup,
    FakeProxyCatalog,
    InMemoryRelationshipRepository,
    clock_offset,
    interval,
    original,
)

TAKE = RelationshipType.ALTERNATE_TAKE_OF
OVERLAP = RelationshipType.TEMPORALLY_OVERLAPS_WITH
TAG: dict[str, str | bool] = {"scene_a": "12", "scene_b": "12", "take_a": "1", "take_b": "2"}


def tags(**detail: str | bool) -> EvidenceSubmission:
    return EvidenceSubmission(
        EvidenceKind.PRODUCTION_TAG_MATCH,
        "tag-detector",
        "1",
        {**TAG, **detail},
    )


def wall(trust: str = "same_device") -> EvidenceSubmission:
    return EvidenceSubmission(
        EvidenceKind.WALL_CLOCK_OVERLAP, "overlap-detector", "1", {"clock_trust": trust}
    )


def take(a: str = "a", b: str = "b", **changes: object) -> ProposeRelationshipCommand:
    base = ProposeRelationshipCommand(
        TAKE, a, b, RelationshipOrigin.DETECTOR, (tags(),), Basis("tag-detector", "1")
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@dataclass
class Env:
    repository: InMemoryRelationshipRepository
    library: FakeAssetLookup
    clock: FixedClock
    propose: ProposeRelationship
    decide: DecideRelationship
    query: QueryRelationships
    editing: RelationshipEditing


@pytest.fixture
def env() -> Env:
    repository = InMemoryRelationshipRepository()
    library = FakeAssetLookup(*(original(i) for i in ("a", "b", "c", "d")))
    clock = FixedClock()
    propose = ProposeRelationship(repository, library, clock)
    decide = DecideRelationship(repository, library, clock)
    return Env(
        repository,
        library,
        clock,
        propose,
        decide,
        QueryRelationships(repository, library, LineageProjection(library, FakeProxyCatalog())),
        RelationshipEditing(propose, decide),
    )


def proposed(env: Env, command: ProposeRelationshipCommand | None = None) -> RelationshipDto:
    result = env.propose.execute(command or take())
    assert isinstance(result, Ok), result
    return result.value


class TestPropose:
    def test_creates_a_proposal_with_evidence_and_endpoint_state(self, env: Env) -> None:
        dto = proposed(env)

        assert (dto.type, dto.status, dto.grade) == (
            TAKE,
            RelationshipStatus.PROPOSED,
            Grade.INDICATED,
        )
        assert (dto.source_asset_id, dto.target_asset_id, dto.symmetric) == ("a", "b", True)
        assert dto.evidence[0].kind is EvidenceKind.PRODUCTION_TAG_MATCH
        assert dto.evidence[0].observed_at == env.clock.now()
        assert (dto.basis_detector, dto.basis_version) == ("tag-detector", "1")
        assert dto.endpoint_states == {"a": EndpointState.ACTIVE, "b": EndpointState.ACTIVE}
        assert dto.history[0].status_to is RelationshipStatus.PROPOSED

    def test_either_order_gives_the_same_relationship(self, env: Env) -> None:
        first = proposed(env, take("a", "b"))
        second = proposed(env, take("b", "a"))

        assert first.relationship_id == second.relationship_id

    def test_an_unknown_asset_is_not_found_and_nothing_is_stored(self, env: Env) -> None:
        result = env.propose.execute(take("a", "ghost"))

        assert isinstance(result, Err)
        assert isinstance(result.error, NotFoundError)
        page = env.query.relationships_of("a")
        assert isinstance(page, Ok)
        assert page.value.items == ()

    @pytest.mark.parametrize(
        "command",
        [
            take(relationship_type=RelationshipType.PROXY_OF),
            take(origin=RelationshipOrigin.LIBRARY_RECORD),
            take(evidence=()),
            take(evidence=(wall(),)),
            take(evidence=(tags(scene_a=""),)),
            take("a", "a"),
            take(a_interval=interval(0, 5)),
        ],
        ids=[
            "lineage type",
            "library origin",
            "no evidence",
            "inadmissible evidence",
            "invalid evidence detail",
            "self relation",
            "interval on a take",
        ],
    )
    def test_invalid_input_is_a_validation_error(
        self, env: Env, command: ProposeRelationshipCommand
    ) -> None:
        result = env.propose.execute(command)

        assert isinstance(result, Err)
        assert isinstance(result.error, ValidationError)
        assert result.error.user_message

    def test_repeating_the_same_assertion_changes_nothing(self, env: Env) -> None:
        first = proposed(env)
        env.clock.advance(hours=1)

        second = proposed(env)

        assert second == first
        assert second.updated_at == first.updated_at

    def test_new_evidence_refreshes_and_moves_updated_at(self, env: Env) -> None:
        first = proposed(env)
        env.clock.advance(hours=1)

        second = proposed(env, take(evidence=(tags(same_device=True),)))

        assert second.grade is Grade.CORROBORATED
        assert second.updated_at > first.updated_at
        assert second.created_at == first.created_at

    def test_a_decision_survives_a_new_assertion(self, env: Env) -> None:
        first = proposed(env)
        assert isinstance(env.editing.reject(first.relationship_id, "not the same scene"), Ok)
        env.clock.advance(hours=1)

        again = proposed(env, take(evidence=(tags(same_device=True),)))

        assert again.status is RelationshipStatus.REJECTED
        assert again.outdated_since == env.clock.now()
        assert [e.superseded for e in again.evidence] == [False, True]

    def test_intervals_and_offset_round_trip_in_canonical_order(self, env: Env) -> None:
        command = ProposeRelationshipCommand(
            OVERLAP,
            "b",
            "a",
            RelationshipOrigin.DETECTOR,
            (wall(),),
            Basis("overlap", "1"),
            a_interval=interval(5, 15, CONTAINER),
            b_interval=interval(0, 10, WALL),
            offset=clock_offset(-5, 1),
        )

        dto = proposed(env, command)

        assert (dto.source_asset_id, dto.target_asset_id) == ("a", "b")
        assert dto.source_interval is not None
        assert dto.source_interval.domain is ClockDomain.WALL_CLOCK
        assert dto.target_interval is not None
        assert dto.target_interval.domain is ClockDomain.CONTAINER
        assert dto.offset is not None
        assert dto.offset.seconds == 5


class TestDecide:
    def test_confirm_then_reject_are_recorded(self, env: Env) -> None:
        created = proposed(env)

        confirmed = env.editing.confirm(created.relationship_id, "same scene")
        rejected = env.editing.reject(created.relationship_id, "changed my mind")

        assert isinstance(confirmed, Ok)
        assert confirmed.value.status is RelationshipStatus.CONFIRMED
        assert isinstance(rejected, Ok)
        assert [h.status_to for h in rejected.value.history] == [
            RelationshipStatus.PROPOSED,
            RelationshipStatus.CONFIRMED,
            RelationshipStatus.REJECTED,
        ]
        assert rejected.value.history[-1].note == "changed my mind"

    @pytest.mark.parametrize("relationship_id", ["", "missing"])
    def test_an_unknown_relationship_is_not_found(self, env: Env, relationship_id: str) -> None:
        result = env.editing.confirm(relationship_id)

        assert isinstance(result, Err)
        assert isinstance(result.error, NotFoundError)

    def test_an_illegal_transition_is_a_conflict_that_changes_nothing(self, env: Env) -> None:
        created = proposed(env)
        assert isinstance(env.editing.confirm(created.relationship_id), Ok)

        result = env.editing.confirm(created.relationship_id)

        assert isinstance(result, Err)
        assert isinstance(result.error, ConflictError)
        stored = env.query.get(created.relationship_id)
        assert isinstance(stored, Ok)
        assert len(stored.value.history) == 2

    def test_a_decision_is_stamped_by_the_clock(self, env: Env) -> None:
        created = proposed(env)
        env.clock.advance(minutes=5)

        result = env.decide.execute(
            DecideRelationshipCommand(created.relationship_id, Decision.REJECT)
        )

        assert isinstance(result, Ok)
        assert result.value.updated_at == env.clock.now()

    def test_decisions_report_endpoint_state(self, env: Env) -> None:
        created = proposed(env)
        env.library.remove("b")

        result = env.editing.confirm(created.relationship_id)

        assert isinstance(result, Ok)
        assert result.value.endpoint_states == {
            "a": EndpointState.ACTIVE,
            "b": EndpointState.MISSING,
        }


class TestQuery:
    def test_finds_a_relationship_from_either_end(self, env: Env) -> None:
        created = proposed(env)

        for end in ("a", "b"):
            page = env.query.relationships_of(end)
            assert isinstance(page, Ok)
            assert [r.relationship_id for r in page.value.items] == [created.relationship_id]

    def test_an_unknown_asset_is_not_found(self, env: Env) -> None:
        result = env.query.relationships_of("ghost")

        assert isinstance(result, Err)
        assert isinstance(result.error, NotFoundError)

    def test_an_asset_without_relationships_has_an_empty_page(self, env: Env) -> None:
        page = env.query.relationships_of("d")

        assert isinstance(page, Ok)
        assert (page.value.items, page.value.next_cursor) == ((), None)

    def test_filters_by_type_status_grade_and_origin(self, env: Env) -> None:
        take_dto = proposed(env, take("a", "b", evidence=(tags(same_device=True),)))
        overlap_dto = proposed(
            env,
            ProposeRelationshipCommand(
                OVERLAP, "a", "c", RelationshipOrigin.USER, (wall("unverified"),)
            ),
        )
        assert isinstance(env.editing.confirm(overlap_dto.relationship_id), Ok)

        def ids(query: RelationshipQuery) -> set[str]:
            page = env.query.relationships_of("a", query)
            assert isinstance(page, Ok)
            return {r.relationship_id for r in page.value.items}

        both = {take_dto.relationship_id, overlap_dto.relationship_id}
        assert ids(RelationshipQuery()) == both
        assert ids(RelationshipQuery(types=frozenset({TAKE}))) == {take_dto.relationship_id}
        assert ids(RelationshipQuery(statuses=frozenset({RelationshipStatus.CONFIRMED}))) == {
            overlap_dto.relationship_id
        }
        assert ids(RelationshipQuery(min_grade=Grade.INDICATED)) == {take_dto.relationship_id}
        assert ids(RelationshipQuery(min_grade=Grade.SUGGESTIVE)) == both
        assert ids(RelationshipQuery(origins=frozenset({RelationshipOrigin.USER}))) == {
            overlap_dto.relationship_id
        }

    @pytest.mark.parametrize("size", [0, 201, -1])
    def test_page_size_is_bounded(self, env: Env, size: int) -> None:
        result = env.query.relationships_of("a", RelationshipQuery(page_size=size))

        assert isinstance(result, Err)
        assert isinstance(result.error, ValidationError)

    def test_pages_walk_every_relationship_once_in_id_order(self, env: Env) -> None:
        expected = {proposed(env, take("a", other)).relationship_id for other in ("b", "c", "d")}

        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = env.query.relationships_of("a", RelationshipQuery(page_size=2, cursor=cursor))
            assert isinstance(page, Ok)
            seen += [r.relationship_id for r in page.value.items]
            cursor = page.value.next_cursor
            if cursor is None:
                break

        assert sorted(seen) == seen
        assert set(seen) == expected
        assert len(seen) == 3

    def test_a_full_last_page_has_no_cursor(self, env: Env) -> None:
        proposed(env, take("a", "b"))
        proposed(env, take("a", "c"))

        page = env.query.relationships_of("a", RelationshipQuery(page_size=2))

        assert isinstance(page, Ok)
        assert (len(page.value.items), page.value.next_cursor) == (2, None)

    def test_endpoint_state_is_checked_against_the_live_library_on_every_read(
        self, env: Env
    ) -> None:
        created = proposed(env)
        env.library.add(original("b", status=AssetStatus.ARCHIVED))

        archived = env.query.get(created.relationship_id)
        env.library.remove("b")
        missing = env.query.get(created.relationship_id)

        assert isinstance(archived, Ok)
        assert archived.value.endpoint_states["b"] is EndpointState.ARCHIVED
        assert isinstance(missing, Ok)
        assert missing.value.endpoint_states["b"] is EndpointState.MISSING
        assert missing.value.endpoint_states["a"] is EndpointState.ACTIVE

    @pytest.mark.parametrize("relationship_id", ["", "nope"])
    def test_get_of_an_unknown_relationship_is_not_found(
        self, env: Env, relationship_id: str
    ) -> None:
        result = env.query.get(relationship_id)

        assert isinstance(result, Err)
        assert isinstance(result.error, NotFoundError)

    def test_a_query_for_claims_only_carries_no_limitation(self, env: Env) -> None:
        proposed(env)

        page = env.query.relationships_of("a", RelationshipQuery(types=frozenset({TAKE})))

        assert isinstance(page, Ok)
        assert page.value.limitations == ()
