"""Recorded lineage projected from the REAL Media Library, inspection and Proxy Manager.

Nothing here is mocked at the contract: derivatives are registered through ``MediaLibrary``,
documents come from the real inspector, proxies from the proxy manager's own use case (scripted
encoder, real store, real provenance), and the answers come through the module's real wiring.
"""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.application.contracts import (
    EvidenceKind,
    EvidenceSubmission,
    Limitation,
    ProposeRelationshipCommand,
    RelationshipOrigin,
    RelationshipPage,
    RelationshipQuery,
    RelationshipType,
)
from media_house.modules.asset_relationships.application.dto import EndpointState, RelationshipDto
from media_house.modules.asset_relationships.application.lineage_projection import (
    LineageProjection,
    LineageSettings,
)
from media_house.modules.audio_intelligence.domain.values import EXTRACTION_OPERATION
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaQuery,
    MediaType,
)
from media_house.modules.proxy_manager.application.contracts import (
    METADATA_KEY,
    read_proxy_provenance,
)
from media_house.shared.errors import Err, NotFoundError, Ok
from tests.integration.asset_relationships.conftest import Env
from tests.support.proxy_fakes import source_facts

pytestmark = pytest.mark.integration

DUPLICATE = RelationshipType.EXACT_DUPLICATE_OF
PROXY = RelationshipType.PROXY_OF
DERIVED = RelationshipType.DERIVED_FROM
PARTIAL = (Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL,)
FACTS = source_facts()


def ask(env: Env, asset_id: str, **query: object) -> RelationshipPage:
    result = env.catalog.relationships_of(asset_id, RelationshipQuery(**query))  # type: ignore[arg-type]
    assert isinstance(result, Ok), result
    return result.value


def of_type(page: RelationshipPage, kind: RelationshipType) -> list[RelationshipDto]:
    return [i for i in page.items if i.type is kind]


def pairs(page: RelationshipPage) -> set[tuple[RelationshipType, str, str]]:
    return {(i.type, i.source_asset_id, i.target_asset_id) for i in page.items}


def derive(
    env: Env, source: str, path: Path, operation: str = "test_extraction", **config: int
) -> str:
    registered = env.library.register_derived(
        source,
        path,
        operation=operation,
        config={"n": 1, **config},
    )
    assert isinstance(registered, Ok), registered
    return registered.value.asset.id


def copy_of_bytes(env: Env, asset_id: str, name: str) -> Path:
    path = env.library.local_path(asset_id)
    assert isinstance(path, Ok)
    target = env.proxy.media_dir / name
    shutil.copy(path.value, target)
    return target


def stored_rows(env: Env) -> int:
    with closing(sqlite3.connect(env.paths.data_dir / "asset_relationships.db")) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0])


def clip(env: Env, which: str = "cfr") -> str:
    return env.import_media(getattr(env.samples, which)())


class TestDerivedFrom:
    def test_a_derived_asset_is_derived_from_its_source_seen_from_either_end(
        self, env: Env
    ) -> None:
        source = clip(env)
        derived = derive(env, source, env.samples.audio_only())

        expected = {(DERIVED, derived, source)}
        assert pairs(ask(env, source, types=frozenset({DERIVED}))) == expected
        assert pairs(ask(env, derived, types=frozenset({DERIVED}))) == expected

    def test_the_item_carries_the_library_record_as_evidence(self, env: Env) -> None:
        source = clip(env)
        derived = derive(env, source, env.samples.audio_only(), operation="test_extraction")

        (item,) = of_type(ask(env, source), DERIVED)
        asset = env.library.get(derived)
        assert isinstance(asset, Ok)
        assert asset.value.derivation is not None

        assert (item.stored, item.status, item.origin) == (
            False,
            None,
            RelationshipOrigin.LIBRARY_RECORD,
        )
        assert item.evidence[0].kind is EvidenceKind.LIBRARY_DERIVATION
        assert item.evidence[0].detail["operation"] == "test_extraction"
        assert item.evidence[0].detail["fingerprint"] == asset.value.derivation.fingerprint
        assert item.endpoint_states == {source: EndpointState.ACTIVE, derived: EndpointState.ACTIVE}


class TestWhatIsNotMediaLineage:
    def test_documents_thumbnails_and_excluded_operations_never_appear(self, env: Env) -> None:
        source = clip(env)
        env.proxy.inspect(source)  # a real inspection document (an OTHER asset)
        transcript = env.proxy.media_dir / "transcript.json"
        transcript.write_text(json.dumps({"document_type": "transcript", "words": []}), "utf-8")
        derive(env, source, transcript, operation="transcription")
        assert isinstance(env.library.ensure_thumbnail(source), Ok)  # a PREVIEW image
        derive(env, source, env.samples.audio_only(), operation=EXTRACTION_OPERATION)
        meaningful = derive(env, source, env.samples.audio_only(), operation="audio_mix", n=2)

        everything = env.library.search(
            MediaQuery(
                source_asset_id=source, roles=frozenset(), statuses=frozenset(), page_size=50
            )
        )
        kinds = {(a.media_type, a.role) for a in everything.items}
        assert (MediaType.OTHER, AssetRole.DERIVED) in kinds  # the documents exist in the library
        assert (MediaType.IMAGE, AssetRole.PREVIEW) in kinds  # and so does the thumbnail

        assert pairs(ask(env, source)) == {(DERIVED, meaningful, source)}

    def test_the_default_exclusion_is_the_real_operation_constant(self) -> None:
        assert LineageSettings().excluded_operations == frozenset({EXTRACTION_OPERATION})


class TestProxies:
    def make_proxies(self, env: Env) -> tuple[str, list[str]]:
        source = env.proxy.clip()
        use_case, _encoder, _probe = env.proxy.scripted(FACTS)
        first = env.proxy.ok(use_case, source, max_long_edge=480)
        return source, [first.proxy_asset_id]

    def test_a_real_proxy_is_proxy_of_with_its_provenance_and_is_served(self, env: Env) -> None:
        source, (proxy_id,) = self.make_proxies(env)

        page = ask(env, source)

        assert pairs(page) == {(PROXY, proxy_id, source)}
        (item,) = page.items
        assert item.origin is RelationshipOrigin.PROXY_RECORD
        assert [e.kind for e in item.evidence] == [
            EvidenceKind.LIBRARY_DERIVATION,
            EvidenceKind.PROXY_PROVENANCE,
        ]
        assert item.evidence[1].detail["profile"] == "analysis"
        assert item.serving is True
        assert of_type(page, DERIVED) == []  # a proxy is a proxy, not also a derivative

    def test_the_proxy_asset_itself_gives_the_same_item(self, env: Env) -> None:
        source, (proxy_id,) = self.make_proxies(env)

        assert ask(env, proxy_id).items == ask(env, source).items

    def test_provenance_and_derivation_really_agree_for_a_real_proxy(self, env: Env) -> None:
        source, (proxy_id,) = self.make_proxies(env)
        asset = env.library.get(proxy_id)

        assert isinstance(asset, Ok)
        provenance = read_proxy_provenance(asset.value.metadata)
        assert provenance is not None
        assert asset.value.derivation is not None
        assert provenance.source_asset_id == asset.value.derivation.source_asset_id == source
        assert asset.value.role is AssetRole.PREVIEW

    def test_replacing_a_proxy_moves_serving_and_stores_nothing(self, env: Env) -> None:
        source = env.proxy.clip()
        use_case, encoder, _ = env.proxy.scripted(FACTS)
        first = env.proxy.ok(use_case, source, max_long_edge=480)
        second = env.proxy.ok(use_case, source, max_long_edge=320)

        both = {i.source_asset_id: i.serving for i in ask(env, source).items}
        assert both == {first.proxy_asset_id: False, second.proxy_asset_id: True}

        again = env.proxy.ok(use_case, source, max_long_edge=480)  # re-activates the first
        assert again.reused and again.proxy_asset_id == first.proxy_asset_id
        flipped = {i.source_asset_id: i.serving for i in ask(env, source).items}
        assert flipped == {first.proxy_asset_id: True, second.proxy_asset_id: False}
        assert len(encoder.calls) == 2
        assert stored_rows(env) == 0  # nothing was ever copied into asset_relationships.db

    def test_two_profiles_are_two_slots(self, env: Env) -> None:
        source = env.proxy.clip()
        use_case, _encoder, _probe = env.proxy.scripted(FACTS)
        analysis = env.proxy.ok(use_case, source, "analysis", max_long_edge=480)
        edit = env.proxy.ok(use_case, source, "edit", max_long_edge=480)

        served = {i.source_asset_id: i.serving for i in ask(env, source).items}

        assert served == {analysis.proxy_asset_id: True, edit.proxy_asset_id: True}

    def test_removing_a_proxy_leaves_no_relationship_and_the_original_untouched(
        self, env: Env
    ) -> None:
        source, (proxy_id,) = self.make_proxies(env)
        before = env.library.get(source)

        removed = env.proxy.maintenance.remove(source, "analysis")

        assert isinstance(removed, Ok), removed
        assert of_type(ask(env, source), PROXY) == []
        assert isinstance(env.library.get(proxy_id), Err)
        assert env.library.get(source) == before

    def test_provenance_that_names_another_source_is_not_trusted(self, env: Env) -> None:
        source, (proxy_id,) = self.make_proxies(env)
        asset = env.library.get(proxy_id)
        assert isinstance(asset, Ok)
        record = asset.value.metadata[METADATA_KEY]
        assert isinstance(record, dict)
        record = {**record, "source_asset_id": "someone-elses-clip"}
        env.clock.advance(seconds=1)  # the library saves a change only when updated_at moves
        assert isinstance(env.library.update_metadata(proxy_id, {METADATA_KEY: record}), Ok)

        page = ask(env, source)

        assert page.items == ()  # not proxy_of, and not derived_from either

    def test_without_the_catalog_serving_is_unknown_not_guessed(self, env: Env) -> None:
        source, _ = self.make_proxies(env)
        asset = env.library.get(source)
        assert isinstance(asset, Ok)

        lineage = LineageProjection(env.library, None).project(
            asset.value, frozenset({PROXY}), include_archived=False
        )

        assert [i.serving for i in lineage.items] == [None]


class TestDeletion:
    def test_deleting_a_source_removes_its_derivatives_and_all_lineage(self, env: Env) -> None:
        source = clip(env)
        other = clip(env, "cfr_25")
        derived = derive(env, source, env.samples.audio_only())
        kept = derive(env, other, env.samples.audio_only(), n=2)

        assert isinstance(env.library.delete(source), Ok)

        for gone in (source, derived):
            result = env.catalog.relationships_of(gone)
            assert isinstance(result, Err)
            assert isinstance(result.error, NotFoundError)
        assert pairs(ask(env, other)) == {(DERIVED, kept, other)}

    def test_a_stored_claim_about_a_deleted_asset_reports_it_missing_not_lineage(
        self, env: Env
    ) -> None:
        first, second = clip(env), clip(env, "cfr_25")
        proposed = env.editor.propose(
            ProposeRelationshipCommand(
                RelationshipType.ALTERNATE_TAKE_OF,
                first,
                second,
                RelationshipOrigin.USER,
                (EvidenceSubmission(EvidenceKind.USER_ASSERTION, "user", "1"),),
            )
        )
        assert isinstance(proposed, Ok)
        assert isinstance(env.library.delete(second), Ok)

        page = ask(env, first)

        assert [(i.type, i.stored) for i in page.items] == [
            (RelationshipType.ALTERNATE_TAKE_OF, True)
        ]
        assert page.items[0].endpoint_states[second] is EndpointState.MISSING


class TestArchived:
    def test_an_archived_derivative_is_hidden_unless_asked_for(self, env: Env) -> None:
        source = clip(env)
        shelved = derive(env, source, env.samples.audio_only())
        env.clock.advance(seconds=1)
        assert isinstance(env.library.archive(shelved), Ok)

        assert ask(env, source, types=frozenset({DERIVED})).items == ()
        (item,) = ask(env, source, types=frozenset({DERIVED}), include_archived=True).items
        assert item.endpoint_states[shelved] is EndpointState.ARCHIVED


class TestExactDuplicates:
    @pytest.fixture
    def world(self, env: Env) -> dict[str, str]:
        original = clip(env, "cfr")
        other = clip(env, "cfr_25")
        return {
            "original": original,
            "other": other,
            # a derivative of the original that has the original's bytes
            "inside": derive(env, original, copy_of_bytes(env, original, "inside.mp4"), n=10),
            # a derivative of ANOTHER source that has the original's bytes
            "far": derive(env, other, copy_of_bytes(env, original, "far.mp4"), n=11),
        }

    def test_from_a_non_original_the_original_is_found_exactly(
        self, env: Env, world: dict[str, str]
    ) -> None:
        for duplicate in ("inside", "far"):
            page = ask(env, world[duplicate], types=frozenset({DUPLICATE}))

            assert pairs(page) == {(DUPLICATE, world[duplicate], world["original"])}
            assert page.limitations == ()
            assert page.items[0].evidence[0].kind is EvidenceKind.CHECKSUM_MATCH

    def test_from_the_original_only_its_own_derivatives_are_found_and_it_says_so(
        self, env: Env, world: dict[str, str]
    ) -> None:
        page = ask(env, world["original"], types=frozenset({DUPLICATE}))

        assert pairs(page) == {(DUPLICATE, world["inside"], world["original"])}
        assert page.limitations == PARTIAL  # "far" exists and is NOT here

    def test_an_original_without_any_duplicate_is_flagged_too(
        self, env: Env, world: dict[str, str]
    ) -> None:
        page = ask(env, world["other"], types=frozenset({DUPLICATE}))

        assert page.items == ()
        assert page.limitations == PARTIAL

    def test_two_originals_are_never_duplicates(self, env: Env, world: dict[str, str]) -> None:
        assert DUPLICATE not in {i.type for i in ask(env, world["other"]).items}
        assert DUPLICATE not in {
            i.type for i in ask(env, world["original"]).items if i.target_asset_id == world["other"]
        }

    def test_a_duplicate_derivative_is_also_a_derivative_both_facts_hold(
        self, env: Env, world: dict[str, str]
    ) -> None:
        page = ask(env, world["inside"])

        assert {(i.type, i.target_asset_id) for i in page.items} >= {
            (DUPLICATE, world["original"]),
            (DERIVED, world["original"]),
        }


class TestBounds:
    def test_more_than_one_library_page_is_complete_ordered_and_not_truncated(
        self, env: Env
    ) -> None:
        source = clip(env)
        audio = env.samples.audio_only()
        derived = {derive(env, source, audio, n=number) for number in range(205)}
        assert len(derived) == 205

        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = ask(env, source, types=frozenset({DERIVED}), page_size=200, cursor=cursor)
            assert page.truncated is False
            seen += [i.relationship_id for i in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break

        assert len(seen) == len(set(seen)) == 205
        assert seen == sorted(seen)
        assert {
            i.source_asset_id
            for i in ask(env, source, types=frozenset({DERIVED}), page_size=200).items
        } <= derived


class TestStoredAndRecordedTogether:
    def test_claims_and_lineage_are_told_apart_and_only_claims_are_stored(self, env: Env) -> None:
        source, other = clip(env), clip(env, "cfr_25")
        derived = derive(env, source, env.samples.audio_only())
        proposed = env.editor.propose(
            ProposeRelationshipCommand(
                RelationshipType.ALTERNATE_TAKE_OF,
                source,
                other,
                RelationshipOrigin.USER,
                (EvidenceSubmission(EvidenceKind.USER_ASSERTION, "user", "1"),),
            )
        )
        assert isinstance(proposed, Ok)

        page = ask(env, source)

        assert {(i.type, i.stored, i.origin) for i in page.items} == {
            (RelationshipType.ALTERNATE_TAKE_OF, True, RelationshipOrigin.USER),
            (DERIVED, False, RelationshipOrigin.LIBRARY_RECORD),
        }
        ids = [i.relationship_id for i in page.items]
        assert ids == sorted(ids) and len(set(ids)) == 2
        assert page == ask(env, source)  # deterministic
        assert stored_rows(env) == 1
        assert pairs(ask(env, derived, types=frozenset({DERIVED}))) == {(DERIVED, derived, source)}
