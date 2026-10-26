"""Recorded lineage projected from the library and proxy provenance, over fakes.

The fakes keep the library's real semantics (originals only for checksum lookup, paged search by
source), so these tests prove the projection's logic, bounds and limits. The same behaviour against
the REAL library and proxy manager is in ``tests/integration/asset_relationships``.
"""

from datetime import timedelta

import pytest

from media_house.modules.asset_relationships.application.dto import (
    EndpointState,
)
from media_house.modules.asset_relationships.application.lineage_projection import (
    LineageProjection,
    LineageSettings,
    ProjectedLineage,
)
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    RelationshipOrigin,
    RelationshipType,
)
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    AssetStatus,
    MediaAssetDto,
    MediaType,
)
from media_house.shared.errors import Ok
from tests.support.asset_dto import NOW
from tests.support.relationship_fakes import (
    FakeAssetLookup,
    FakeProxyCatalog,
    checksum_of,
    derivative,
    original,
    proxy,
    proxy_metadata,
)

DUPLICATE = RelationshipType.EXACT_DUPLICATE_OF
PROXY = RelationshipType.PROXY_OF
DERIVED = RelationshipType.DERIVED_FROM
ALL = frozenset({DUPLICATE, PROXY, DERIVED})


def project(
    library: FakeAssetLookup,
    asset_id: str,
    *,
    scope: frozenset[RelationshipType] = ALL,
    proxies: FakeProxyCatalog | None = None,
    include_archived: bool = False,
    settings: LineageSettings | None = None,
) -> ProjectedLineage:
    found = library.get(asset_id)
    assert isinstance(found, Ok)
    return LineageProjection(library, proxies, settings).project(
        found.value, scope, include_archived=include_archived
    )


def pairs(lineage: ProjectedLineage) -> set[tuple[RelationshipType, str, str]]:
    return {(i.type, i.source_asset_id, i.target_asset_id) for i in lineage.items}


class TestDerivedFrom:
    def test_a_derivative_points_at_its_source_from_either_end(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"))
        expected = {(DERIVED, "out", "src")}

        assert pairs(project(library, "out")) == expected
        assert pairs(project(library, "src")) == expected

    def test_the_item_is_a_recorded_fact_not_a_claim(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"))

        (item,) = project(library, "src").items

        assert item.stored is False
        assert item.status is None
        assert item.grade is Grade.RECORDED
        assert item.origin is RelationshipOrigin.LIBRARY_RECORD
        assert item.symmetric is False
        assert item.serving is None
        assert (item.history, item.source_interval, item.offset) == ((), None, None)
        assert item.contradicted is False
        assert item.endpoint_states == {"out": EndpointState.ACTIVE, "src": EndpointState.ACTIVE}

    def test_the_evidence_names_the_operation_and_who_recorded_it(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src", operation="audio_mix"))

        (item,) = project(library, "src").items
        (evidence,) = item.evidence

        assert evidence.kind is EvidenceKind.LIBRARY_DERIVATION
        assert evidence.provider == "media_library"
        assert evidence.detail["operation"] == "audio_mix"
        assert evidence.detail["version"] == 1
        assert evidence.detail["fingerprint"] == "fp-out"

    def test_times_come_from_the_records_so_the_same_query_gives_the_same_answer(self) -> None:
        later = NOW + timedelta(days=3)
        library = FakeAssetLookup(
            original("src"), derivative("out", "src", created_at=later, updated_at=later)
        )

        first, second = project(library, "src"), project(library, "src")

        assert first == second
        assert first.items[0].created_at == later
        assert first.items[0].evidence[0].observed_at == later

    def test_an_item_id_is_deterministic_and_differs_by_type_and_direction(self) -> None:
        library = FakeAssetLookup(original("a"), derivative("b", "a"))

        (item,) = project(library, "a").items

        assert len(item.relationship_id) == 32
        assert item.relationship_id == project(library, "b").items[0].relationship_id

    def test_items_are_in_relationship_id_order(self) -> None:
        library = FakeAssetLookup(
            original("src"), *(derivative(f"out{i}", "src") for i in range(12))
        )

        ids = [i.relationship_id for i in project(library, "src").items]

        assert ids == sorted(ids)
        assert len(set(ids)) == 12

    def test_a_missing_source_fabricates_nothing(self) -> None:
        library = FakeAssetLookup(derivative("out", "gone"))

        assert project(library, "out").items == ()

    def test_an_asset_with_no_lineage_has_none(self) -> None:
        assert project(FakeAssetLookup(original("lone")), "lone").items == ()


class TestWhatIsNotMediaLineage:
    @pytest.mark.parametrize(
        "artifact",
        [
            derivative("doc", "src", operation="media_inspection", media_type=MediaType.OTHER),
            derivative("doc", "src", operation="transcription", media_type=MediaType.OTHER),
            derivative("doc", "src", operation="content_intelligence", media_type=MediaType.OTHER),
            derivative(
                "thumb",
                "src",
                operation="thumbnail",
                media_type=MediaType.IMAGE,
                role=AssetRole.PREVIEW,
            ),
            derivative("wav", "src", operation="audio_extraction", media_type=MediaType.AUDIO),
        ],
        ids=["inspection", "transcript", "analysis", "thumbnail", "excluded operation"],
    )
    def test_documents_previews_and_intermediates_are_not_reported(
        self, artifact: MediaAssetDto
    ) -> None:
        library = FakeAssetLookup(original("src"), artifact, derivative("real", "src"))

        lineage = project(library, "src")

        assert pairs(lineage) == {(DERIVED, "real", "src")}

    def test_a_document_is_not_lineage_even_with_a_proxy_record_in_its_metadata(self) -> None:
        doc = proxy("doc", "src", media_type=MediaType.OTHER)

        assert project(FakeAssetLookup(original("src"), doc), "src").items == ()

    def test_the_exclusion_list_is_configurable(self) -> None:
        library = FakeAssetLookup(
            original("src"), derivative("wav", "src", operation="audio_extraction")
        )

        shown = project(library, "src", settings=LineageSettings(excluded_operations=frozenset()))

        assert pairs(shown) == {(DERIVED, "wav", "src")}

    def test_from_the_excluded_asset_itself_nothing_is_reported_either(self) -> None:
        library = FakeAssetLookup(
            original("src"), derivative("wav", "src", operation="audio_extraction")
        )

        assert project(library, "wav").items == ()


class TestProxies:
    def test_a_proxy_whose_provenance_and_derivation_agree_is_proxy_of(self) -> None:
        library = FakeAssetLookup(original("src"), proxy("prx", "src"))

        lineage = project(library, "src")

        assert pairs(lineage) == {(PROXY, "prx", "src")}
        (item,) = lineage.items
        assert item.origin is RelationshipOrigin.PROXY_RECORD
        assert [e.kind for e in item.evidence] == [
            EvidenceKind.LIBRARY_DERIVATION,
            EvidenceKind.PROXY_PROVENANCE,
        ]
        assert item.evidence[1].provider == "proxy_manager"
        assert item.evidence[1].detail["profile"] == "analysis"
        assert item.evidence[1].detail["verdict"] == "valid"
        assert item.evidence[1].detail["evidence"] == "packets"

    def test_a_proxy_is_not_also_reported_as_a_derivative(self) -> None:
        library = FakeAssetLookup(original("src"), proxy("prx", "src"))

        assert {i.type for i in project(library, "src").items} == {PROXY}

    def test_provenance_that_names_another_source_is_not_trusted(self) -> None:
        liar = derivative(
            "prx",
            "src",
            operation="proxy",
            role=AssetRole.PREVIEW,
            metadata=proxy("x", "someone-else").metadata,
        )

        assert project(FakeAssetLookup(original("src"), liar), "src").items == ()

    def test_provenance_that_disagrees_does_not_fall_back_to_derived_from(self) -> None:
        liar = derivative(
            "prx",
            "src",
            operation="proxy",
            role=AssetRole.DERIVED,
            metadata=proxy("x", "someone-else").metadata,
        )

        assert project(FakeAssetLookup(original("src"), liar), "src").items == ()

    def test_a_proxy_operation_without_provenance_is_only_a_preview(self) -> None:
        bare = derivative("prx", "src", operation="proxy", role=AssetRole.PREVIEW)

        assert project(FakeAssetLookup(original("src"), bare), "src").items == ()

    def test_a_malformed_provenance_record_is_ignored(self) -> None:
        metadata = proxy_metadata("src")
        del metadata["proxy"]["profile"]
        broken = derivative(
            "prx", "src", operation="proxy", role=AssetRole.PREVIEW, metadata=metadata
        )

        assert project(FakeAssetLookup(original("src"), broken), "src").items == ()

    def test_the_item_is_the_same_from_the_proxy_and_from_the_source(self) -> None:
        library = FakeAssetLookup(original("src"), proxy("prx", "src"))

        assert project(library, "prx").items == project(library, "src").items

    @pytest.mark.parametrize(
        ("served", "expected"),
        [("prx", True), ("other-proxy", False), (None, False)],
    )
    def test_serving_says_whether_the_slot_serves_this_proxy_now(
        self, served: str | None, expected: bool
    ) -> None:
        catalog = FakeProxyCatalog()
        if served:
            catalog.served[("src", "analysis")] = served
        library = FakeAssetLookup(original("src"), proxy("prx", "src"))

        (item,) = project(library, "src", proxies=catalog).items

        assert item.serving is expected

    def test_serving_is_unknown_without_the_catalog(self) -> None:
        library = FakeAssetLookup(original("src"), proxy("prx", "src"))

        (item,) = project(library, "src", proxies=None).items

        assert item.serving is None

    def test_a_replaced_proxy_is_not_stale_the_old_one_is_just_not_served(self) -> None:
        catalog = FakeProxyCatalog()
        library = FakeAssetLookup(original("src"), proxy("old", "src"), proxy("new", "src"))
        catalog.served[("src", "analysis")] = "new"

        lineage = project(library, "src", proxies=catalog)

        assert {i.source_asset_id: i.serving for i in lineage.items} == {"old": False, "new": True}

    def test_a_deleted_proxy_leaves_nothing_behind(self) -> None:
        library = FakeAssetLookup(original("src"), proxy("old", "src"), proxy("new", "src"))
        library.remove("old")

        assert pairs(project(library, "src")) == {(PROXY, "new", "src")}

    def test_the_catalog_is_asked_once_per_slot(self) -> None:
        catalog = FakeProxyCatalog()
        library = FakeAssetLookup(
            original("src"),
            *(proxy(f"p{i}", "src") for i in range(5)),
            proxy("edit", "src", profile="edit"),
        )

        project(library, "src", proxies=catalog)

        assert sorted(catalog.finds) == [("src", "analysis"), ("src", "edit")]

    def test_the_proxy_end_sees_its_own_serving_state(self) -> None:
        catalog = FakeProxyCatalog()
        catalog.served[("src", "analysis")] = "prx"
        library = FakeAssetLookup(original("src"), proxy("prx", "src"))

        (item,) = project(library, "prx", proxies=catalog).items

        assert item.serving is True


class TestExactDuplicates:
    def test_a_non_original_finds_the_original_with_its_bytes(self) -> None:
        library = FakeAssetLookup(
            original("o"),
            original("src"),
            derivative("copy", "src", checksum=checksum_of("o")),
        )

        lineage = project(library, "copy", scope=frozenset({DUPLICATE}))

        assert pairs(lineage) == {(DUPLICATE, "copy", "o")}
        (item,) = lineage.items
        assert item.evidence[0].kind is EvidenceKind.CHECKSUM_MATCH
        assert item.evidence[0].detail["sha256"] == checksum_of("o")
        assert item.origin is RelationshipOrigin.LIBRARY_RECORD

    def test_forward_discovery_costs_one_checksum_lookup_and_no_scan(self) -> None:
        library = FakeAssetLookup(
            original("o"), derivative("copy", "src", checksum=checksum_of("o")), original("src")
        )

        project(library, "copy", scope=frozenset({DUPLICATE}))

        assert library.checksum_lookups == [checksum_of("o")]
        assert library.searches == []

    def test_a_non_original_that_matches_nothing_has_no_duplicate(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"))

        assert project(library, "out", scope=frozenset({DUPLICATE})).items == ()

    def test_an_original_finds_duplicates_among_its_own_derivatives(self) -> None:
        library = FakeAssetLookup(
            original("o"),
            derivative("same", "o", checksum=checksum_of("o")),
            derivative("different", "o"),
        )

        lineage = project(library, "o", scope=frozenset({DUPLICATE}))

        assert pairs(lineage) == {(DUPLICATE, "same", "o")}

    def test_an_original_does_not_find_a_duplicate_derived_from_another_source(self) -> None:
        """The library cannot list every asset sharing an original's bytes: this is the limit."""
        library = FakeAssetLookup(
            original("o"),
            original("other"),
            derivative("far", "other", checksum=checksum_of("o")),
        )

        assert project(library, "o", scope=frozenset({DUPLICATE})).items == ()
        assert pairs(project(library, "far", scope=frozenset({DUPLICATE}))) == {
            (DUPLICATE, "far", "o")
        }

    def test_a_non_original_is_not_a_duplicate_target(self) -> None:
        library = FakeAssetLookup(
            original("src"),
            derivative("mid", "src"),
            derivative("leaf", "mid", checksum=checksum_of("mid")),
        )

        assert project(library, "mid", scope=frozenset({DUPLICATE})).items == ()

    def test_two_originals_never_duplicate_each_other(self) -> None:
        library = FakeAssetLookup(original("a"), original("b"))

        assert project(library, "a").items == ()
        assert project(library, "b").items == ()
        assert library.checksum_lookups == []

    def test_a_duplicate_derivative_is_also_reported_as_derived_both_facts_hold(self) -> None:
        library = FakeAssetLookup(original("o"), derivative("same", "o", checksum=checksum_of("o")))

        assert pairs(project(library, "o")) == {(DUPLICATE, "same", "o"), (DERIVED, "same", "o")}

    def test_an_archived_original_is_hidden_unless_asked_for(self) -> None:
        library = FakeAssetLookup(
            original("o", status=AssetStatus.ARCHIVED),
            derivative("copy", "src", checksum=checksum_of("o")),
            original("src"),
        )
        scope = frozenset({DUPLICATE})

        assert project(library, "copy", scope=scope).items == ()
        shown = project(library, "copy", scope=scope, include_archived=True)
        assert pairs(shown) == {(DUPLICATE, "copy", "o")}
        assert shown.items[0].endpoint_states["o"] is EndpointState.ARCHIVED


class TestScopeAndWork:
    def test_an_empty_scope_reads_nothing(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"))
        library.gets.clear()

        lineage = project(library, "src", scope=frozenset())

        assert lineage.items == ()
        assert (library.searches, library.checksum_lookups) == ([], [])

    def test_a_scope_of_duplicates_does_not_scan_a_non_originals_derivatives(self) -> None:
        library = FakeAssetLookup(
            original("src"), derivative("mid", "src"), derivative("leaf", "mid")
        )

        project(library, "mid", scope=frozenset({DUPLICATE}))

        assert library.searches == []

    def test_a_scope_of_proxies_only_returns_proxies(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"), proxy("prx", "src"))

        assert pairs(project(library, "src", scope=frozenset({PROXY}))) == {(PROXY, "prx", "src")}

    def test_a_scope_of_derivations_leaves_proxies_out(self) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"), proxy("prx", "src"))

        assert pairs(project(library, "src", scope=frozenset({DERIVED}))) == {
            (DERIVED, "out", "src")
        }


class TestArchivedDerivatives:
    def test_an_archived_derivative_is_hidden_unless_asked_for(self) -> None:
        library = FakeAssetLookup(
            original("src"),
            derivative("old", "src", status=AssetStatus.ARCHIVED),
            derivative("new", "src"),
        )

        assert pairs(project(library, "src")) == {(DERIVED, "new", "src")}
        shown = project(library, "src", include_archived=True)
        assert pairs(shown) == {(DERIVED, "new", "src"), (DERIVED, "old", "src")}
        old = next(i for i in shown.items if i.source_asset_id == "old")
        assert old.endpoint_states["old"] is EndpointState.ARCHIVED

    def test_an_archived_source_is_hidden_from_its_derivative(self) -> None:
        library = FakeAssetLookup(
            original("src", status=AssetStatus.ARCHIVED), derivative("out", "src")
        )

        assert project(library, "out").items == ()
        assert len(project(library, "out", include_archived=True).items) == 1

    def test_the_queried_asset_itself_may_be_archived(self) -> None:
        library = FakeAssetLookup(
            original("src"), derivative("out", "src", status=AssetStatus.ARCHIVED)
        )

        assert len(project(library, "out").items) == 1


class TestBounds:
    def many(self, count: int) -> FakeAssetLookup:
        return FakeAssetLookup(
            original("src"), *(derivative(f"d{i:05d}", "src") for i in range(count))
        )

    def test_more_than_one_library_page_is_read_completely_and_in_order(self) -> None:
        library = self.many(450)

        lineage = project(library, "src")

        assert len(lineage.items) == 450
        assert lineage.truncated is False
        assert len(library.searches) == 3  # 200 + 200 + 50
        assert [q.page for q in library.searches] == [1, 2, 3]
        assert {q.page_size for q in library.searches} == {200}
        ids = [i.relationship_id for i in lineage.items]
        assert ids == sorted(ids)

    def test_exactly_one_full_page_needs_no_second_read(self) -> None:
        library = self.many(200)

        lineage = project(library, "src")

        assert (len(lineage.items), lineage.truncated, len(library.searches)) == (200, False, 1)

    def test_beyond_the_cap_the_answer_says_so_instead_of_silently_stopping(self) -> None:
        library = self.many(1250)

        lineage = project(library, "src")

        assert lineage.truncated is True
        assert len(lineage.items) == 1000  # 5 pages of 200
        assert len(library.searches) == 5

    def test_exactly_the_cap_is_not_truncated(self) -> None:
        library = self.many(1000)

        lineage = project(library, "src")

        assert (len(lineage.items), lineage.truncated) == (1000, False)

    def test_the_cap_is_a_setting(self) -> None:
        library = self.many(450)

        lineage = project(library, "src", settings=LineageSettings(max_derivative_pages=1))

        assert (len(lineage.items), lineage.truncated, len(library.searches)) == (200, True, 1)

    def test_the_same_query_returns_the_same_items_in_the_same_order(self) -> None:
        library = self.many(450)

        assert project(library, "src") == project(library, "src")

    def test_the_scan_reads_derivatives_oldest_first_with_all_roles_and_statuses(self) -> None:
        library = self.many(3)

        project(library, "src")

        query = library.searches[0]
        assert query.source_asset_id == "src"
        assert (query.roles, query.statuses) == (frozenset(), frozenset())
