"""REAL HyperFrames renders (skipped unless ``scripts/setup_hyperframes.py`` was run): the five
showcase graphics as ProRes 4444 MOVs with true alpha, checked with FFprobe and decoded
frames."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import (
    RENDER_OPERATION,
    AnimationRequest,
    GraphicContent,
    GraphicKind,
    GraphicRequest,
    GraphicsRenderer,
    ItemStatus,
    Origin,
    RenderGraphicsCommand,
    TimedWord,
    Timing,
)
from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import (
    install_dir,
    locate,
)
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.motion_graphics_engine.conftest import import_png, resolve, with_motion

INSTALL = install_dir(AppPaths.for_user())
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        isinstance(locate(INSTALL), str) or shutil.which("ffmpeg") is None,
        reason="no HyperFrames (uv run python scripts/setup_hyperframes.py) or no FFmpeg",
    ),
]
R = Rational
W, H, FPS = 1920, 1080, R(25, 1)


@pytest.fixture
def real_app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(
            env={},
            home=tmp_path / "home",
            environment=Environment.TEST,
            modules=with_motion(MotionGraphicsEngineModule(INSTALL)),
        )
    )
    yield application
    application.shutdown()


def frame_rgba(runner: ProcessRunner, mov: Path, frame: int, tmp: Path) -> np.ndarray:
    raw = tmp / f"f{frame}.rgba"
    result = runner.run(
        ProcessSpec(
            "ffmpeg",
            (
                "-v",
                "error",
                "-y",
                "-i",
                str(mov),
                "-vf",
                f"select=eq(n\\,{frame})",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                str(raw),
            ),
        )
    )
    assert result.succeeded, result.stderr
    return np.fromfile(raw, dtype=np.uint8).reshape(H, W, 4)


def test_showcase_graphics_render_with_true_alpha(real_app: Application, tmp_path: Path) -> None:
    anchor = import_png(real_app, tmp_path)
    style = resolve(real_app, anchor.id, W, H)
    words = (
        ("DIESE", 0, 10, False),
        ("BATTERIE", 10, 22, True),
        ("IST", 22, 30, False),
        ("DER", 30, 36, False),
        ("HAMMER", 36, 50, True),
    )
    requests = (
        GraphicRequest(
            request_id="rank",
            kind=GraphicKind.RANK_CARD,
            content=GraphicContent(rank=3),
            timing=Timing(local_duration=R(3, 1), timeline_start=R(10, 1)),
        ),
        GraphicRequest(
            request_id="price",
            kind=GraphicKind.PRICE_TAG,
            content=GraphicContent(headline="Lidl Feuerwerksbatterie", price_cents=1599),
            timing=Timing(local_duration=R(3, 1), timeline_start=R(20, 1)),
        ),
        GraphicRequest(
            request_id="lower",
            kind=GraphicKind.LOWER_THIRD,
            content=GraphicContent(headline="Marvin Esch", body=("MEsc Feuerwerk",)),
            timing=Timing(local_duration=R(3, 1), timeline_start=R(30, 1)),
        ),
        GraphicRequest(
            request_id="caption",
            kind=GraphicKind.CAPTION,
            origin=Origin.CAPTION,
            content=GraphicContent(
                look="word_group",
                words=tuple(
                    TimedWord(text=t, local_start=R(a, 25), local_end=R(b, 25), emphasis=e)
                    for t, a, b, e in words
                ),
            ),
            timing=Timing(local_duration=R(60, 25), timeline_start=R(40, 1)),
        ),
        GraphicRequest(
            request_id="end",
            kind=GraphicKind.END_SCREEN,
            origin=Origin.BRAND,
            content=GraphicContent(),
            timing=Timing(local_duration=R(3, 1), timeline_start=R(50, 1)),
            animation=AnimationRequest(intensity=2),
        ),
    )
    result = real_app.container.resolve(GraphicsRenderer).render(
        RenderGraphicsCommand(anchor_asset_id=anchor.id, requests=requests, style=style, fps=FPS),
        JobContext.detached(),
    )
    assert isinstance(result, Ok), result
    library = real_app.container.resolve(MediaLibrary)
    runner = real_app.container.resolve(ProcessRunner)
    for item in result.value.items:
        assert item.status is ItemStatus.OK, (item.request_id, item.qc)
        assert item.render is not None and item.asset_id is not None and item.frames is not None
        assert item.render.renderer == "hyperframes"
        assert item.render.codec == "prores 4444" and item.render.pix_fmt.startswith("yuva444p")
        assert item.render.frames == item.frames.frames  # exact frame count
        assert (item.render.width, item.render.height) == (W, H)
        assert item.render.alpha_min == 0 and item.render.alpha_max == 255
        assert 0 < item.render.opaque_fraction < 0.5  # an overlay, not a plate
        asset = library.get(item.asset_id)
        assert isinstance(asset, Ok) and asset.value.derivation is not None
        assert asset.value.derivation.operation == RENDER_OPERATION

    caption = result.value.item("caption")
    assert caption.asset_id is not None
    mov = library.local_path(caption.asset_id)
    assert isinstance(mov, Ok)
    # frame 15: "BATTERIE" (emphasis) is spoken -> highlight yellow #F4DE19 is drawn
    pixels = frame_rgba(runner, mov.value, 15, tmp_path)
    yellow = (
        (np.abs(pixels[:, :, 0].astype(int) - 0xF4) < 24)
        & (np.abs(pixels[:, :, 1].astype(int) - 0xDE) < 24)
        & (pixels[:, :, 2] < 90)
        & (pixels[:, :, 3] > 200)
    )
    assert yellow.sum() > 500
    # the first frame of the group is fully transparent (words reveal when spoken)
    assert frame_rgba(runner, mov.value, 0, tmp_path)[:, :, 3].max() == 0

    again = real_app.container.resolve(GraphicsRenderer).render(
        RenderGraphicsCommand(
            anchor_asset_id=anchor.id, requests=requests[:1], style=style, fps=FPS
        ),
        JobContext.detached(),
    )
    assert isinstance(again, Ok) and not again.value.items[0].created  # cache: nothing rendered
