"""The real application for every transition engine integration test.

The module (and motion_graphics_engine, the overlay renderer it needs) is registered in this
fixture's container; bootstrap is the orchestrator's. motion_graphics_engine's HyperFrames
runtime is looked up in the test's own cache, so it is NOT installed there: the real render test
builds its own application pointing at the user's install and skips without it.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.modules import installed_modules
from media_house.core.modules import ApplicationModule
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
from media_house.modules.transition_engine.module import TransitionEngineModule
from media_house.modules.visual_style_engine.application.contracts import (
    ResolveStyleCommand,
    ResolveStyleResult,
    StyleConfig,
    StyleResolver,
)
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok

pytestmark = pytest.mark.integration

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe are not on PATH",
)


def start(home: Path, motion: MotionGraphicsEngineModule | None = None) -> Application:
    """The installed modules plus motion graphics and transitions (unless already installed);
    a given ``motion`` module replaces the installed one."""
    modules: list[ApplicationModule] = list(installed_modules())
    if motion is not None:
        modules = [m for m in modules if m.name != motion.name]
    names = {m.name for m in modules}
    for extra in (motion or MotionGraphicsEngineModule(), TransitionEngineModule()):
        if extra.name not in names:
            modules.append(extra)
    return Application.start(
        StartupOptions(env={}, home=home, environment=Environment.TEST, modules=tuple(modules))
    )


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = start(tmp_path / "home")
    yield application
    application.shutdown()


def import_png(app: Application, tmp_path: Path, name: str = "anchor") -> MediaAssetDto:
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (64, 36), (20, 20, 30)).save(path)
    result = app.container.resolve(MediaLibrary).import_file(path)
    assert isinstance(result, Ok), result
    return result.value.asset


def resolve_style(app: Application, anchor: str, profile: str | None = None) -> ResolveStyleResult:
    result = app.container.resolve(StyleResolver).execute(
        ResolveStyleCommand(
            anchor_asset_id=anchor, width=1920, height=1080, config=StyleConfig(profile=profile)
        )
    )
    assert isinstance(result, Ok), result
    return result.value
