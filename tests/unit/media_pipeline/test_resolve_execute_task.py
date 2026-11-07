"""The Resolve task: its place in the graph, what the adapter is handed, how it ends."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import (
    DEFAULT_STAGES,
    LAYER5_STAGES,
)
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.resolve_adapter.application.contracts import (
    CaptionStrategy,
    ExecuteTimelineCommand,
    ExecutionPolicy,
    ExecutionState,
    OperationStatus,
    Outcome,
    RenderCommand,
    ResolveUnavailable,
)
from media_house.shared.errors import Err, Ok, ValidationError
from tests.support.asset_dto import make_asset
from tests.unit.media_pipeline.test_executors import Recorder, executors, job
from tests.unit.resolve_adapter import fixtures as rf
from tests.unit.resolve_adapter.test_document import report_of
from tests.unit.timeline_builder import fixtures as bf

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


def built_result() -> TaskResult:
    return TaskResult(TaskStatus.SUCCEEDED, payload=bf.built())


def executed(tmp_path: Path, outcome: Outcome | None = None) -> Any:
    ex, _ = rf.execution(tmp_path)
    rf.run(ex)
    report = report_of(ex)
    if outcome is not None:
        report = replace(
            report,
            outcome=outcome,
            state={
                Outcome.FAILED: ExecutionState.FAILED,
                Outcome.INTERRUPTED: ExecutionState.INTERRUPTED,
                Outcome.CANCELLED: ExecutionState.CANCELLED,
            }.get(outcome, ExecutionState.COMPLETED),
            errors=("it broke",) if outcome is Outcome.FAILED else (),
        )
    return SimpleNamespace(report=report, report_asset=make_asset("rep-doc"))


def rendered(*, ok: bool = True) -> Any:
    report = SimpleNamespace(
        outcome=Outcome.COMPLETED if ok else Outcome.FAILED,
        errors=() if ok else ("disk full",),
        render=SimpleNamespace(status="complete" if ok else "failed"),
    )
    return SimpleNamespace(report=report, output=make_asset("out-1") if ok else None, reused=False)


# --- the stage table -----------------------------------------------------------------------------
def test_the_task_is_opt_in_in_no_group_and_pulls_in_the_build() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=frozenset({K.RESOLVE_EXECUTE}))
    spec = next(s for s in plan.specs if s.id.kind is K.RESOLVE_EXECUTE)

    assert K.RESOLVE_EXECUTE not in DEFAULT_STAGES and K.RESOLVE_EXECUTE not in LAYER5_STAGES
    assert spec.needs == {tid(K.TIMELINE_BUILD)}
    assert {K.TIMELINE_BUILD, K.TIMELINE, K.VISUAL_STYLE} <= plan.implied


def test_nothing_waits_for_resolve_but_the_delivery_of_its_render() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=set(K))

    waiting = {s.id.kind for s in plan.specs if tid(K.RESOLVE_EXECUTE) in s.upstream}
    assert waiting == {K.DELIVER}


# --- the executor ------------------------------------------------------------------------------
def test_the_adapter_is_handed_the_built_timeline_of_the_run(tmp_path: Path) -> None:
    owner = Recorder(Ok(executed(tmp_path)))

    out = executors(resolve_executor=owner).resolve_execute(
        job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result()))
    )

    [command] = owner.commands
    assert isinstance(command, ExecuteTimelineCommand)
    assert command.timeline is not None and command.timeline.timeline_id
    assert command.config.policy is ExecutionPolicy.REQUIRE_CONFLICT_REVIEW
    assert command.config.caption_strategy is CaptionStrategy.RENDERED
    assert out.facts["report_asset_id"] == "rep-doc"


def test_the_run_settings_reach_the_adapter(tmp_path: Path) -> None:
    owner = Recorder(Ok(executed(tmp_path)))
    settings = StageSettings(
        resolve_policy="create_new",
        resolve_project="My film",
        resolve_adopt_project=True,
        resolve_allow_switch=True,
        resolve_captions="rendered_with_editable_cues",
    )

    executors(settings, resolve_executor=owner).resolve_execute(
        job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result()))
    )

    config = owner.commands[0].config
    assert config.policy is ExecutionPolicy.CREATE_NEW and config.project_name == "My film"
    assert config.adopt_existing_project and config.allow_project_switch
    assert config.caption_strategy is CaptionStrategy.RENDERED_WITH_EDITABLE_CUES


def test_instructions_scripting_cannot_carry_out_make_the_task_partial_never_a_success(
    tmp_path: Path,
) -> None:
    out = executors(resolve_executor=Recorder(Ok(executed(tmp_path)))).resolve_execute(
        job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.PARTIAL and "cannot carry out" in out.detail
    assert out.facts["outcome"] == "partial" and out.facts["partial"] >= 1  # type: ignore[operator]
    assert out.facts["resolve_edition"] == "studio" and out.facts["open_conflicts"] == 0


def test_a_clean_outcome_is_a_success(tmp_path: Path) -> None:
    out = executors(
        resolve_executor=Recorder(Ok(executed(tmp_path, Outcome.COMPLETED_WITH_WARNINGS)))
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert out.status is TaskStatus.SUCCEEDED


def test_a_failed_run_in_resolve_is_a_failure_with_the_reason(tmp_path: Path) -> None:
    out = executors(
        resolve_executor=Recorder(Ok(executed(tmp_path, Outcome.FAILED)))
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert out.status is TaskStatus.FAILED and "it broke" in out.detail


def test_an_interrupted_run_is_a_failure_not_a_skip(tmp_path: Path) -> None:
    out = executors(
        resolve_executor=Recorder(Ok(executed(tmp_path, Outcome.INTERRUPTED)))
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert out.status is TaskStatus.FAILED


def test_resolve_not_being_there_is_unavailable_not_a_failure() -> None:
    owner = Recorder(Err(ResolveUnavailable("not_running", "start it")))

    out = executors(resolve_executor=owner).resolve_execute(
        job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.SKIPPED_UNAVAILABLE and "start it" in out.detail


def test_any_other_error_is_a_failure() -> None:
    out = executors(
        resolve_executor=Recorder(Err(ValidationError("no", user_message="Not executable.")))
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert out.status is TaskStatus.FAILED


def test_a_wrong_setting_fails_clearly_without_calling_resolve() -> None:
    owner = Recorder(Err(ValidationError("unexpected")))

    out = executors(StageSettings(resolve_policy="maybe"), resolve_executor=owner).resolve_execute(
        job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.FAILED and "unknown Resolve setting" in out.detail
    assert owner.commands == []


def test_without_a_built_timeline_there_is_nothing_to_send() -> None:
    owner = Recorder(Err(ValidationError("unexpected")))

    out = executors(resolve_executor=owner).resolve_execute(job(K.RESOLVE_EXECUTE))

    assert out.status is TaskStatus.FAILED and "no built timeline" in out.detail
    assert owner.commands == []


def test_a_render_is_asked_for_only_after_a_timeline_without_open_work(tmp_path: Path) -> None:
    executor = Recorder(Ok(executed(tmp_path, Outcome.COMPLETED_WITH_WARNINGS)))
    renderer = Recorder(Ok(rendered()))

    out = executors(
        StageSettings(resolve_render_profile="youtube"),
        resolve_executor=executor,
        resolve_renderer=renderer,
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    [command] = renderer.commands
    assert isinstance(command, RenderCommand) and command.render.profile == "youtube"
    assert out.status is TaskStatus.SUCCEEDED and out.facts["render_asset_id"] == "out-1"
    assert out.facts["render_status"] == "complete"


def test_a_partial_timeline_without_failures_is_rendered_and_stays_partial(tmp_path: Path) -> None:
    renderer = Recorder(Ok(rendered()))

    out = executors(
        StageSettings(resolve_render_profile="youtube"),
        resolve_executor=Recorder(Ok(executed(tmp_path))),
        resolve_renderer=renderer,
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert len(renderer.commands) == 1 and out.status is TaskStatus.PARTIAL
    assert out.facts["render_asset_id"] == "out-1"


def test_a_partial_timeline_with_a_failed_operation_is_not_rendered(tmp_path: Path) -> None:
    renderer = Recorder(Ok(rendered()))
    done = executed(tmp_path)
    first, *rest = done.report.operations
    done.report = replace(
        done.report, operations=(replace(first, status=OperationStatus.FAILED), *rest)
    )

    out = executors(
        StageSettings(resolve_render_profile="youtube"),
        resolve_executor=Recorder(Ok(done)),
        resolve_renderer=renderer,
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert renderer.commands == [] and out.status is TaskStatus.PARTIAL


def test_a_render_that_fails_fails_the_task(tmp_path: Path) -> None:
    out = executors(
        StageSettings(resolve_render_profile="master"),
        resolve_executor=Recorder(Ok(executed(tmp_path, Outcome.COMPLETED_WITH_WARNINGS))),
        resolve_renderer=Recorder(Ok(rendered(ok=False))),
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert out.status is TaskStatus.FAILED and "disk full" in out.detail


def test_an_unknown_render_profile_fails_clearly(tmp_path: Path) -> None:
    owner = Recorder(Err(ValidationError("unexpected")))

    out = executors(
        StageSettings(resolve_render_profile="vhs"), resolve_executor=owner
    ).resolve_execute(job(K.RESOLVE_EXECUTE, (K.TIMELINE_BUILD, built_result())))

    assert out.status is TaskStatus.FAILED and owner.commands == []
