"""The real application for every VFX engine integration test (module registered here until the
composition root lists it)."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.modules import DependencyError
from media_house.modules.vfx_engine.application.contracts import VfxPlanner
from media_house.modules.vfx_engine.module import VfxEngineModule
from media_house.shared.configuration import Environment

pytestmark = pytest.mark.integration

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required to make, import and render the test footage",
)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    try:
        application.container.resolve(VfxPlanner)
    except DependencyError:  # not in bootstrap/modules.py yet: register it here
        VfxEngineModule().register(application.container)
    yield application
    application.shutdown()
