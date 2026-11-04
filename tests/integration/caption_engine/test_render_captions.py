"""Caption groups rendered by motion graphics with the user's real HyperFrames install.

Skipped when HyperFrames (``scripts/setup_hyperframes.py``) or FFmpeg is absent. One frame of a
rendered group is composited over a dark plate and kept as ``caption_frame.png`` under the
test's folder so a person can judge readability.
"""

import shutil
from pathlib import Path

import pytest
from PIL import Image

from media_house.bootstrap.application import Application
from media_house.core.application.ports import Clock, ProcessRunner, ProcessSpec
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.caption_engine.application.contracts import (
    CaptionMode,
    PlanCaptionsCommand,
    RenderStatus,
    ResultState,
)
from media_house.modules.caption_engine.application.plan_captions import PlanCaptions
from media_house.modules.content_intelligence.application.contracts import ContentCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import GraphicsCatalog
from media_house.modules.motion_graphics_engine.application.render_graphics import (
    GraphicsProducer,
    RenderGraphics,
)
from media_house.modules.motion_graphics_engine.infrastructure.font_metrics import SfntFontMetrics
from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import (
    HyperFramesBackend,
    install_dir,
    locate,
)
from media_house.modules.motion_graphics_engine.infrastructure.render_probe import (
    FfmpegRenderProbe,
)
from media_house.modules.reframing_intelligence.application.contracts import ReframingCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.caption_engine.conftest import (
    caption_timeline,
    import_anchor,
    resolve_style,
    store_timeline,
)
from tests.support.caption_fakes import audio_timeline, said

pytestmark = pytest.mark.integration

INSTALL = install_dir(AppPaths.for_user())
needs_renderer = pytest.mark.skipif(
    isinstance(locate(INSTALL), str)
    or shutil.which("ffmpeg") is None
    or shutil.which("ffprobe") is None,
    reason="HyperFrames (scripts/setup_hyperframes.py) or FFmpeg is not installed",
)


def planner_with_real_renderer(app: Application) -> PlanCaptions:
    c = app.container
    runner = c.resolve(ProcessRunner)
    producer = GraphicsProducer(
        c.resolve(MediaLibrary),
        c.resolve(VisualStyleCatalog),
        c.resolve(StyleValidator),
        c.resolve(FontFiles),
        SfntFontMetrics(),
        HyperFramesBackend(runner, INSTALL),
        FfmpegRenderProbe(runner),
        c.resolve(AppPaths),
    )
    return PlanCaptions(
        c.resolve(MediaLibrary),
        c.resolve(AudioCatalog),
        c.resolve(ContentCatalog),
        c.resolve(VideoCatalog),
        c.resolve(ReframingCatalog),
        c.resolve(InspectionCatalog),
        c.resolve(GraphicsCatalog),
        c.resolve(VisualStyleCatalog),
        c.resolve(StyleValidator),
        RenderGraphics(c.resolve(MediaLibrary), producer),
        c.resolve(AppPaths),
        c.resolve(Clock),
    )


@needs_renderer
def test_groups_render(app: Application, tmp_path: Path) -> None:
    anchor = import_anchor(app, tmp_path)
    speech = "Heute nur 19,99 € bei Lidl. Größer geht's nicht!"
    store_timeline(app, anchor, audio_timeline(said(speech), asset_id=anchor.id))
    _, ref = resolve_style(app, anchor.id, "shorts_haul")
    planner = planner_with_real_renderer(app)
    command = PlanCaptionsCommand(timeline=caption_timeline(anchor.id, 8), style=ref, render=True)

    result = planner.execute(command, JobContext.detached())

    assert isinstance(result, Ok), result
    plan = result.value.plan
    assert plan.mode is CaptionMode.FULL_CAPTIONS
    assert [s.render.status for s in plan.segments] == [RenderStatus.RENDERED] * len(
        plan.segments
    ), [(c.code, c.message, c.evidence) for c in plan.checks]
    assert plan.state is ResultState.OK, [c.message for c in plan.checks]
    price = next(s for s in plan.segments if "19,99" in s.text)
    assert price.render.asset_id is not None
    assert price.render.start_frame == price.start_frame

    library = app.container.resolve(MediaLibrary)
    path = library.local_path(price.render.asset_id)
    assert isinstance(path, Ok)
    last = price.words[-1]
    shown = float(price.timeline_end.value) - float(price.timeline_start.value)
    at = min(float(last.local_start.value) + 0.25, shown - 0.1)  # all words in, still up
    rgba = tmp_path / "caption_rgba.png"
    ran = app.container.resolve(ProcessRunner).run(
        ProcessSpec(
            "ffmpeg",
            (
                "-v",
                "error",
                "-y",
                "-i",
                str(path.value),
                "-ss",
                f"{at:.3f}",
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgba",
                str(rgba),
            ),
            timeout_seconds=120,
        )
    )
    assert ran.succeeded, ran.stderr
    with Image.open(rgba) as graphic:
        plate = Image.new("RGBA", graphic.size, (64, 72, 80, 255))
        rgb = Image.alpha_composite(plate, graphic.convert("RGBA")).convert("RGB")
    rgb.save(tmp_path / "caption_frame.png")  # for a person to judge readability
    band = rgb.crop((54, 1114, 907, 1363))  # the 9:16 caption band
    colours = [c for _, c in band.getcolors(maxcolors=1_000_000) or [] if isinstance(c, tuple)]
    assert any(r > 230 and g > 200 and b < 80 for r, g, b in colours)  # yellow keyword
    assert any(min(r, g, b) > 235 for r, g, b in colours)  # white letters
    above = rgb.crop((54, 300, 907, 1000)).getcolors(maxcolors=1_000_000) or []
    assert len(above) == 1  # nothing drawn outside the caption band

    again = planner.execute(command, JobContext.detached())
    assert isinstance(again, Ok) and not again.value.created  # the identical plan is reused


@needs_renderer
def test_a_planned_emoji_is_drawn_after_its_word(app: Application, tmp_path: Path) -> None:
    anchor = import_anchor(app, tmp_path)
    store_timeline(app, anchor, audio_timeline(said("Das ist echt krass."), asset_id=anchor.id))
    style, ref = resolve_style(app, anchor.id, "shorts_haul")  # identity v2: emoji face
    assert style.tokens.emoji is not None
    planner = planner_with_real_renderer(app)
    command = PlanCaptionsCommand(timeline=caption_timeline(anchor.id, 4), style=ref, render=True)

    result = planner.execute(command, JobContext.detached())

    assert isinstance(result, Ok), result
    segment = next(s for s in result.value.plan.segments if s.emojis)
    (emoji,) = segment.emojis
    assert segment.render.status is RenderStatus.RENDERED
    assert (emoji.emoji, emoji.render, emoji.detail) == ("🤯", RenderStatus.RENDERED, "")
    path = app.container.resolve(MediaLibrary).local_path(segment.render.asset_id or "")
    assert isinstance(path, Ok)
    shown = float(segment.timeline_end.value) - float(segment.timeline_start.value)
    rgba = tmp_path / "emoji_rgba.png"
    ran = app.container.resolve(ProcessRunner).run(
        ProcessSpec(
            "ffmpeg",
            ("-v", "error", "-y", "-i", str(path.value), "-ss", f"{shown - 0.2:.3f}",
             "-frames:v", "1", "-pix_fmt", "rgba", str(rgba)),
            timeout_seconds=120,
        )
    )  # fmt: skip
    assert ran.succeeded, ran.stderr
    with Image.open(rgba) as graphic:
        band = graphic.convert("RGBA").crop((54, 1114, 907, 1363))  # the 9:16 caption band
    colours = [c for _, c in band.getcolors(maxcolors=1_000_000) or [] if isinstance(c, tuple)]
    # the emoji's orange explosion: no caption colour (white, yellow keyword, black) is orange
    assert any(a > 200 and r > 200 and 80 < g < 170 and b < 90 for r, g, b, a in colours)
