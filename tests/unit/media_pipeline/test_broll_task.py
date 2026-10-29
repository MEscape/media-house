"""The ``broll`` task: where it sits in the graph and what it is handed."""

from dataclasses import replace

from media_house.modules.broll_intelligence.application.analyze_broll import BrollResult
from media_house.modules.broll_intelligence.domain.errors import NothingToCover
from media_house.modules.broll_intelligence.domain.values import ResultState
from media_house.modules.editing_intelligence.application.contracts import EditResult
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import DEFAULT_STAGES
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.support.broll_fakes import run, shot, target
from tests.support.edit_fakes import S, Sec, edit, story, talk
from tests.unit.media_pipeline.test_executors import FAILING, Recorder, executors, job

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


# --- the stage table -------------------------------------------------------------------------
def test_broll_is_opt_in_and_needs_the_content_analysis() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.BROLL})
    spec = next(s for s in plan.specs if s.id.kind is K.BROLL)

    assert K.BROLL not in DEFAULT_STAGES
    assert spec.needs == {tid(K.CONTENT_ANALYSIS)}
    assert {K.CONTENT_ANALYSIS, K.AUDIO_ANALYSIS, K.INSPECT} <= {s.id.kind for s in plan.specs}


def test_broll_waits_for_the_other_analyses_when_they_are_in_the_graph() -> None:
    plan = plan_asset(
        "a",
        has_video=True,
        has_audio=True,
        requested={K.BROLL, K.VIDEO_ANALYSIS, K.ALIGNMENT, K.STORY, K.PACING},
    )
    spec = next(s for s in plan.specs if s.id.kind is K.BROLL)

    assert spec.wants >= {
        tid(K.VIDEO_ANALYSIS),
        tid(K.ALIGNMENT),
        tid(K.STORY),
        tid(K.PACING),
    }


def test_the_edit_plan_waits_for_the_broll_analysis_when_it_is_in_the_graph() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.EDIT_PLAN, K.BROLL})
    spec = next(s for s in plan.specs if s.id.kind is K.EDIT_PLAN)

    assert tid(K.BROLL) in spec.wants


def test_a_recording_without_speech_gets_no_broll_task() -> None:
    plan = plan_asset("a", has_video=True, has_audio=False, requested={K.BROLL})

    assert K.BROLL in plan.not_applicable


# --- the executor ----------------------------------------------------------------------------
def broll_result(*, created: bool = True, partial: bool = False) -> BrollResult:
    found = run(
        [target()],
        [shot("bench", "s1", description="sanding a wooden board", events=[("gesture", 2.0)])],
    )
    if partial:
        found = replace(found, state=ResultState.PARTIAL)
    return BrollResult(found, make_asset("broll-doc"), created=created)


def test_broll_is_handed_the_story_and_the_upstream_results_and_asks_for_nothing_new() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import analysis_for, facts_for, plan_of

    content = analysis_for(BATTERIES, "a")
    story_plan = plan_of(facts_for(BATTERIES, "a"))
    owner = Recorder(Ok(broll_result()))
    ex = executors(StageSettings(broll_profile="tutorial"), broll_analyzer=owner)

    result = ex.broll(
        job(
            K.BROLL,
            (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=story_plan)),
            (K.CONTENT_ANALYSIS, TaskResult(TaskStatus.SUCCEEDED, payload=content)),
            (K.ALIGNMENT, TaskResult(TaskStatus.FAILED, payload=object())),
        )
    )

    [command] = owner.commands
    assert command.asset_ids == ("a",) and command.story_plan is story_plan
    assert command.content_analyses == {"a": content}
    assert command.alignments == {} and command.video_analyses == {}
    assert command.config.profile == "tutorial"
    assert result.status is TaskStatus.SUCCEEDED and result.payload is not None
    assert result.facts["broll_asset_id"] == "broll-doc"
    assert result.facts["searched_assets"] == 1


def test_a_stored_broll_analysis_is_reported_as_reused() -> None:
    owner = Recorder(Ok(broll_result(created=False)))

    assert executors(broll_analyzer=owner).broll(job(K.BROLL)).status is TaskStatus.REUSED


def test_a_partial_analysis_is_partial_and_says_so() -> None:
    owner = Recorder(Ok(broll_result(partial=True)))

    result = executors(broll_analyzer=owner).broll(job(K.BROLL))

    assert result.status is TaskStatus.PARTIAL and result.payload is not None


def test_a_recording_with_no_passage_for_footage_is_skipped_not_failed() -> None:
    result = executors(broll_analyzer=Recorder(Err(NothingToCover("no passage")))).broll(
        job(K.BROLL)
    )

    assert result.status is TaskStatus.SKIPPED_UNAVAILABLE


def test_a_broll_error_is_a_failed_task_with_the_reason() -> None:
    result = executors(broll_analyzer=Recorder(FAILING)).broll(job(K.BROLL))

    assert result.status is TaskStatus.FAILED and "Cannot decode" in result.detail


def test_the_edit_task_is_handed_the_broll_analysis_of_the_same_run() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import facts_for, plan_of

    rec = talk([S("the screen is bright and sharp"), S("the speaker is loud", gap=0.4)])
    edited = edit(story(rec, [Sec("a", (0, 1), "essential")]), rec)
    owner = Recorder(Ok(EditResult(edited, make_asset("edit-doc"), created=True)))
    analysis = broll_result().analysis

    executors(edit_planner=owner).edit_plan(
        job(
            K.EDIT_PLAN,
            (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=plan_of(facts_for(BATTERIES, "a")))),
            (K.BROLL, TaskResult(TaskStatus.SUCCEEDED, payload=analysis)),
        )
    )

    [command] = owner.commands
    assert command.broll is analysis


def test_every_task_kind_still_has_an_executor() -> None:
    assert set(executors().table()) == set(TaskKind)
