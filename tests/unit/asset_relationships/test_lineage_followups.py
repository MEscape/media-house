"""The read-path follow-ups: quiet logs, reported findings, a failing proxy catalog, paging checks.

R-A routine skips are debug only. R-B ``source_missing`` and ``untrusted_provenance`` are on the
page. R-C a failing proxy catalog degrades ``serving`` instead of failing the query. R-D a changed
derivative total between pages is reported; an unchanged total is NOT a promise of a stable
snapshot.
"""

import logging

import pytest

from media_house.modules.asset_relationships.application import cursor as page_cursor
from media_house.modules.asset_relationships.application.commands import RelationshipQuery
from media_house.modules.asset_relationships.application.dto import RelationshipPage
from media_house.modules.asset_relationships.application.lineage_projection import (
    LineageProjection,
)
from media_house.modules.asset_relationships.application.query_relationships import (
    MAX_FINDINGS,
    QueryRelationships,
)
from media_house.modules.asset_relationships.domain.lineage import FindingKind, LineageFinding
from media_house.modules.asset_relationships.domain.values import Limitation, RelationshipType
from media_house.modules.media_library.application.contracts import AssetRole, MediaType
from media_house.shared.errors import Ok, PersistenceError
from tests.support.relationship_fakes import (
    FakeAssetLookup,
    FakeProxyCatalog,
    InMemoryRelationshipRepository,
    derivative,
    original,
    proxy,
    proxy_metadata,
)

DERIVED = RelationshipType.DERIVED_FROM
PROXY = RelationshipType.PROXY_OF
LINEAGE_LOGGER = "media_house.modules.asset_relationships.application.lineage_projection"


def query(library: FakeAssetLookup, proxies: FakeProxyCatalog | None = None) -> QueryRelationships:
    return QueryRelationships(
        InMemoryRelationshipRepository(), library, LineageProjection(library, proxies)
    )


def ask(service: QueryRelationships, asset: str, **options: object) -> RelationshipPage:
    result = service.relationships_of(asset, RelationshipQuery(**options))  # type: ignore[arg-type]
    assert isinstance(result, Ok), result
    return result.value


def untrusted(asset_id: str, source: str = "src") -> object:
    """A proxy whose provenance names another source than its derivation record does."""
    return derivative(
        asset_id,
        source,
        operation="proxy",
        role=AssetRole.PREVIEW,
        metadata=proxy_metadata("someone-else"),
    )


class TestQuietLogs:
    def test_routine_skips_are_debug_only(self, caplog: pytest.LogCaptureFixture) -> None:
        library = FakeAssetLookup(
            original("src"),
            derivative("doc", "src", operation="media_inspection", media_type=MediaType.OTHER),
            derivative(
                "thumb",
                "src",
                operation="thumbnail",
                media_type=MediaType.IMAGE,
                role=AssetRole.PREVIEW,
            ),
            derivative("wav", "src", operation="audio_extraction", media_type=MediaType.AUDIO),
        )

        with caplog.at_level(logging.DEBUG, logger=LINEAGE_LOGGER):
            ask(query(library), "src")

        levels = {r.levelno for r in caplog.records if r.name == LINEAGE_LOGGER}
        assert levels == {logging.DEBUG}

    def test_nothing_is_logged_above_debug_for_an_ordinary_query(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        library = FakeAssetLookup(original("src"), derivative("out", "src"), proxy("prx", "src"))

        with caplog.at_level(logging.INFO, logger=LINEAGE_LOGGER):
            ask(query(library, FakeProxyCatalog()), "src")

        assert [r for r in caplog.records if r.name == LINEAGE_LOGGER] == []

    def test_an_untrusted_provenance_is_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        library = FakeAssetLookup(original("src"), untrusted("prx"))  # type: ignore[arg-type]

        with caplog.at_level(logging.DEBUG, logger=LINEAGE_LOGGER):
            ask(query(library), "src")

        records = [r for r in caplog.records if r.name == LINEAGE_LOGGER]
        assert [r.levelno for r in records] == [logging.WARNING]

    def test_a_truncated_scan_is_info(self, caplog: pytest.LogCaptureFixture) -> None:
        library = FakeAssetLookup(
            original("src"), *(derivative(f"d{i:05d}", "src") for i in range(1001))
        )

        with caplog.at_level(logging.INFO, logger=LINEAGE_LOGGER):
            ask(query(library), "src", types=frozenset({DERIVED}))

        records = [r for r in caplog.records if r.name == LINEAGE_LOGGER]
        assert [r.levelno for r in records] == [logging.INFO]


class TestFindings:
    def test_a_proxy_rejected_as_untrusted_is_reported_not_just_logged(self) -> None:
        library = FakeAssetLookup(original("src"), untrusted("prx"))  # type: ignore[arg-type]

        page = ask(query(library), "src")

        assert page.items == ()
        assert page.findings == (LineageFinding(FindingKind.UNTRUSTED_PROVENANCE, "prx"),)
        assert page.findings_omitted == 0

    def test_a_derivative_whose_source_is_gone_is_reported_from_its_own_end(self) -> None:
        library = FakeAssetLookup(derivative("out", "gone"))

        page = ask(query(library), "out")

        assert page.items == ()
        assert page.findings == (LineageFinding(FindingKind.SOURCE_MISSING, "out"),)

    def test_a_trusted_proxy_and_a_plain_derivative_report_no_finding(self) -> None:
        library = FakeAssetLookup(original("src"), proxy("prx", "src"), derivative("out", "src"))

        assert ask(query(library), "src").findings == ()

    def test_routine_skips_are_not_findings(self) -> None:
        library = FakeAssetLookup(
            original("src"),
            derivative("doc", "src", media_type=MediaType.OTHER),
            derivative("wav", "src", operation="audio_extraction", media_type=MediaType.AUDIO),
        )

        assert ask(query(library), "src").findings == ()

    def test_at_most_twenty_are_listed_and_the_rest_are_counted(self) -> None:
        library = FakeAssetLookup(
            original("src"),
            *(untrusted(f"p{i:02d}") for i in range(25)),  # type: ignore[arg-type]
        )

        page = ask(query(library), "src")

        assert len(page.findings) == MAX_FINDINGS == 20
        assert page.findings_omitted == 5
        assert [f.asset_id for f in page.findings] == sorted(f.asset_id for f in page.findings)
        assert page.findings[0].asset_id == "p00"

    def test_findings_are_on_every_page_of_the_same_answer(self) -> None:
        library = FakeAssetLookup(
            original("src"),
            derivative("out", "src"),
            derivative("out2", "src"),
            untrusted("prx"),  # type: ignore[arg-type]
        )
        service = query(library)

        first = ask(service, "src", page_size=1)
        later = ask(service, "src", page_size=1, cursor=first.next_cursor)

        assert (
            first.findings
            == later.findings
            == (LineageFinding(FindingKind.UNTRUSTED_PROVENANCE, "prx"),)
        )

    def test_the_finding_kinds_are_stable_text(self) -> None:
        assert [k.value for k in FindingKind] == ["source_missing", "untrusted_provenance"]

    def test_an_archived_untrusted_derivative_is_not_reported_unless_archived_is_asked_for(
        self,
    ) -> None:
        from media_house.modules.media_library.application.contracts import AssetStatus

        library = FakeAssetLookup(
            original("src"),
            derivative(
                "prx",
                "src",
                operation="proxy",
                role=AssetRole.PREVIEW,
                metadata=proxy_metadata("someone-else"),
                status=AssetStatus.ARCHIVED,
            ),
        )
        service = query(library)

        assert ask(service, "src").findings == ()
        assert len(ask(service, "src", include_archived=True).findings) == 1


class FailingCatalog(FakeProxyCatalog):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def find(self, source_asset_id: str, profile: str):  # type: ignore[no-untyped-def]
        self.finds.append((source_asset_id, profile))
        raise self._error


class TestProxyCatalogFailure:
    def library(self) -> FakeAssetLookup:
        return FakeAssetLookup(
            original("src"), proxy("p1", "src"), proxy("p2", "src"), derivative("out", "src")
        )

    def test_a_failing_catalog_degrades_serving_and_the_query_still_answers(self) -> None:
        catalog = FailingCatalog(PersistenceError("proxy database unavailable"))

        page = ask(query(self.library(), catalog), "src")

        assert {i.type for i in page.items} == {PROXY, DERIVED}
        assert [i.serving for i in page.items if i.type is PROXY] == [None, None]
        assert Limitation.PROXY_STATE_UNAVAILABLE in page.limitations

    def test_the_catalog_is_asked_once_per_slot_even_when_it_fails(self) -> None:
        catalog = FailingCatalog(PersistenceError("down"))

        ask(query(self.library(), catalog), "src")

        assert catalog.finds == [("src", "analysis")]

    def test_a_working_catalog_has_no_such_limitation(self) -> None:
        catalog = FakeProxyCatalog()
        catalog.served[("src", "analysis")] = "p1"

        page = ask(query(self.library(), catalog), "src")

        assert Limitation.PROXY_STATE_UNAVAILABLE not in page.limitations
        assert {i.source_asset_id: i.serving for i in page.items if i.type is PROXY} == {
            "p1": True,
            "p2": False,
        }

    def test_without_a_catalog_serving_is_unknown_but_that_is_not_a_failure(self) -> None:
        page = ask(query(self.library(), None), "src")

        assert [i.serving for i in page.items if i.type is PROXY] == [None, None]
        assert Limitation.PROXY_STATE_UNAVAILABLE not in page.limitations

    def test_a_defect_in_the_catalog_is_not_swallowed(self) -> None:
        catalog = FailingCatalog(RuntimeError("bug"))

        with pytest.raises(RuntimeError, match="bug"):
            ask(query(self.library(), catalog), "src")

    def test_a_query_that_asks_for_no_proxies_does_not_touch_the_catalog(self) -> None:
        catalog = FailingCatalog(PersistenceError("down"))

        page = ask(query(self.library(), catalog), "src", types=frozenset({DERIVED}))

        assert catalog.finds == []
        assert Limitation.PROXY_STATE_UNAVAILABLE not in page.limitations


class TestCursor:
    def test_a_cursor_carries_the_id_and_the_total(self) -> None:
        token = page_cursor.encode("abc123", 42)

        assert page_cursor.decode(token) == ("abc123", 42)

    def test_without_a_total_the_cursor_is_just_the_id(self) -> None:
        assert page_cursor.encode("abc123", None) == "abc123"
        assert page_cursor.decode("abc123") == ("abc123", None)

    @pytest.mark.parametrize("junk", ["abc:", "abc:x", "abc:-1", "abc:1.5", ":", ""])
    def test_a_malformed_total_falls_back_to_the_whole_text_as_the_id(self, junk: str) -> None:
        assert page_cursor.decode(junk) == (junk, None)

    def test_no_cursor_means_the_first_page(self) -> None:
        assert page_cursor.decode(None) == (None, None)


class TestPagingChecks:
    def library(self, count: int = 5) -> FakeAssetLookup:
        return FakeAssetLookup(
            original("src"), *(derivative(f"d{i:03d}", "src") for i in range(count))
        )

    def test_the_cursor_of_a_lineage_page_carries_the_total_the_scan_saw(self) -> None:
        first = ask(query(self.library(5)), "src", page_size=2)

        assert first.next_cursor is not None
        assert first.next_cursor.endswith(":5")

    def test_a_claims_only_query_keeps_the_plain_id_cursor(self) -> None:
        library = self.library(5)
        service = query(library)

        page = ask(service, "src", page_size=2, include_projected=False)

        assert page.next_cursor is None  # no claims: nothing to page
        assert library.searches == []

    def test_an_unchanged_total_reports_nothing(self) -> None:
        service = query(self.library(5))
        first = ask(service, "src", page_size=2)

        second = ask(service, "src", page_size=2, cursor=first.next_cursor)

        assert Limitation.LINEAGE_CHANGED_WHILE_PAGING not in second.limitations

    def test_a_changed_total_is_reported_on_the_next_page(self) -> None:
        library = self.library(5)
        service = query(library)
        first = ask(service, "src", page_size=2)
        library.add(derivative("d999", "src"))  # a derivative appeared between the pages

        second = ask(service, "src", page_size=2, cursor=first.next_cursor)

        assert Limitation.LINEAGE_CHANGED_WHILE_PAGING in second.limitations

    def test_a_deletion_between_pages_is_reported_too(self) -> None:
        library = self.library(5)
        service = query(library)
        first = ask(service, "src", page_size=2)
        library.remove("d004")

        second = ask(service, "src", page_size=2, cursor=first.next_cursor)

        assert Limitation.LINEAGE_CHANGED_WHILE_PAGING in second.limitations

    def test_an_insertion_plus_a_deletion_keeps_the_total_and_is_not_detected(self) -> None:
        """The check detects SOME concurrent changes only: an unchanged total is no promise.

        A derivative was added and another removed between the pages, so the pages may not fit
        together, yet the total is the same and nothing is reported. A caller that needs certainty
        must not read this limitation's absence as a stable snapshot.
        """
        library = self.library(5)
        service = query(library)
        first = ask(service, "src", page_size=2)
        library.add(derivative("d000a", "src"))
        library.remove("d004")

        second = ask(service, "src", page_size=2, cursor=first.next_cursor)

        assert Limitation.LINEAGE_CHANGED_WHILE_PAGING not in second.limitations

    def test_the_first_page_never_reports_a_change(self) -> None:
        assert (
            Limitation.LINEAGE_CHANGED_WHILE_PAGING
            not in ask(query(self.library(5)), "src", page_size=2).limitations
        )

    def test_a_plain_id_cursor_from_before_still_pages(self) -> None:
        library = self.library(5)
        service = query(library)
        first = ask(service, "src", page_size=2)
        assert first.next_cursor is not None
        plain = first.next_cursor.partition(":")[0]

        second = ask(service, "src", page_size=2, cursor=plain)

        assert second.items
        assert Limitation.LINEAGE_CHANGED_WHILE_PAGING not in second.limitations

    def test_paging_still_walks_every_item_once(self) -> None:
        service = query(self.library(7))
        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = ask(service, "src", page_size=3, cursor=cursor)
            seen += [i.relationship_id for i in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break

        assert len(seen) == len(set(seen)) == 7
        assert seen == sorted(seen)

    def test_the_total_counts_every_derivative_whatever_its_state_or_kind(self) -> None:
        library = FakeAssetLookup(
            original("src"),
            derivative("doc", "src", media_type=MediaType.OTHER),
            derivative("out", "src"),
            derivative("out2", "src"),
            derivative("wav", "src", operation="audio_extraction", media_type=MediaType.AUDIO),
        )

        page = ask(query(library), "src", page_size=1)

        assert page.next_cursor is not None
        assert page.next_cursor.endswith(":4")  # two reported, two set aside, all counted
