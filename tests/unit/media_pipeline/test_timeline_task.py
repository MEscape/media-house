"""The Layer 5 task: its place in the graph, what the compiler is handed, how it ends."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from media_house.modules.edit_plan_compiler.application.contracts import (
    CompilerConfig,
    CompileTimelineCommand,
    DerivativePolicy,
    NothingToCompile,
    TimelineBlocked,
)
from media_house.modules.edit_plan_compiler.domain.engine import compile_timeline
from media_house.modules.media_pipeline.application.compile_source import timeline_source
from media_house.modules.media_pipeline.application.edit_view import view_of
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import (
    DEFAULT_STAGES,
    LAYER4_STAGES,
    LAYER5_STAGES,
)
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.graph import PipelineGraph
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.unit.edit_plan_compiler import fixtures as f
from tests.unit.media_pipeline.test_continuity_task import an_edit
from tests.unit.media_pipeline.test_executors import Recorder, executors, job
from tests.unit.media_pipeline.test_layer4_tasks import INSPECTED, STYLE

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


def compiled_result(created: bool = True) -> Any:
    spec = compile_timeline(f.inputs(), CompilerConfig(), meta=f.meta()).spec
    return SimpleNamespace(spec=spec, asset=make_asset("tl-doc"), created=created)


# --- the stage table -------------------------------------------------------------------------
def test_the_timeline_is_opt_in_and_pulls_in_only_the_style_and_the_inspection() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=frozenset({K.TIMELINE}))
    spec = next(s for s in plan.specs if s.id.kind is K.TIMELINE)

    assert not LAYER5_STAGES & DEFAULT_STAGES
    assert spec.needs == {tid(K.VISUAL_STYLE), tid(K.INSPECT)}
    assert plan.implied == {K.VISUAL_STYLE, K.INSPECT}


def test_the_timeline_waits_for_every_layer4_plan_the_run_makes_and_nothing_waits_for_it() -> None:
    requested = LAYER4_STAGES | {K.TIMELINE, K.EDIT_PLAN, K.REFRAMING}
    plan = plan_asset("a", has_video=True, has_audio=True, requested=requested)
    specs = {s.id.kind: s for s in plan.specs}

    # thumbnails are a delivery of their own: the timeline neither shows nor waits for them
    layer4 = {tid(k) for k in LAYER4_STAGES - {K.VISUAL_STYLE, K.THUMBNAIL_DESIGN}}
    assert layer4 <= specs[K.TIMELINE].wants
    assert tid(K.THUMBNAIL_DESIGN) not in specs[K.TIMELINE].upstream
    assert tid(K.EDIT_PLAN) in specs[K.TIMELINE].wants
    assert tid(K.TIMELINE) not in {u for s in specs.values() for u in s.upstream}
    assert len(PipelineGraph(plan.specs).levels()) > 1


def test_a_voice_recording_with_music_is_a_timeline_too() -> None:
    plan = plan_asset("a", has_video=False, has_audio=True, requested=LAYER4_STAGES | LAYER5_STAGES)

    assert K.TIMELINE in {s.id.kind for s in plan.specs}


# --- the edit as the compiler's own input ------------------------------------------------------
def test_an_edit_plan_becomes_a_timeline_source_with_everything_a_timeline_needs() -> None:
    plan = an_edit()

    source = timeline_source(view_of(plan, None), plan)

    assert source.edit is not None and source.edit.edit_plan_id == plan.edit_plan_id
    assert [c.clip_id for c in source.clips] == [p.placement_id for p in plan.placements]
    first = plan.placements[0]
    clip = source.clips[0]
    assert (clip.source_start, clip.source_end) == (first.source.start, first.source.end)
    assert (
        clip.timeline_duration == first.timeline_duration and clip.start_frame == first.start_frame
    )
    assert (clip.fade_in, clip.fade_out, clip.linked) == (
        first.fade_in,
        first.fade_out,
        first.linked,
    )
    assert source.frame_rate == plan.timebase.frame_rate


# --- the executor ----------------------------------------------------------------------------
def test_without_an_edit_the_compiler_gets_the_whole_recording_and_the_runs_style() -> None:
    owner = Recorder(Ok(compiled_result()))

    out = executors(timeline_compiler=owner).timeline(job(K.TIMELINE, STYLE, INSPECTED))

    [command] = owner.commands
    assert isinstance(command, CompileTimelineCommand)
    assert command.source.edit is None
    [clip] = command.source.clips
    assert clip.clip_id == "clip:a" and clip.timeline_start == clip.source_start
    assert command.style is not None and command.style_ref is not None
    assert command.style_ref.fingerprint == command.style.fingerprint
    assert (
        out.status is TaskStatus.SUCCEEDED
        and out.facts["document_type"] == "timeline_specification"
    )
    assert out.facts["video_items"] == 3 and out.facts["spec_hash"]
    assert out.payload is not None


def test_with_an_edit_the_compiler_is_handed_the_plan_of_the_same_run() -> None:
    plan = an_edit()
    owner = Recorder(Ok(compiled_result()))

    out = executors(timeline_compiler=owner).timeline(
        job(
            K.TIMELINE,
            STYLE,
            INSPECTED,
            (K.EDIT_PLAN, TaskResult(TaskStatus.SUCCEEDED, payload=plan)),
        )
    )

    source = owner.commands[0].source
    assert source.edit is not None and source.edit.plan_version == plan.plan_version
    assert out.facts["path"] == "edit" and out.facts["edit_plan_id"] == plan.edit_plan_id


def test_a_layer4_task_of_the_run_that_did_not_deliver_is_handed_over_as_undelivered() -> None:
    owner = Recorder(Ok(compiled_result()))
    captions = TaskResult(TaskStatus.FAILED, "renderer crashed")
    behind = TaskResult(TaskStatus.SKIPPED_DEPENDENCY, "graphics failed", cause=TaskStatus.FAILED)
    no_sound = TaskResult(TaskStatus.SKIPPED_UNAVAILABLE, "no audio track")
    fine = TaskResult(TaskStatus.PARTIAL, "some items fell back")

    executors(timeline_compiler=owner).timeline(
        job(
            K.TIMELINE, STYLE, INSPECTED,
            (K.CAPTIONS, captions), (K.GRAPHICS, behind), (K.SOUND_DESIGN, no_sound), (K.VFX, fine),
        )
    )  # fmt: skip

    undelivered = dict(owner.commands[0].undelivered)
    assert set(undelivered) == {"captions", "graphics"}  # not applicable and partial are not
    assert "renderer crashed" in undelivered["captions"]


def test_a_blocked_timeline_is_a_failure_with_the_reasons() -> None:
    blocked = Recorder(Err(TimelineBlocked(["asset x does not exist", "range beyond media"])))

    out = executors(timeline_compiler=blocked).timeline(job(K.TIMELINE, STYLE, INSPECTED))

    assert out.status is TaskStatus.FAILED
    assert "timeline_blocked" in out.detail and "asset x does not exist" in out.detail


def test_an_edit_with_no_clip_is_unavailable_not_failed() -> None:
    owner = Recorder(Err(NothingToCompile()))

    out = executors(timeline_compiler=owner).timeline(job(K.TIMELINE, STYLE, INSPECTED))

    assert out.status is TaskStatus.SKIPPED_UNAVAILABLE


def test_a_failed_edit_is_never_replaced_by_the_whole_recording() -> None:
    owner = Recorder(Ok(compiled_result()))
    failed = TaskResult(TaskStatus.FAILED, "planner crashed")

    out = executors(timeline_compiler=owner).timeline(
        job(K.TIMELINE, STYLE, INSPECTED, (K.EDIT_PLAN, failed))
    )

    assert out.status is TaskStatus.SKIPPED_DEPENDENCY and owner.commands == []


def test_without_a_style_there_is_nothing_to_bind_the_identity_to() -> None:
    owner = Recorder(Ok(compiled_result()))

    out = executors(timeline_compiler=owner).timeline(job(K.TIMELINE, INSPECTED))

    assert out.status is TaskStatus.SKIPPED_DEPENDENCY and owner.commands == []


def test_the_recording_version_is_a_setting_and_a_wrong_one_fails_clearly() -> None:
    owner = Recorder(Ok(compiled_result()))

    executors(
        StageSettings(timeline_derivatives="prefer_improved"), timeline_compiler=owner
    ).timeline(job(K.TIMELINE, STYLE, INSPECTED))
    bad = executors(StageSettings(timeline_derivatives="best"), timeline_compiler=owner).timeline(
        job(K.TIMELINE, STYLE, INSPECTED)
    )

    assert owner.commands[0].config.derivative_policy is DerivativePolicy.PREFER_IMPROVED
    assert bad.status is TaskStatus.FAILED and "prefer_improved" in bad.detail
    assert len(owner.commands) == 1


def test_a_partial_specification_is_a_partial_task_with_its_findings_as_warnings() -> None:
    plan = f.captions()
    broken = replace(
        plan,
        segments=(
            replace(plan.segments[0], render_status="failed", asset_id=None),
            plan.segments[1],
        ),
    )
    spec = compile_timeline(f.inputs(captions=broken), CompilerConfig(), meta=f.meta()).spec
    owner = Recorder(Ok(SimpleNamespace(spec=spec, asset=make_asset("tl-doc"), created=True)))

    out = executors(timeline_compiler=owner).timeline(job(K.TIMELINE, STYLE, INSPECTED))

    assert out.status is TaskStatus.PARTIAL
    assert any("caption" in w for w in out.warnings) and out.facts["state"] == "partial"
    assert out.facts["warning_items"] == 1 and out.facts["conflicts"] == 0
