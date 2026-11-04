"""The transition planner in a real Application: stored plans, cache, versions, decisions,
handles from the stored inspection, and graphic overlays through motion_graphics_engine."""

from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.media_inspection.application.contracts import (
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import (
    install_dir,
    locate,
)
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
from media_house.modules.transition_engine.application.contracts import (
    Bookend,
    BookendKind,
    DecisionAction,
    DecisionStatus,
    InputHow,
    PlanTransitionsCommand,
    RenderState,
    TransitionCatalog,
    TransitionDecision,
    TransitionKind,
    TransitionPlanner,
    TransitionRequest,
    TransitionsResult,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.transition_engine.conftest import (
    import_png,
    needs_ffmpeg,
    resolve_style,
    start,
)
from tests.support.media_factories import make_video
from tests.support.trans_fakes import chain, r, sequence

pytestmark = pytest.mark.integration
K = TransitionKind


def run(app: Application, command: PlanTransitionsCommand) -> TransitionsResult:
    result = app.container.resolve(TransitionPlanner).execute(command, JobContext.detached())
    assert isinstance(result, Ok), result
    return result.value


def test_plans_are_stored_cached_versioned_and_keep_person_decisions(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_png(app, tmp_path).id
    seq = sequence(
        chain(("c1", anchor, 0.0, 10.0), ("c2", anchor, 20.0, 30.0)),
        requests=(TransitionRequest(request_id="fx", timeline_at=r(10)),),
    )
    first = run(app, PlanTransitionsCommand(seq))
    assert first.created
    t = first.plan.between("c1", "c2")
    assert t is not None
    # a still image has no length: the requested dissolve cannot be checked -> safe hard cut
    assert (t.kind, t.status) == (K.JUMP_CUT, DecisionStatus.FALLBACK)
    assert {(i.kind, i.how) for i in first.plan.inputs} >= {
        ("inspection", InputHow.NOT_AVAILABLE),
        ("audio_timeline", InputHow.NOT_AVAILABLE),
        ("video_analysis", InputHow.NOT_AVAILABLE),
    }
    again = run(app, PlanTransitionsCommand(seq))
    assert (again.created, again.asset.id) == (False, first.asset.id)

    keep = TransitionDecision("cut:c1>c2", DecisionAction.HARD_CUT, note="no effect here")
    second = run(app, PlanTransitionsCommand(seq, decisions=(keep,)))
    assert (second.plan.plan_version, second.plan.supersedes) == (2, first.asset.id)
    third = run(app, PlanTransitionsCommand(seq, render=True))  # another key, decision carried
    assert third.plan.plan_version == 3
    assert third.plan.decisions == (keep,)
    carried = third.plan.between("c1", "c2")
    assert carried is not None
    assert (carried.status, carried.decision_note) == (DecisionStatus.OVERRIDDEN, "no effect here")

    catalog = app.container.resolve(TransitionCatalog)
    latest = catalog.latest(anchor, edit_plan_id="edit-1")
    assert isinstance(latest, Ok)
    assert latest.value is not None
    assert latest.value[1].id == third.asset.id
    assert len(catalog.history(anchor)) == 3


def test_overlays_report_a_missing_renderer_and_the_plan_names_its_style(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_png(app, tmp_path).id
    styled = resolve_style(app, anchor, "ranking")
    seq = sequence(
        chain(("c1", anchor, 0.0, 10.0), ("c2", anchor, 20.0, 30.0)),
        bookends=(Bookend(kind=BookendKind.MEMBERSHIP_PLUG, timeline_at=r(10)),),
        format_hint="top_n",
    )
    done = run(app, PlanTransitionsCommand(seq, style=styled.ref, render=True))
    plug = done.plan.between("c1", "c2")
    assert plug is not None
    assert plug.overlay is not None
    assert (plug.kind, plug.status) == (K.ZOOM_TUNNEL, DecisionStatus.BRAND)
    assert plug.overlay.render_state is RenderState.NOT_AVAILABLE
    assert "setup_hyperframes" in plug.overlay.detail
    assert "overlay.not_rendered" in {i.code for i in done.plan.checks}
    assert done.plan.style is not None
    assert done.plan.style.document_id == styled.ref.document_id
    assert done.plan.profile == "ranking"
    assert [e.kind for e in done.plan.events] == [K.ZOOM_TUNNEL]


@needs_ffmpeg
def test_handles_come_from_the_stored_inspection(app: Application, tmp_path: Path) -> None:
    video = make_video(tmp_path / "clip.mp4", seconds=6)
    imported = app.container.resolve(MediaLibrary).import_file(video)
    assert isinstance(imported, Ok), imported
    asset = imported.value.asset.id
    inspected = app.container.resolve(MediaInspector).execute(
        InspectMediaCommand(asset), JobContext.detached()
    )
    assert isinstance(inspected, Ok), inspected
    seq = sequence(
        chain(("c1", asset, 1.0, 3.0), ("c2", asset, 4.0, 5.5)),
        requests=(TransitionRequest(request_id="fx", timeline_at=r(2)),),
        frame_rate=r(25),
    )
    plan = run(app, PlanTransitionsCommand(seq)).plan
    t = plan.between("c1", "c2")
    assert t is not None
    assert (t.kind, t.status, t.duration_frames) == (K.CROSS_DISSOLVE, DecisionStatus.REQUESTED, 12)
    assert t.handles.sufficient
    assert t.handles.available_out is not None
    assert t.handles.available_out >= 70  # 3 s of the 6 s recording at 25 fps
    assert ("inspection", InputHow.REUSED) in {(i.kind, i.how) for i in plan.inputs}


@needs_ffmpeg
def test_a_graphic_transition_is_rendered_by_motion_graphics(tmp_path: Path) -> None:
    install = install_dir(AppPaths.for_user())
    if isinstance(locate(install), str):
        pytest.skip("HyperFrames is not set up (uv run python scripts/setup_hyperframes.py)")
    app = start(tmp_path / "home", MotionGraphicsEngineModule(hyperframes_dir=install))
    try:
        anchor = import_png(app, tmp_path).id
        styled = resolve_style(app, anchor, "ranking")
        seq = sequence(
            chain(("c1", anchor, 0.0, 4.0), ("c2", anchor, 10.0, 14.0)),
            bookends=(Bookend(kind=BookendKind.MEMBERSHIP_PLUG, timeline_at=r(4)),),
            format_hint="top_n",
        )
        done = run(app, PlanTransitionsCommand(seq, style=styled.style, render=True))
        plug = done.plan.between("c1", "c2")
        assert plug is not None
        assert plug.overlay is not None
        assert plug.overlay.render_state is RenderState.RENDERED, plug.overlay.detail
        assert plug.overlay.asset_id is not None
        rendered = app.container.resolve(MediaLibrary).get(plug.overlay.asset_id)
        assert isinstance(rendered, Ok)
        assert rendered.value.extension == ".mov"
        assert any(e[0].startswith("renderer:") for e in done.plan.provenance.engines)
    finally:
        app.shutdown()
