"""Renderer contract, library caching, incremental renders and stored plans (real Application,
real Media Library, real FFprobe QC; the HyperFrames step is replaced by an FFmpeg fake)."""

from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.application.contracts import (
    RENDER_OPERATION,
    DecisionAction,
    GraphicContent,
    GraphicDecision,
    GraphicKind,
    GraphicRequest,
    GraphicsBrief,
    GraphicsCatalog,
    GraphicsRenderer,
    ItemStatus,
    MotionGraphicsConfig,
    MotionGraphicsPlanner,
    Origin,
    PlanGraphicsCommand,
    RendererNotAvailable,
    RenderGraphicsCommand,
    RenderQuality,
    SponsoredSpan,
    TimedWord,
    Timing,
)
from media_house.modules.motion_graphics_engine.application.plan_graphics import PlanGraphics
from media_house.modules.motion_graphics_engine.application.render_graphics import (
    GraphicsProducer,
    RenderGraphics,
)
from media_house.modules.motion_graphics_engine.infrastructure.font_metrics import SfntFontMetrics
from media_house.modules.motion_graphics_engine.infrastructure.render_probe import (
    FfmpegRenderProbe,
)
from media_house.modules.visual_asset_generation.application.contracts import VisualAssetPreparer
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.motion_graphics_engine.conftest import (
    FakeBackend,
    import_png,
    needs_ffmpeg,
    resolve,
)

pytestmark = pytest.mark.integration
R = Rational


def wire(app: Application) -> tuple[RenderGraphics, PlanGraphics, FakeBackend]:
    c = app.container
    runner = c.resolve(ProcessRunner)
    backend = FakeBackend(runner)
    producer = GraphicsProducer(
        c.resolve(MediaLibrary),
        c.resolve(VisualStyleCatalog),
        c.resolve(StyleValidator),
        c.resolve(FontFiles),
        SfntFontMetrics(),
        backend,
        FfmpegRenderProbe(runner),
        c.resolve(AppPaths),
    )
    renderer = RenderGraphics(c.resolve(MediaLibrary), producer)
    planner = PlanGraphics(
        c.resolve(MediaLibrary),
        producer,
        c.resolve(AppPaths),
        c.resolve(Clock),
        c.resolve(VisualAssetPreparer),
    )
    return renderer, planner, backend


def callout(rid: str, text: str, start: int) -> GraphicRequest:
    return GraphicRequest(
        request_id=rid,
        kind=GraphicKind.CALLOUT,
        content=GraphicContent(headline=text),
        timing=Timing(local_duration=R(3, 1), timeline_start=R(start, 1)),
    )


@needs_ffmpeg
def test_render_registers_qc_checked_assets_and_reuses_identical_requests(
    app: Application, tmp_path: Path
) -> None:
    renderer, _, backend = wire(app)
    anchor = import_png(app, tmp_path)
    style = resolve(app, anchor.id)
    command = RenderGraphicsCommand(
        anchor_asset_id=anchor.id,
        requests=(callout("a", "Nur bei Lidl", 1), callout("b", "Ab 2. Januar", 10)),
        style=style,
        fps=R(25, 1),
    )

    first = renderer.render(command, JobContext.detached())
    assert isinstance(first, Ok), first
    items = first.value.items
    assert [i.status for i in items] == [ItemStatus.OK, ItemStatus.OK]
    assert all(i.created for i in items) and len(backend.rendered) == 2
    library = app.container.resolve(MediaLibrary)
    asset = library.get(items[0].asset_id or "")
    assert isinstance(asset, Ok)
    assert (
        asset.value.derivation is not None and asset.value.derivation.operation == RENDER_OPERATION
    )
    meta = asset.value.metadata
    assert meta["renderer"] == "fake" and meta["style_fingerprint"] == style.fingerprint
    assert meta["frames"] == 75 and str(meta["pix_fmt"]).startswith("yuva444p")
    assert (
        items[0].render is not None
        and items[0].render.alpha_min == 0
        and items[0].render.alpha_max == 255
    )
    assert items[0].frames is not None and items[0].frames.start_frame == 25

    again = renderer.render(command, JobContext.detached())
    assert isinstance(again, Ok)
    assert [i.created for i in again.value.items] == [False, False]
    assert [i.asset_id for i in again.value.items] == [i.asset_id for i in items]
    assert len(backend.rendered) == 2  # nothing rendered

    changed = RenderGraphicsCommand(
        anchor_asset_id=anchor.id,
        requests=(callout("a", "Nur bei Lidl", 1), callout("b", "Ab 3. Januar", 10)),
        style=style,
        fps=R(25, 1),
    )
    third = renderer.render(changed, JobContext.detached())
    assert isinstance(third, Ok)
    assert [i.created for i in third.value.items] == [False, True]  # incremental
    assert len(backend.rendered) == 3

    vertical = resolve(app, anchor.id, 1080, 1920)
    fourth = renderer.render(
        RenderGraphicsCommand(
            anchor_asset_id=anchor.id,
            requests=(callout("a", "Nur bei Lidl", 1),),
            style=vertical,
            fps=R(25, 1),
        ),
        JobContext.detached(),
    )
    assert isinstance(fourth, Ok) and fourth.value.items[0].created  # new style -> new render


@needs_ffmpeg
def test_caption_groups_render_for_caption_engine_and_preview_is_a_still(
    app: Application, tmp_path: Path
) -> None:
    renderer, _, backend = wire(app)
    anchor = import_png(app, tmp_path)
    style = resolve(app, anchor.id, 1080, 1920)
    words = tuple(
        TimedWord(
            text=t, local_start=R(i * 10, 25), local_end=R(i * 10 + 10, 25), emphasis=t == "KRASS"
        )
        for i, t in enumerate(["DAS", "IST", "KRASS"])
    )
    caption = GraphicRequest(
        request_id="cap:1",
        kind=GraphicKind.CAPTION,
        origin=Origin.CAPTION,
        content=GraphicContent(words=words, look="word_group"),
        timing=Timing(local_duration=R(32, 25), timeline_start=R(4, 1)),
    )
    result = renderer.render(
        RenderGraphicsCommand(
            anchor_asset_id=anchor.id,
            requests=(caption,),
            style=style,
            fps=R(25, 1),
            quality=RenderQuality.PREVIEW,
        ),
        JobContext.detached(),
    )
    assert isinstance(result, Ok), result
    item = result.value.item("cap:1")
    assert item.status is ItemStatus.OK, item.qc
    assert item.render is not None and item.render.quality is RenderQuality.PREVIEW
    assert (item.render.width, item.render.height) == (540, 960)
    assert any(e.kind.value == "emphasis" and e.ref.endswith(".w2") for e in item.events)
    assert backend.rendered


def test_without_the_renderer_the_contract_names_the_setup_command(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_png(app, tmp_path)
    style = resolve(app, anchor.id)
    result = app.container.resolve(GraphicsRenderer).render(
        RenderGraphicsCommand(
            anchor_asset_id=anchor.id,
            requests=(callout("a", "Hallo", 1),),
            style=style,
            fps=R(25, 1),
        ),
        JobContext.detached(),
    )
    assert isinstance(result, Err)
    assert isinstance(result.error, RendererNotAvailable)
    assert "scripts/setup_hyperframes.py" in str(result.error)


def test_no_speech_plan_is_stored_with_not_available_renders(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_png(app, tmp_path)
    style = resolve(app, anchor.id)
    brief = GraphicsBrief(
        anchor_asset_id=anchor.id,
        project_id=None,
        edit=None,
        frame_rate=R(25, 1),
        width=1920,
        height=1080,
        timeline_duration=R(60, 1),
        sponsored=True,
    )
    planner = app.container.resolve(MotionGraphicsPlanner)
    result = planner.execute(PlanGraphicsCommand(brief=brief, style=style), JobContext.detached())
    assert isinstance(result, Ok), result
    plan = result.value.plan
    assert {g.graphic_id for g in plan.graphics} == {"brand:end_screen", "brand:sponsor:0"}
    assert all(g.status is ItemStatus.NOT_AVAILABLE for g in plan.graphics)
    assert plan.timeline_end_with_bookends == R(80, 1)  # 60 s + the appended 20 s end screen
    assert (
        plan.edit is None and plan.style is not None and plan.style.fingerprint == style.fingerprint
    )
    assert any(s.stage == "render" and s.state.value == "not_available" for s in plan.stages)
    stored = app.container.resolve(GraphicsCatalog).latest(anchor.id)
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0] == plan


@needs_ffmpeg
def test_plans_are_cached_versioned_and_keep_person_decisions(
    app: Application, tmp_path: Path
) -> None:
    _, planner, backend = wire(app)
    anchor = import_png(app, tmp_path)
    style = resolve(app, anchor.id)
    brief = GraphicsBrief(
        anchor_asset_id=anchor.id,
        project_id="p1",
        edit=None,
        frame_rate=R(25, 1),
        width=1920,
        height=1080,
        timeline_duration=R(30, 1),
        sponsored=True,
        sponsored_spans=(SponsoredSpan(timeline_start=R(0, 1), timeline_end=R(4, 1)),),
    )
    first = planner.execute(PlanGraphicsCommand(brief=brief, style=style), JobContext.detached())
    assert isinstance(first, Ok), first
    assert first.value.created and first.value.plan.plan_version == 1
    statuses = {g.graphic_id: g.status for g in first.value.plan.graphics}
    assert statuses == {"brand:end_screen": ItemStatus.OK, "brand:sponsor:0": ItemStatus.OK}, [
        g.checks for g in first.value.plan.graphics
    ]
    assert first.value.plan.events and all(
        e.timeline_at is not None for e in first.value.plan.events
    )
    rendered = len(backend.rendered)

    again = planner.execute(PlanGraphicsCommand(brief=brief, style=style), JobContext.detached())
    assert isinstance(again, Ok) and not again.value.created and len(backend.rendered) == rendered

    reject = MotionGraphicsConfig(
        decisions=(GraphicDecision(graphic_id="brand:end_screen", action=DecisionAction.REJECT),)
    )
    third = planner.execute(
        PlanGraphicsCommand(brief=brief, style=style, config=reject), JobContext.detached()
    )
    assert isinstance(third, Ok) and third.value.created
    plan = third.value.plan
    assert plan.plan_version == 2 and plan.supersedes == first.value.document.id
    assert {g.graphic_id: g.status for g in plan.graphics}[
        "brand:end_screen"
    ] is ItemStatus.REJECTED
    assert len(backend.rendered) == rendered  # the sponsor label render was reused


@needs_ffmpeg
def test_caption_groups_of_one_run_render_as_one_file_and_each_knows_its_place(
    app: Application, tmp_path: Path
) -> None:
    # E2E (O9): one HyperFrames run per caption group (34 groups = 537 s on CPU). A run of
    # groups is one composition now; every group keeps its own frames and its offset in the file.
    renderer, _, backend = wire(app)
    anchor = import_png(app, tmp_path)
    style = resolve(app, anchor.id, 1080, 1920)

    def group(n: int, at: int) -> GraphicRequest:
        words = tuple(
            TimedWord(text=t, local_start=R(i * 10, 25), local_end=R(i * 10 + 10, 25))
            for i, t in enumerate(["WIR", f"HABEN{n}"])
        )
        return GraphicRequest(
            request_id=f"cap:{n}",
            kind=GraphicKind.CAPTION,
            origin=Origin.CAPTION,
            content=GraphicContent(words=words, look="word_group"),
            timing=Timing(local_duration=R(20, 25), timeline_start=R(at, 1)),
        )

    result = renderer.render(
        RenderGraphicsCommand(
            anchor_asset_id=anchor.id,
            requests=tuple(group(n, at) for n, at in ((1, 2), (2, 4), (3, 7))),
            style=style,
            fps=R(25, 1),
        ),
        JobContext.detached(),
    )

    assert isinstance(result, Ok), result
    items = [result.value.item(f"cap:{n}") for n in (1, 2, 3)]
    assert all(i.status is ItemStatus.OK for i in items), [i.qc for i in items]
    assert len({i.asset_id for i in items}) == 1 and len(backend.rendered) == 1
    assert [i.render.asset_start_frame for i in items if i.render] == [0, 50, 125]
    assert [i.frames.start_frame for i in items if i.frames] == [50, 100, 175]


def test_appended_end_screen_ends_on_its_last_rendered_frame(
    app: Application, tmp_path: Path
) -> None:
    # 20 s at 29.97 fps is 599 frames (19.987 s); a bookend end of content + 20.000 s overhung the
    # timeline by a fraction of a frame and the builder refused the music tail under it.
    anchor = import_png(app, tmp_path)
    fps = R(30000, 1001)
    brief = GraphicsBrief(
        anchor_asset_id=anchor.id,
        project_id=None,
        edit=None,
        frame_rate=fps,
        width=1920,
        height=1080,
        timeline_duration=R(286 * 1001, 30000),  # content ends on frame 286
        sponsored=False,
    )
    planner = app.container.resolve(MotionGraphicsPlanner)
    result = planner.execute(
        PlanGraphicsCommand(brief=brief, style=resolve(app, anchor.id)), JobContext.detached()
    )
    assert isinstance(result, Ok), result
    assert result.value.plan.timeline_end_with_bookends == R(885 * 1001, 30000)


@needs_ffmpeg
def test_the_identical_end_screen_of_another_clip_is_registered_not_rendered(
    app: Application, tmp_path: Path
) -> None:
    renderer, _, backend = wire(app)
    library = app.container.resolve(MediaLibrary)
    end = GraphicRequest(
        request_id="brand:end_screen",
        kind=GraphicKind.END_SCREEN,
        origin=Origin.BRAND,
        content=GraphicContent(),
        timing=Timing(local_duration=R(2, 1), timeline_start=R(30, 1)),
    )
    assets = []
    for name, size in (("clip1", (64, 36)), ("clip2", (96, 54))):
        anchor = import_png(app, tmp_path, name=name, size=size)
        result = renderer.render(
            RenderGraphicsCommand(
                anchor_asset_id=anchor.id,
                requests=(end,),
                style=resolve(app, anchor.id),
                fps=R(25, 1),
            ),
            JobContext.detached(),
        )
        assert isinstance(result, Ok), result
        item = result.value.items[0]
        assert item.status is ItemStatus.OK and item.asset_id is not None
        stored = library.get(item.asset_id)
        assert isinstance(stored, Ok) and stored.value.derivation is not None
        assert stored.value.derivation.source_asset_id == anchor.id  # derived of ITS clip
        assets.append((item.created, stored.value.checksum))
    assert len(backend.rendered) == 1  # the second clip reused the first render
    assert assets[0][0] and not assets[1][0] and assets[0][1] == assets[1][1]
