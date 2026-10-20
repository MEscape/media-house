"""``register_derived`` takes an optional role: callers that ignore it are unaffected."""

from pathlib import Path
from typing import Any

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    DerivedAssetResultDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.proxy_media import cfr_with_audio

pytestmark = pytest.mark.integration


@pytest.fixture
def library(tmp_path: Path) -> MediaLibrary:
    container = Container()
    container.register_instance(AppPaths, AppPaths.under_root(tmp_path / "app"))
    container.register_instance(Clock, FixedClock())
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, SubprocessRunner())
    MediaLibraryModule().register(container)
    return container.resolve(MediaLibrary)


def register(
    library: MediaLibrary, tmp_path: Path, operation: str, **kwargs: Any
) -> tuple[str, Result[DerivedAssetResultDto, MediaError]]:
    imported = library.import_file(cfr_with_audio(tmp_path / "source.mp4"))
    assert isinstance(imported, Ok)
    derivative = cfr_with_audio(tmp_path / f"{operation}.mp4", seconds=1)
    return imported.value.asset.id, library.register_derived(
        imported.value.asset.id,
        derivative,
        operation=operation,
        config={"k": operation},
        **kwargs,
    )


def test_a_caller_that_passes_no_role_still_gets_a_derived_asset(
    library: MediaLibrary, tmp_path: Path
) -> None:
    _, result = register(library, tmp_path, "legacy")

    assert isinstance(result, Ok) and result.value.asset.role is AssetRole.DERIVED
    assert result.value.asset.id in {a.id for a in library.search().items}  # default listing


def test_a_preview_role_is_stored_and_hidden_from_default_searches_only(
    library: MediaLibrary, tmp_path: Path
) -> None:
    source, result = register(library, tmp_path, "playback", role=AssetRole.PREVIEW)

    assert isinstance(result, Ok) and result.value.asset.role is AssetRole.PREVIEW
    proxy = result.value.asset.id
    assert proxy not in {a.id for a in library.search().items}
    assert proxy in {a.id for a in library.list_derived(source)}  # still a recorded derivative
    found = library.find_derived_asset(source, result.value.asset.derivation.fingerprint)  # type: ignore[union-attr]
    assert found is not None and found.id == proxy


def test_the_original_role_is_refused(library: MediaLibrary, tmp_path: Path) -> None:
    _, result = register(library, tmp_path, "bad", role=AssetRole.ORIGINAL)

    assert isinstance(result, Err)


def test_deleting_the_source_removes_its_preview_derivatives_too(
    library: MediaLibrary, tmp_path: Path
) -> None:
    source, result = register(library, tmp_path, "playback", role=AssetRole.PREVIEW)
    assert isinstance(result, Ok)

    assert isinstance(library.delete(source), Ok)

    assert isinstance(library.get(result.value.asset.id), Err)
