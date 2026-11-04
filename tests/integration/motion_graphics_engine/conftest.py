"""The real application for every motion graphics integration test.

The module is registered in this fixture's container (bootstrap is the orchestrator's). Its
default HyperFrames folder lies under the test's own cache, so the renderer is NOT installed
there: tests that render use the fake backend below or the user's real install (skipped when
``scripts/setup_hyperframes.py`` was not run).
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.modules import installed_modules
from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.core.domain import Rational
from media_house.core.modules import ApplicationModule
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.motion_graphics_engine.application.ports import ComposedScene, Scene
from media_house.modules.motion_graphics_engine.domain.errors import RenderFailed
from media_house.modules.motion_graphics_engine.domain.profile import RenderSettings
from media_house.modules.motion_graphics_engine.infrastructure.composition import compose_html
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
from media_house.modules.visual_style_engine.application.contracts import (
    ResolvedStyle,
    ResolveStyleCommand,
    StyleResolver,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok, Result

pytestmark = pytest.mark.integration

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe are not on PATH",
)


def with_motion(module: MotionGraphicsEngineModule) -> tuple[ApplicationModule, ...]:
    """The product's modules with this one (replacing it once bootstrap registers it)."""
    others = tuple(m for m in installed_modules() if m.name != module.name)
    return (*others, module)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(
            env={},
            home=tmp_path / "home",
            environment=Environment.TEST,
            modules=with_motion(MotionGraphicsEngineModule()),
        )
    )
    yield application
    application.shutdown()


def import_png(
    app: Application,
    tmp_path: Path,
    name: str = "anchor",
    size: tuple[int, int] = (64, 36),
    rgba: bool = False,
) -> MediaAssetDto:
    path = tmp_path / f"{name}.png"
    Image.new("RGBA" if rgba else "RGB", size, (200, 40, 30, 255) if rgba else (20, 20, 30)).save(
        path
    )
    result = app.container.resolve(MediaLibrary).import_file(path)
    assert isinstance(result, Ok), result
    return result.value.asset


def resolve(app: Application, anchor: str, width: int = 1920, height: int = 1080) -> ResolvedStyle:
    result = app.container.resolve(StyleResolver).execute(
        ResolveStyleCommand(anchor_asset_id=anchor, width=width, height=height)
    )
    assert isinstance(result, Ok), result
    return result.value.style


class FakeBackend:
    """Composes the real HTML (so digests are real) and 'renders' a ProRes 4444 MOV with a
    partly transparent picture through FFmpeg, counting the renders."""

    def __init__(self, runner: ProcessRunner) -> None:
        self.runner = runner
        self.rendered: list[str] = []

    def identity(self) -> tuple[str, str]:
        return "fake", "1"

    def unavailable(self) -> str | None:
        return None

    def compose(self, scene: Scene) -> ComposedScene:
        document = compose_html(scene)
        import hashlib

        return ComposedScene(
            document=document,
            files=(),
            digest=hashlib.sha256(document.encode()).hexdigest(),
            width=round(scene.composition.width * scene.scale),
            height=round(scene.composition.height * scene.scale),
            frames=1 if scene.still_frame is not None else scene.frames,
            still=scene.still_frame is not None,
        )

    def render(
        self,
        composed: ComposedScene,
        *,
        output: Path,
        fps: Rational,
        settings: RenderSettings,
        cancellation: CancellationToken,
    ) -> Result[Path, RenderFailed]:
        self.rendered.append(composed.digest)
        w, h = composed.width, composed.height
        rate = f"{fps.numerator}/{fps.denominator}"
        graph = (
            f"color=c=black@0.0:s={w}x{h}:r={rate},format=rgba[bg];"
            f"color=c=red:s=200x100:r={rate},format=rgba[box];[bg][box]overlay=40:40"
        )
        args = ["-v", "error", "-y", "-filter_complex", graph, "-frames:v", str(composed.frames)]
        if composed.still:
            args += [str(output)]
        else:
            args += [
                "-c:v",
                "prores_ks",
                "-profile:v",
                "4444",
                "-pix_fmt",
                "yuva444p10le",
                str(output),
            ]
        result = self.runner.run(ProcessSpec("ffmpeg", tuple(args), timeout_seconds=120))
        if not result.succeeded:
            return Err(RenderFailed(result.stderr[-300:]))
        return Ok(output)
