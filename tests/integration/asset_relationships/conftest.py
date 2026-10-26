"""A real Media Library, real inspection, the real Proxy Manager and the real module wiring.

FFmpeg makes the (tiny) media files. Proxies are made by ``proxy_manager``'s own use case over
the real library and store, with a scripted encoder (see
``tests/integration/proxy_manager/conftest.py``), so the provenance, role and replacement
behaviour under test are the production ones.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.application.contracts import (
    RelationshipCatalog,
    RelationshipEditor,
    RelationshipMaintenance,
)
from media_house.modules.asset_relationships.module import AssetRelationshipsModule
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.proxy_manager.conftest import Env as ProxyEnv
from tests.integration.proxy_manager.conftest import build_env as build_proxy_env
from tests.support.fakes import FixedClock
from tests.support.inspection_media import Samples


@dataclass
class Env:
    library: MediaLibrary
    catalog: RelationshipCatalog
    editor: RelationshipEditor
    maintenance: RelationshipMaintenance
    paths: AppPaths
    clock: FixedClock
    samples: Samples
    #: The proxy manager over the same library (scripted encoder, real store and provenance).
    proxy: ProxyEnv

    def import_media(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Samples:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    return Samples(tmp_path_factory.mktemp("relationship-samples"))


@pytest.fixture
def env(tmp_path: Path, samples: Samples) -> Env:
    proxy = build_proxy_env(tmp_path)
    AssetRelationshipsModule().register(proxy.container)
    return Env(
        proxy.library,
        proxy.container.resolve(RelationshipCatalog),
        proxy.container.resolve(RelationshipEditor),
        proxy.container.resolve(RelationshipMaintenance),
        proxy.paths,
        proxy.clock,
        samples,
        proxy,
    )
