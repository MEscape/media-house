"""The real application for every visual style integration test."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.modules import DependencyError
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.visual_style_engine.application.contracts import StyleResolver
from media_house.modules.visual_style_engine.module import VisualStyleEngineModule
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    try:
        application.container.resolve(StyleResolver)
    except DependencyError:  # not in bootstrap/modules.py yet: register it here
        VisualStyleEngineModule().register(application.container)
    yield application
    application.shutdown()


def import_image(app: Application, folder: Path, name: str, colour: str) -> MediaAssetDto:
    """A PNG imported into the real library (no FFmpeg needed)."""
    path = folder / f"{name}.png"
    Image.new("RGB", (64, 36), colour).save(path)
    imported = app.container.resolve(MediaLibrary).import_file(path)
    assert isinstance(imported, Ok)
    return imported.value.asset
