"""The Layer 4 tasks: their place in the graph, the no-speech path, a failed edit, the style."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import (
    DEFAULT_STAGES,
    LAYER4_STAGES,
)
from media_house.modules.media_pipeline.application.runner import GraphRunner
from media_house.modules.media_pipeline.application.tasks import TaskJob, TaskResult
from media_house.modules.media_pipeline.domain.graph import PipelineGraph
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.music_and_sound_design.application.contracts import NothingToScore
from media_house.modules.transition_engine.application.contracts import NothingToTransition
from media_house.modules.visual_style_engine.application.contracts import (
    CURRENT_IDENTITY_VERSION,
    IDENTITIES,
    ResolveStyleResult,
    StyleConfig,
    StyleRef,
    resolve_style,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.unit.media_pipeline.test_continuity_task import an_edit
from tests.unit.media_pipeline.test_executors import FAILING, Recorder, executors, inspection, job

K = TaskKind
LAYER4_TASKS = LAYER4_STAGES - {K.VISUAL_STYLE}


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


# --- the stage table -------------------------------------------------------------------------
def test_layer4_is_opt_in_and_every_task_resolves_the_style_first() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=LAYER4_TASKS)
    specs = {s.id.kind: s for s in plan.specs}

    assert not LAYER4_STAGES & DEFAULT_STAGES
    assert specs[K.VISUAL_STYLE].needs == frozenset()
    for kind in LAYER4_TASKS:
        assert specs[kind].needs == {tid(K.VISUAL_STYLE), tid(K.INSPECT)}, kind
    assert plan.implied == {K.VISUAL_STYLE, K.INSPECT}  # no analysis is dragged in


def test_layer4_waits_for_the_edit_and_for_each_other_without_a_cycle() -> None:
    requested = LAYER4_STAGES | {K.EDIT_PLAN, K.CONTINUITY, K.REFRAMING, K.PACING}
    plan = plan_asset("a", has_video=True, has_audio=True, requested=requested)
    specs = {s.id.kind: s for s in plan.specs}

    for kind in LAYER4_STAGES:
        assert tid(K.EDIT_PLAN) in specs[kind].wants, kind
    assert tid(K.GRAPHICS) in specs[K.CAPTIONS].wants
    assert tid(K.GRAPHICS) in specs[K.TRANSITIONS].wants
    assert tid(K.CONTINUITY) in specs[K.TRANSITIONS].wants
    assert {tid(K.GRAPHICS), tid(K.TRANSITIONS), tid(K.CAPTIONS)} <= specs[K.SOUND_DESIGN].wants
    assert tid(K.REFRAMING) in specs[K.VFX].wants
    layer4 = {tid(k) for k in LAYER4_STAGES}
    assert not layer4 & specs[K.EDIT_PLAN].upstream  # the edit never waits for Layer 4
    assert len(PipelineGraph(plan.specs).levels()) > 1  # it is a DAG (the graph checks cycles)


def test_picture_tasks_need_a_picture_and_captions_need_sound() -> None:
    audio_only = plan_asset("a", has_video=False, has_audio=True, requested=LAYER4_STAGES)
    silent = plan_asset("a", has_video=True, has_audio=False, requested=LAYER4_STAGES)

    assert {K.GRAPHICS, K.CAPTIONS, K.TRANSITIONS, K.VFX} <= audio_only.not_applicable
    assert {s.id.kind for s in audio_only.specs} == {K.INSPECT, K.VISUAL_STYLE, K.SOUND_DESIGN}
    assert silent.not_applicable == {K.CAPTIONS}


# --- why a dependency was skipped ------------------------------------------------------------
def test_a_dependency_skip_carries_the_status_at_the_root_of_the_chain() -> None:
    def execute(work: TaskJob) -> TaskResult:
        if work.id.kind is K.AUDIO_ANALYSIS:
            return TaskResult(TaskStatus.SKIPPED_UNAVAILABLE, "no audio track")
        if work.id.kind is K.VIDEO_ANALYSIS:
            return TaskResult(TaskStatus.FAILED, "decoder")
        return TaskResult(TaskStatus.SUCCEEDED)

    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.EDIT_PLAN, K.REFRAMING})
    run = GraphRunner(dict.fromkeys(TaskKind, execute)).run(
        PipelineGraph(plan.specs), JobContext.detached()
    )

    edit = run.records[tid(K.EDIT_PLAN)].result  # audio -> content -> story -> edit
    framing = run.records[tid(K.REFRAMING)].result  # video failed
    assert edit.status is TaskStatus.SKIPPED_DEPENDENCY
    assert edit.cause is TaskStatus.SKIPPED_UNAVAILABLE
    assert framing.cause is TaskStatus.FAILED


# --- fixtures for the executors ----------------------------------------------------------------
def style_result(width: int = 1080, height: int = 1920, profile: str | None = None) -> Any:
    style = resolve_style(
        IDENTITIES[CURRENT_IDENTITY_VERSION],
        StyleConfig(profile=profile),
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        width=width,
        height=height,
        anchor_asset_id="a",
    )
    ref = StyleRef(
        document_id="style-doc",
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
    )
    return ResolveStyleResult(style=style, document=make_asset("style-doc"), ref=ref, created=True)


RESOLVED = style_result()
STYLE = (K.VISUAL_STYLE, TaskResult(TaskStatus.SUCCEEDED, payload=RESOLVED))
INSPECTED = (K.INSPECT, inspection(audios=1))


def transitions_result(created: bool = True, state: str = "ok") -> Any:
    plan = SimpleNamespace(
        state=SimpleNamespace(value=state),
        plan_version=1,
        warnings=(),
        profile="shorts_meme",
        transitions=(),
        events=(),
    )
    return SimpleNamespace(plan=plan, asset=make_asset("trans-doc"), created=created)


class StyleOwner(Recorder):
    """The style resolver answers without a job context."""

    def execute(self, command: Any, ctx: JobContext | None = None) -> Any:
        return super().execute(command, ctx)


# --- the no-speech path and a failed edit ----------------------------------------------------
def test_without_an_edit_in_the_run_the_whole_recording_is_one_clip_at_the_inspected_rate() -> None:
    owner = Recorder(Ok(transitions_result()))

    out = executors(transition_planner=owner).transitions(job(K.TRANSITIONS, STYLE, INSPECTED))

    [command] = owner.commands
    sequence = command.sequence
    assert sequence.edit is None and sequence.project_id is None
    [clip] = sequence.clips
    assert (clip.clip_id, clip.asset_id) == ("clip:a", "a")
    assert (clip.source_start, clip.timeline_start) == (clip.timeline_start, clip.source_start)
    assert clip.source_end.numerator == 3 and clip.source_end.denominator == 1  # 3.0 s probed
    assert sequence.frame_rate.numerator == 30 and not sequence.frame_rate_assumed
    assert command.style == RESOLVED.ref and command.render is True
    assert out.status is TaskStatus.SUCCEEDED and out.facts["path"] == "no_speech"
    assert out.facts["edit_plan_id"] is None and out.facts["style_fingerprint"]
    assert "no edit plan in this run" in out.detail


def test_a_recording_without_speech_takes_the_no_speech_path() -> None:
    no_story = TaskResult(
        TaskStatus.SKIPPED_DEPENDENCY,
        "story skipped_unavailable: no speech",
        cause=TaskStatus.SKIPPED_UNAVAILABLE,
    )
    owner = Recorder(Ok(transitions_result()))

    out = executors(transition_planner=owner).transitions(
        job(K.TRANSITIONS, STYLE, INSPECTED, (K.EDIT_PLAN, no_story))
    )

    assert owner.commands[0].sequence.edit is None
    assert out.status is TaskStatus.SUCCEEDED and "no speech" in out.detail


def test_a_failed_edit_is_reported_and_never_replaced_by_the_whole_recording() -> None:
    owner = Recorder(Ok(transitions_result()))
    ex = executors(transition_planner=owner, style_resolver=StyleOwner(FAILING))
    failed = TaskResult(TaskStatus.FAILED, "planner crashed")
    behind_failure = TaskResult(
        TaskStatus.SKIPPED_DEPENDENCY, "story failed", cause=TaskStatus.FAILED
    )

    for edit in (failed, behind_failure):
        out = ex.transitions(job(K.TRANSITIONS, STYLE, INSPECTED, (K.EDIT_PLAN, edit)))
        style = ex.visual_style(job(K.VISUAL_STYLE, (K.EDIT_PLAN, edit)))
        assert out.status is TaskStatus.SKIPPED_DEPENDENCY and "never used" in out.detail
        assert style.status is TaskStatus.SKIPPED_DEPENDENCY
    assert owner.commands == []


def test_with_an_edit_the_plan_names_it_and_uses_its_clips() -> None:
    plan = an_edit()
    owner = Recorder(Ok(transitions_result()))

    out = executors(transition_planner=owner).transitions(
        job(
            K.TRANSITIONS,
            STYLE,
            INSPECTED,
            (K.EDIT_PLAN, TaskResult(TaskStatus.SUCCEEDED, payload=plan)),
        )
    )

    sequence = owner.commands[0].sequence
    assert sequence.edit.edit_plan_id == plan.edit_plan_id
    assert [c.clip_id for c in sequence.clips] == ["clip:000:v", "clip:001:v"]
    assert sequence.frame_rate == plan.timebase.frame_rate
    assert out.facts["path"] == "edit" and out.facts["edit_plan_id"] == plan.edit_plan_id


def test_nothing_to_place_is_unavailable_not_failed() -> None:
    for error, planner in (
        (NothingToTransition(), "transition_planner"),
        (NothingToScore(), "sound_planner"),
    ):
        ex = executors(None, **{planner: Recorder(Err(error))})
        method = ex.transitions if planner == "transition_planner" else ex.sound_design
        kind = K.TRANSITIONS if planner == "transition_planner" else K.SOUND_DESIGN
        assert method(job(kind, STYLE, INSPECTED)).status is TaskStatus.SKIPPED_UNAVAILABLE


# --- the style: the one place the format is chosen -------------------------------------------
def test_the_style_is_resolved_for_the_delivery_format_and_profile() -> None:
    owner = StyleOwner(Ok(style_result()))
    settings = StageSettings(format_profile="shorts_haul", sponsored=True, output_format="9:16")

    out = executors(settings, style_resolver=owner).visual_style(job(K.VISUAL_STYLE))

    [command] = owner.commands
    assert (command.width, command.height) == (1080, 1920) and command.project_id is None
    assert command.config.profile == "shorts_haul" and command.config.sponsored
    assert out.status is TaskStatus.SUCCEEDED and out.facts["output"] == "1080x1920"
    assert out.payload is owner.answer.value


def test_the_reframing_target_is_the_delivery_shape_unless_one_is_given() -> None:
    owner = StyleOwner(Ok(style_result()))
    executors(StageSettings(reframing_target="1:1"), style_resolver=owner).visual_style(
        job(K.VISUAL_STYLE)
    )
    executors(
        StageSettings(reframing_target="1:1", output_resolution=(720, 1280)),
        style_resolver=owner,
    ).visual_style(job(K.VISUAL_STYLE))
    bad = executors(StageSettings(output_format="4:5"), style_resolver=owner).visual_style(
        job(K.VISUAL_STYLE)
    )

    assert [(c.width, c.height) for c in owner.commands] == [(1080, 1080), (720, 1280)]
    assert bad.status is TaskStatus.FAILED and "16:9" in bad.detail


def test_each_module_maps_the_style_profile_unless_its_own_profile_is_given() -> None:
    haul = (
        K.VISUAL_STYLE,
        TaskResult(TaskStatus.SUCCEEDED, payload=style_result(profile="shorts_haul")),
    )
    graphics = Recorder(Err(NothingToTransition()))
    transitions = Recorder(Err(NothingToTransition()))
    vfx = Recorder(Err(NothingToTransition()))
    sound = Recorder(Err(NothingToTransition()))
    captions = Recorder(Err(NothingToTransition()))
    owners: dict[str, Any] = {
        "graphics_planner": graphics,
        "transition_planner": transitions,
        "vfx_planner": vfx,
        "sound_planner": sound,
        "caption_planner": captions,
    }
    ex = executors(StageSettings(transition_profile="cinematic"), **owners)
    for kind, method in (
        (K.GRAPHICS, ex.graphics),
        (K.TRANSITIONS, ex.transitions),
        (K.VFX, ex.vfx),
        (K.SOUND_DESIGN, ex.sound_design),
        (K.CAPTIONS, ex.captions),
    ):
        method(job(kind, haul, INSPECTED))

    assert graphics.commands[0].config.profile == "shorts"
    assert transitions.commands[0].config.profile == "cinematic"  # explicit wins
    assert vfx.commands[0].config.profile == "shorts_energetic"
    assert sound.commands[0].config.profile == "shorts_haul"
    assert captions.commands[0].config.profile is None  # the caption engine reads the style itself
    assert graphics.commands[0].brief.width == 1080 and graphics.commands[0].brief.height == 1920


def test_a_vertical_delivery_plans_the_sound_of_a_short() -> None:
    style = (K.VISUAL_STYLE, TaskResult(TaskStatus.SUCCEEDED, payload=style_result()))
    shapes = {"9:16": True, "16:9": False, "1:1": False, None: False}
    for shape, short in shapes.items():
        sound = Recorder(Err(NothingToTransition()))

        executors(StageSettings(output_format=shape), sound_planner=sound).sound_design(
            job(K.SOUND_DESIGN, style, INSPECTED)
        )

        assert sound.commands[0].config.short_form is short, shape


# --- the report's Layer 4 package ------------------------------------------------------------
def test_the_layer4_package_lists_what_was_stored_rendered_and_not_made() -> None:
    from media_house.modules.media_pipeline.application.report import AssetReport, TaskReport

    style = TaskReport(
        K.VISUAL_STYLE,
        TaskStatus.REUSED,
        facts={"plan_asset_id": "s", "fingerprint": "f", "profile": "generic", "path": "edit"},
    )
    sound = TaskReport(
        K.SOUND_DESIGN,
        TaskStatus.SUCCEEDED,
        facts={
            "plan_asset_id": "p",
            "document_type": "sound_design_plan",
            "plan_version": 2,
            "state": "ok",
            "path": "edit",
            "edit_plan_id": "e",
            "edit_plan_version": 3,
            "rendered_asset_ids": ["stem"],
        },
    )
    failed = TaskReport(K.GRAPHICS, TaskStatus.SKIPPED_DEPENDENCY, detail="edit_plan failed")
    unasked = TaskReport(K.CAPTIONS, TaskStatus.SKIPPED_DISABLED, detail="not requested")
    report = AssetReport("a", "a.mp4", "video", "given", (style, sound, failed, unasked))

    package = report.layer4_package()

    assert package is not None
    assert package["edit"] == {"edit_plan_id": "e", "plan_version": 3}
    assert package["style"] == {"plan_asset_id": "s", "fingerprint": "f", "profile": "generic"}
    assert set(package["plans"]) == {"visual_style", "sound_design"}  # type: ignore[arg-type]
    assert package["rendered"] == {"sound_design": ["stem"]}
    assert package["not_made"] == {
        "graphics": {"status": "skipped_dependency", "detail": "edit_plan failed"}
    }
    assert AssetReport("a", "a.mp4", "video", "given", (unasked,)).layer4_package() is None
