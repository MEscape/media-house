"""Contract tests: every MediaAssetRepository implementation must behave identically."""

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from media_house.modules.media_library.domain.errors import DuplicateMedia
from media_house.modules.media_library.domain.media_asset import MediaAsset, MediaFileInfo
from media_house.modules.media_library.domain.repository import MediaAssetRepository
from media_house.modules.media_library.domain.values import (
    MediaSource,
    Checksum,
    MediaAssetId,
    MediaName,
    MediaType,
    SourceType,
    StorageKey,
    Tag,
)
from media_house.modules.media_library.infrastructure.sqlite_asset_repository import (
    SqliteMediaAssetRepository,
)
from media_house.modules.media_library.infrastructure.sqlite_database import SqliteMediaDatabase
from tests.support.media_fakes import InMemoryMediaStore

pytestmark = pytest.mark.integration

Factory = Callable[[], MediaAssetRepository]

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["in_memory", "sqlite"])
def make_repository(request: pytest.FixtureRequest, tmp_path: Path) -> Factory:
    if request.param == "in_memory":
        shared = InMemoryMediaStore()
        return lambda: shared

    db_path = tmp_path / "media_library.db"
    return lambda: SqliteMediaAssetRepository(SqliteMediaDatabase(db_path))


def new_asset(name: str, checksum: str) -> MediaAsset:
    return MediaAsset.import_original(
        file=MediaFileInfo(
            storage_key=StorageKey("image/ab/cd/abc.png"),
            checksum=Checksum(checksum),
            file_size=100,
            media_type=MediaType.IMAGE,
            mime_type="image/png",
            extension=".png",
        ),
        original_filename=name,
        display_name=MediaName.of(name),
        source=MediaSource.of(SourceType.LOCAL_FILE),
        now=T0,
    )


def test_add_get_roundtrip(make_repository: Factory) -> None:
    repository = make_repository()
    asset = new_asset(
        "photo.png", "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    )
    repository.add(asset)

    loaded = repository.get(asset.id)
    assert loaded is not None
    assert loaded.id == asset.id
    assert loaded.original_filename == "photo.png"
    assert (
        loaded.file.checksum.value
        == "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    )
    assert loaded.created_at == T0
    assert loaded.pull_events() == []


def test_get_unknown_returns_none(make_repository: Factory) -> None:
    assert make_repository().get(MediaAssetId("missing")) is None


def test_find_original_by_checksum(make_repository: Factory) -> None:
    repository = make_repository()
    asset = new_asset(
        "test.png", "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    )
    repository.add(asset)

    loaded = repository.find_original_by_checksum(
        Checksum("abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")
    )
    assert loaded is not None
    assert loaded.id == asset.id

    missing = repository.find_original_by_checksum(
        Checksum("0000000000000000000000000000000000000000000000000000000000000000")
    )
    assert missing is None


def test_add_duplicate_checksum_raises_error(make_repository: Factory) -> None:
    repository = make_repository()
    asset1 = new_asset(
        "test1.png", "1111111111111111111111111111111111111111111111111111111111111111"
    )
    repository.add(asset1)

    asset2 = new_asset(
        "test2.png", "1111111111111111111111111111111111111111111111111111111111111111"
    )
    with pytest.raises(DuplicateMedia):
        repository.add(asset2)
