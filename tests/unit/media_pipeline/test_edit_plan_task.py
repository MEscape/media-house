"""The ``edit_plan`` task: where it sits in the graph and what it is handed."""

from dataclasses import replace

from media_house.modules.editing_intelligence.application.contracts import (
    DurationMode,
    EditResult,
    PlanState,
)
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import DEFAULT_STAGES
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.shared.errors import Ok
from tests.support.asset_dto import make_asset
from tests.support.edit_fakes import S, Sec, edit, story, talk
from tests.unit.media_pipeline.test_executors import FAILING, Recorder, executors, job

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


# --- the stage table -------------------------------------------------------------------------
def test_the_edit_plan_is_opt_in_and_pulls_in_what_it_needs() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.EDIT_PLAN})
    spec = next(s for s in plan.specs if s.id.kind is K.EDIT_PLAN)

    assert K.EDIT_PLAN not in DEFAULT_STAGES and K.STORY in DEFAULT_STAGES
    assert spec.needs == {tid(K.STORY)}
    assert {K.STORY, K.CONTENT_ANALYSIS, K.AUDIO_ANALYSIS, K.INSPECT} <= {
        s.id.kind for s in plan.specs
    }
    assert plan.implied >= {K.STORY, K.CONTENT_ANALYSIS, K.AUDIO_ANALYSIS}


def test_the_edit_plan_waits_for_the_alignment_and_the_video_when_they_are_in_the_graph() -> None:
    plan = plan_asset(
        "a", has_video=True, has_audio=True, requested={K.EDIT_PLAN, K.ALIGNMENT, K.VIDEO_ANALYSIS}
    )
    spec = next(s for s in plan.specs if s.id.kind is K.EDIT_PLAN)

    assert spec.wants >= {tid(K.ALIGNMENT), tid(K.VIDEO_ANALYSIS), tid(K.INSPECT)}
    assert not any(tid(K.EDIT_PLAN) in s.upstream for s in plan.specs)  # nothing waits for it


def test_picture_media_without_audio_cannot_be_edited_from_a_story() -> None:
    plan = plan_asset("a", has_video=True, has_audio=False, requested={K.EDIT_PLAN})

    assert K.EDIT_PLAN in plan.not_applicable


def test_audio_only_media_get_an_edit_plan_without_video_stages() -> None:
    plan = plan_asset("a", has_video=False, has_audio=True, requested={K.EDIT_PLAN})

    assert K.EDIT_PLAN not in plan.not_applicable
    assert K.VIDEO_ANALYSIS not in {s.id.kind for s in plan.specs}


# --- the executor ----------------------------------------------------------------------------
def edit_result(*, created: bool = True, hard_miss: bool = False) -> EditResult:
    rec = talk([S("the screen is bright and sharp"), S("the speaker is loud", gap=0.4)])
    plan = edit(story(rec, [Sec("a", (0, 1), "essential")]), rec)
    if hard_miss:
        plan = replace(plan, state=PlanState.UNMET_CONSTRAINTS)
    return EditResult(plan, make_asset("edit-doc"), created=created)


def test_the_edit_task_is_handed_the_story_and_the_upstream_results_and_asks_for_nothing_new() -> (
    None
):
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import analysis_for, facts_for, plan_of

    analysis = analysis_for(BATTERIES, "a")
    story_plan = plan_of(facts_for(BATTERIES, "a"))
    owner = Recorder(Ok(edit_result()))
    ex = executors(
        StageSettings(edit_profile="tutorial", edit_target_seconds=90.0, edit_target_hard=True),
        edit_planner=owner,
    )

    result = ex.edit_plan(
        job(
            K.EDIT_PLAN,
            (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=story_plan)),
            (K.CONTENT_ANALYSIS, TaskResult(TaskStatus.SUCCEEDED, payload=analysis)),
            (K.ALIGNMENT, TaskResult(TaskStatus.FAILED, payload=object())),
        )
    )

    [command] = owner.commands
    assert command.asset_ids == ("a",) and command.story_plan is story_plan
    assert command.content_analyses == {"a": analysis}
    assert (
        command.alignments == {} and command.video_analyses == {}
    )  # nothing delivered, nothing passed
    assert command.acquire.value == "stored"  # the pipeline never asks the planner to plan
    assert command.config.profile == "tutorial"
    assert command.config.target is not None
    assert (command.config.target.high, command.config.target.mode) == (90.0, DurationMode.HARD)
    assert result.status is TaskStatus.SUCCEEDED and result.payload is not None
    assert result.facts["edit_plan_asset_id"] == "edit-doc" and result.facts["profile"] == "generic"


def test_a_stored_edit_plan_is_reported_as_reused() -> None:
    owner = Recorder(Ok(edit_result(created=False)))

    assert executors(edit_planner=owner).edit_plan(job(K.EDIT_PLAN)).status is TaskStatus.REUSED


def test_a_plan_that_misses_a_hard_constraint_is_partial_not_failed() -> None:
    owner = Recorder(Ok(edit_result(hard_miss=True)))

    result = executors(edit_planner=owner).edit_plan(job(K.EDIT_PLAN))

    assert result.status is TaskStatus.PARTIAL and "hard constraint" in result.detail
    assert result.payload is not None  # a best-effort plan exists


def test_an_edit_error_is_a_failed_task_with_the_reason() -> None:
    result = executors(edit_planner=Recorder(FAILING)).edit_plan(job(K.EDIT_PLAN))

    assert result.status is TaskStatus.FAILED and "Cannot decode" in result.detail


def test_every_task_kind_has_an_executor() -> None:
    assert set(executors().table()) == set(TaskKind)
