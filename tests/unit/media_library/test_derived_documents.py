"""The shared find-or-store of derived JSON documents (used by every analysis module)."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
)
from media_house.modules.media_library.application.dto import DerivationDto, DerivedAssetResultDto
from media_house.shared.errors import Err, NotFoundError, Ok, ValidationError
from media_house.shared.filesystem import AppPaths
from tests.support.asset_dto import make_asset


class FakeLibrary:
    """The part of the library the helper uses: documents keyed by (source, fingerprint)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.assets: dict[tuple[str, str], MediaAssetDto] = {}
        self.files: dict[str, Path] = {}
        self.deleted: list[str] = []
        self.registered = 0

    def fingerprint(
        self,
        source_asset_id: str,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
    ) -> Ok[str] | Err[NotFoundError]:
        if source_asset_id == "missing":
            return Err(NotFoundError("no such asset"))
        text = json.dumps([source_asset_id, operation, config, version], sort_keys=True)
        return Ok(hashlib.sha256(text.encode()).hexdigest())

    def find_derived_asset(self, source_asset_id: str, fingerprint: str) -> MediaAssetDto | None:
        return self.assets.get((source_asset_id, fingerprint))

    def local_path(self, asset_id: str) -> Ok[Path] | Err[NotFoundError]:
        path = self.files.get(asset_id)
        return Ok(path) if path is not None and path.exists() else Err(NotFoundError(asset_id))

    def register_derived(
        self,
        source_asset_id: str,
        path: Path,
        *,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
        display_name: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> Ok[DerivedAssetResultDto] | Err[ValidationError]:
        fingerprint = self.fingerprint(source_asset_id, operation, config, version)
        assert isinstance(fingerprint, Ok)
        existing = self.assets.get((source_asset_id, fingerprint.value))
        if existing is not None:  # like the real library: the identical derivative is reused
            return Ok(DerivedAssetResultDto(existing, created=False))
        self.registered += 1
        asset_id = f"derived-{self.registered}"
        stored = self._root / f"{asset_id}.json"
        stored.write_bytes(path.read_bytes())
        self.files[asset_id] = stored
        asset = replace(
            make_asset(asset_id),
            display_name=display_name or "",
            metadata=dict(metadata or {}),
            derivation=DerivationDto(source_asset_id, operation, version, dict(config), "f"),
        )
        self.assets[(source_asset_id, fingerprint.value)] = asset
        return Ok(DerivedAssetResultDto(asset, created=True))

    def delete(self, asset_id: str, *, include_derived: bool = True) -> Ok[None]:
        self.deleted.append(asset_id)
        self.assets = {k: v for k, v in self.assets.items() if v.id != asset_id}
        return Ok(None)


@pytest.fixture
def library(tmp_path: Path) -> FakeLibrary:
    return FakeLibrary(tmp_path)


@pytest.fixture
def documents(library: FakeLibrary, tmp_path: Path) -> DerivedDocuments:
    return DerivedDocuments(library, AppPaths.under_root(tmp_path / "app"))


SPEC = DocumentSpec("demo.document", 2, {"threshold": 0.5})
SOURCE = make_asset("source")


def store(documents: DerivedDocuments, text: str = '{"value": 1}') -> MediaAssetDto:
    stored = documents.store(
        SOURCE,
        text,
        filename="doc.json",
        spec=SPEC,
        display_name="demo",
        metadata={"count": 3},
    )
    assert isinstance(stored, Ok)
    return stored.value


def fingerprint_of(documents: DerivedDocuments) -> str:
    result = documents.fingerprint("source", SPEC)
    assert isinstance(result, Ok)
    return result.value


def parse(text: str) -> int:
    """Like every real parser: rejects with ``ValueError`` (or a domain error)."""
    value = json.loads(text).get("value")
    if not isinstance(value, int):
        raise ValueError("no value")
    return value


def test_a_stored_document_is_found_by_its_fingerprint_and_parsed(
    documents: DerivedDocuments,
) -> None:
    asset = store(documents)

    found = documents.load("source", fingerprint_of(documents), parse)

    assert found == (1, asset)


def test_the_asset_names_its_operation_source_and_metadata(documents: DerivedDocuments) -> None:
    asset = store(documents)

    assert asset.metadata == {
        "processing_type": "demo.document",
        "source_asset_id": "source",
        "count": 3,
    }
    assert asset.derivation is not None
    assert (asset.derivation.operation, asset.derivation.version) == ("demo.document", 2)


def test_a_different_version_or_setting_is_a_different_document(
    documents: DerivedDocuments,
) -> None:
    first = documents.fingerprint("source", SPEC)
    newer = documents.fingerprint("source", replace(SPEC, version=3))
    other = documents.fingerprint("source", replace(SPEC, config={"threshold": 0.6}))

    assert isinstance(first, Ok) and isinstance(newer, Ok) and isinstance(other, Ok)
    assert len({first.value, newer.value, other.value}) == 3


def test_nothing_stored_means_nothing_found(documents: DerivedDocuments) -> None:
    assert documents.load("source", fingerprint_of(documents), parse) is None


def test_an_unknown_source_is_reported_by_the_library_not_swallowed(
    documents: DerivedDocuments,
) -> None:
    assert isinstance(documents.fingerprint("missing", SPEC), Err)


def test_an_unparseable_document_is_discarded_so_a_fresh_one_can_take_its_place(
    documents: DerivedDocuments, library: FakeLibrary
) -> None:
    asset = store(documents)
    library.files[asset.id].write_text("{ not json", encoding="utf-8")

    assert documents.load("source", fingerprint_of(documents), parse) is None

    assert library.deleted == [asset.id]
    assert store(documents).id != asset.id  # registered again, not answered with the damaged one


def test_a_document_whose_file_is_gone_is_discarded(
    documents: DerivedDocuments, library: FakeLibrary
) -> None:
    asset = store(documents)
    library.files[asset.id].unlink()

    assert documents.load("source", fingerprint_of(documents), parse) is None
    assert library.deleted == [asset.id]


def test_a_parser_that_rejects_the_content_discards_it_too(
    documents: DerivedDocuments, library: FakeLibrary
) -> None:
    asset = store(documents, '{"other": 1}')

    assert documents.load("source", fingerprint_of(documents), parse) is None
    assert library.deleted == [asset.id]


def test_long_display_names_are_cut_to_what_the_library_accepts(
    documents: DerivedDocuments,
) -> None:
    stored = documents.store(
        SOURCE, "{}", filename="d.json", spec=SPEC, display_name="x" * 400, metadata={}
    )

    assert isinstance(stored, Ok) and len(stored.value.display_name) == 255


def test_storing_the_identical_document_twice_reuses_the_first(
    documents: DerivedDocuments, library: FakeLibrary
) -> None:
    first = store(documents)
    second = store(documents)

    assert second.id == first.id and library.registered == 1
