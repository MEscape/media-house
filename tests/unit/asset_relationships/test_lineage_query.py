"""Stored claims and recorded lineage answered together, and the limitation flags of a page."""

import pytest

from media_house.modules.asset_relationships.application.commands import (
    EvidenceSubmission,
    ProposeRelationshipCommand,
    RelationshipQuery,
)
from media_house.modules.asset_relationships.application.dto import RelationshipPage
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
from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    Limitation,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.media_library.application.contracts import AssetStatus
from media_house.shared.errors import Err, NotFoundError, Ok
from tests.support.fakes import FixedClock
from tests.support.relationship_fakes import (
    FakeAssetLookup,
    FakeProxyCatalog,
    InMemoryRelationshipRepository,
    checksum_of,
    derivative,
    original,
    proxy,
)

TAKE = RelationshipType.ALTERNATE_TAKE_OF
DUPLICATE = RelationshipType.EXACT_DUPLICATE_OF
PROXY = RelationshipType.PROXY_OF
DERIVED = RelationshipType.DERIVED_FROM
PARTIAL = (Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL,)


class World:
    """A library, a store, a proxy catalog and the query over them."""

    def __init__(self) -> None:
        self.repository = InMemoryRelationshipRepository()
        self.library = FakeAssetLookup(*(original(i) for i in ("a", "b", "c", "d")))
        self.proxies = FakeProxyCatalog()
        self.query = QueryRelationships(
            self.repository, self.library, LineageProjection(self.library, self.proxies)
        )
        self.propose = ProposeRelationship(self.repository, self.library, FixedClock())

    def claim(self, first: str, second: str) -> None:
        result = self.propose.execute(
            ProposeRelationshipCommand(
                TAKE,
                first,
                second,
                RelationshipOrigin.DETECTOR,
                (
                    EvidenceSubmission(
                        EvidenceKind.PRODUCTION_TAG_MATCH,
                        "tag-detector",
                        "1",
                        {"scene_a": "1", "scene_b": "1", "take_a": "1", "take_b": "2"},
                    ),
                ),
                Basis("tag-detector", "1"),
            )
        )
        assert isinstance(result, Ok), result

    def page(self, asset: str, **query: object) -> RelationshipPage:
        result = self.query.relationships_of(asset, RelationshipQuery(**query))  # type: ignore[arg-type]
        assert isinstance(result, Ok), result
        return result.value


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def mixed(world: World) -> World:
    world.library.add(derivative("out", "a"))
    world.library.add(proxy("prx", "a"))
    world.claim("a", "b")
    return world


class TestLineageAlongsideClaims:
    def test_claims_and_recorded_lineage_come_back_together_and_are_told_apart(
        self, mixed: World
    ) -> None:
        items = mixed.page("a").items

        assert {(i.type, i.stored, i.origin) for i in items} == {
            (TAKE, True, RelationshipOrigin.DETECTOR),
            (DERIVED, False, RelationshipOrigin.LIBRARY_RECORD),
            (PROXY, False, RelationshipOrigin.PROXY_RECORD),
        }

    def test_a_claim_has_a_status_and_a_fact_has_none(self, mixed: World) -> None:
        items = {i.type: i for i in mixed.page("a").items}

        assert items[TAKE].status is RelationshipStatus.PROPOSED
        assert items[DERIVED].status is None
        assert items[PROXY].status is None
        assert items[DERIVED].grade is Grade.RECORDED

    def test_one_deterministic_order_over_both(self, mixed: World) -> None:
        ids = [i.relationship_id for i in mixed.page("a").items]

        assert ids == sorted(ids)
        assert len(ids) == len(set(ids)) == 3

    def test_the_same_query_gives_the_same_answer(self, mixed: World) -> None:
        assert mixed.page("a") == mixed.page("a")

    def test_no_projected_item_is_ever_stored(self, mixed: World) -> None:
        mixed.page("a")
        mixed.page("prx")
        mixed.page("out")

        assert [r.type for r in mixed.repository.find(RelationshipCriteria("a"))] == [TAKE]
        assert mixed.repository.find(RelationshipCriteria("out")) == []

    def test_a_projected_id_cannot_be_fetched_as_a_stored_claim(self, mixed: World) -> None:
        projected = next(i for i in mixed.page("a").items if not i.stored)

        result = mixed.query.get(projected.relationship_id)

        assert isinstance(result, Err)
        assert isinstance(result.error, NotFoundError)

    def test_pages_walk_the_merged_answer_once_each(self, mixed: World) -> None:
        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = mixed.page("a", page_size=2, cursor=cursor)
            seen += [i.relationship_id for i in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break

        assert seen == sorted(seen)
        assert len(seen) == len(set(seen)) == 3

    def test_the_cursor_applies_to_lineage_too(self, mixed: World) -> None:
        first = mixed.page("a", page_size=1)
        second = mixed.page("a", page_size=1, cursor=first.next_cursor)

        assert first.items[0].relationship_id < second.items[0].relationship_id

    def test_a_status_filter_asks_for_claims_only(self, mixed: World) -> None:
        page = mixed.page("a", statuses=frozenset({RelationshipStatus.PROPOSED}))

        assert [i.type for i in page.items] == [TAKE]
        assert page.limitations == ()

    def test_types_pick_lineage_or_claims(self, mixed: World) -> None:
        def types(*wanted: RelationshipType) -> set[RelationshipType]:
            return {i.type for i in mixed.page("a", types=frozenset(wanted)).items}

        assert types(DERIVED) == {DERIVED}
        assert types(TAKE) == {TAKE}
        assert types(TAKE, PROXY) == {TAKE, PROXY}

    def test_origins_pick_the_authority(self, mixed: World) -> None:
        def types(*origins: RelationshipOrigin) -> set[RelationshipType]:
            return {i.type for i in mixed.page("a", origins=frozenset(origins)).items}

        assert types(RelationshipOrigin.PROXY_RECORD) == {PROXY}
        assert types(RelationshipOrigin.LIBRARY_RECORD) == {DERIVED}
        assert types(RelationshipOrigin.DETECTOR) == {TAKE}
        assert types(RelationshipOrigin.USER) == set()

    def test_min_grade_keeps_recorded_facts_and_filters_claims(self, mixed: World) -> None:
        page = mixed.page("a", min_grade=Grade.CORROBORATED)

        assert {i.type for i in page.items} == {DERIVED, PROXY}

    def test_lineage_can_be_switched_off_and_then_the_library_is_not_searched(
        self, mixed: World
    ) -> None:
        page = mixed.page("a", include_projected=False)

        assert [i.type for i in page.items] == [TAKE]
        assert page.limitations == ()
        assert mixed.library.searches == []

    def test_archived_counterparts_follow_include_archived_but_claims_do_not(
        self, mixed: World
    ) -> None:
        mixed.library.add(derivative("shelved", "a", status=AssetStatus.ARCHIVED))
        mixed.library.add(original("b", status=AssetStatus.ARCHIVED))

        hidden = mixed.page("a")
        shown = mixed.page("a", include_archived=True)

        assert "shelved" not in {i.source_asset_id for i in hidden.items}
        assert "shelved" in {i.source_asset_id for i in shown.items}
        assert TAKE in {i.type for i in hidden.items}  # claims always come back, flagged

    def test_the_proxy_serving_state_reaches_the_item(self, mixed: World) -> None:
        mixed.proxies.served[("a", "analysis")] = "prx"

        item = next(i for i in mixed.page("a").items if i.type is PROXY)

        assert item.serving is True

    def test_a_deleted_proxy_disappears_from_the_answer(self, mixed: World) -> None:
        mixed.library.remove("prx")

        assert PROXY not in {i.type for i in mixed.page("a").items}

    def test_a_truncated_scan_is_reported_on_the_page(self, world: World) -> None:
        for number in range(1001):
            world.library.add(derivative(f"d{number:05d}", "a"))

        page = world.page("a", types=frozenset({DERIVED}), page_size=200)

        assert page.truncated is True
        assert len(page.items) == 200

    def test_a_complete_scan_is_not_truncated(self, world: World) -> None:
        for number in range(450):
            world.library.add(derivative(f"d{number:05d}", "a"))

        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = world.page("a", types=frozenset({DERIVED}), page_size=200, cursor=cursor)
            assert page.truncated is False
            seen += [i.relationship_id for i in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break

        assert len(seen) == len(set(seen)) == 450

    def test_an_unknown_asset_is_not_found(self, world: World) -> None:
        result = world.query.relationships_of("ghost")

        assert isinstance(result, Err)
        assert isinstance(result.error, NotFoundError)

    def test_a_deleted_asset_cannot_be_asked_about_and_leaves_no_lineage_behind(
        self, mixed: World
    ) -> None:
        mixed.library.remove("a")
        mixed.library.remove("out")
        mixed.library.remove("prx")

        assert isinstance(mixed.query.relationships_of("a"), Err)
        assert isinstance(mixed.query.relationships_of("prx"), Err)
        assert (
            mixed.page("b").items[0].stored is True
        )  # the stored claim remains; endpoint is missing
        assert mixed.page("b").items[0].endpoint_states["a"].value == "missing"


class TestLimitations:
    def test_an_original_is_flagged_even_when_it_has_no_derivatives(self, world: World) -> None:
        page = world.page("d")

        assert page.items == ()
        assert page.limitations == PARTIAL

    def test_an_original_is_flagged_even_when_a_duplicate_was_found(self, world: World) -> None:
        world.library.add(derivative("same", "a", checksum=checksum_of("a")))

        page = world.page("a", types=frozenset({DUPLICATE}))

        assert [i.type for i in page.items] == [DUPLICATE]
        assert page.limitations == PARTIAL

    def test_a_non_original_is_not_flagged_its_original_is_found_exactly(
        self, world: World
    ) -> None:
        world.library.add(derivative("far", "b", checksum=checksum_of("a")))

        page = world.page("far", types=frozenset({DUPLICATE}))

        assert [(i.source_asset_id, i.target_asset_id) for i in page.items] == [("far", "a")]
        assert page.limitations == ()

    @pytest.mark.parametrize(
        "query",
        [
            {"types": frozenset({DERIVED})},
            {"types": frozenset({TAKE})},
            {"types": frozenset({PROXY})},
            {"origins": frozenset({RelationshipOrigin.PROXY_RECORD})},
            {"origins": frozenset({RelationshipOrigin.DETECTOR})},
            {"statuses": frozenset({RelationshipStatus.CONFIRMED})},
            {"include_projected": False},
        ],
    )
    def test_no_flag_when_duplicates_are_not_in_scope(
        self, world: World, query: dict[str, object]
    ) -> None:
        assert world.page("a", **query).limitations == ()

    @pytest.mark.parametrize(
        "query",
        [
            {},
            {"types": frozenset({DUPLICATE})},
            {"types": frozenset({DUPLICATE, TAKE})},
            {"origins": frozenset({RelationshipOrigin.LIBRARY_RECORD})},
            {"min_grade": Grade.SUGGESTIVE},
            {"include_archived": True},
            {"page_size": 1},
        ],
    )
    def test_the_flag_when_duplicates_are_in_scope_for_an_original(
        self, world: World, query: dict[str, object]
    ) -> None:
        assert world.page("a", **query).limitations == PARTIAL

    def test_every_page_of_an_original_carries_it(self, world: World) -> None:
        for number in range(3):
            world.library.add(derivative(f"d{number}", "a"))

        first = world.page("a", page_size=1)
        later = world.page("a", page_size=1, cursor=first.next_cursor)

        assert first.limitations == later.limitations == PARTIAL

    def test_the_flag_is_stable_documented_text(self) -> None:
        assert Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL.value == "exact_duplicate_reverse_partial"
        assert list(Limitation) == [
            Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL,
            Limitation.PROXY_STATE_UNAVAILABLE,
            Limitation.LINEAGE_CHANGED_WHILE_PAGING,
        ]
        assert Limitation.PROXY_STATE_UNAVAILABLE.value == "proxy_state_unavailable"
        assert Limitation.LINEAGE_CHANGED_WHILE_PAGING.value == "lineage_changed_while_paging"
