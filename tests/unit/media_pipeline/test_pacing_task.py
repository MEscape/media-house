"""The ``pacing`` task: where it sits in the graph and what it is handed."""

from dataclasses import replace

from media_house.modules.editing_intelligence.application.contracts import EditResult
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import DEFAULT_STAGES
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.pacing_intelligence.application.analyze_pacing import PacingResult
from media_house.modules.pacing_intelligence.domain.values import ResultState, Strategy
from media_house.shared.errors import Ok
from tests.support.asset_dto import make_asset
from tests.support.edit_fakes import S, Sec, edit, story, talk
from tests.support.pace_fakes import T, analyse, speak
from tests.unit.media_pipeline.test_executors import FAILING, Recorder, executors, job

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


# --- the stage table -------------------------------------------------------------------------
def test_pacing_is_a_default_read_that_needs_only_the_audio_analysis() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.PACING})
    spec = next(s for s in plan.specs if s.id.kind is K.PACING)

    assert K.PACING in DEFAULT_STAGES
    assert spec.needs == {tid(K.AUDIO_ANALYSIS)}
    assert {K.AUDIO_ANALYSIS, K.INSPECT} <= {s.id.kind for s in plan.specs}
    assert not any(tid(K.PACING) in s.upstream for s in plan.specs)  # nothing waits for it


def test_pacing_waits_for_the_other_analyses_when_they_are_in_the_graph() -> None:
    plan = plan_asset(
        "a",
        has_video=True,
        has_audio=True,
        requested={K.PACING, K.CONTENT_ANALYSIS, K.VIDEO_ANALYSIS, K.ALIGNMENT, K.STORY},
    )
    spec = next(s for s in plan.specs if s.id.kind is K.PACING)

    assert spec.wants >= {
        tid(K.CONTENT_ANALYSIS),
        tid(K.VIDEO_ANALYSIS),
        tid(K.ALIGNMENT),
        tid(K.STORY),
    }


def test_the_edit_plan_waits_for_the_pacing_analysis_when_it_is_in_the_graph() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.EDIT_PLAN, K.PACING})
    spec = next(s for s in plan.specs if s.id.kind is K.EDIT_PLAN)

    assert tid(K.PACING) in spec.wants


def test_media_without_audio_gets_no_pacing_task() -> None:
    plan = plan_asset("a", has_video=True, has_audio=False, requested={K.PACING})

    assert K.PACING in plan.not_applicable


# --- the executor ----------------------------------------------------------------------------
def pacing_result(*, created: bool = True, partial: bool = False) -> PacingResult:
    found = analyse(speak([T("this phone has a great screen and a long lasting battery")]))
    if partial:
        found = replace(found, state=ResultState.PARTIAL)
    return PacingResult(found, make_asset("pacing-doc"), created=created)


def test_pacing_is_handed_the_story_and_the_upstream_results_and_asks_for_nothing_new() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import analysis_for, facts_for, plan_of

    content = analysis_for(BATTERIES, "a")
    story_plan = plan_of(facts_for(BATTERIES, "a"))
    owner = Recorder(Ok(pacing_result()))
    ex = executors(
        StageSettings(
            pacing_profile="tutorial", pacing_strategy="concise", edit_target_seconds=90.0
        ),
        pacing_analyzer=owner,
    )

    result = ex.pacing(
        job(
            K.PACING,
            (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=story_plan)),
            (K.CONTENT_ANALYSIS, TaskResult(TaskStatus.SUCCEEDED, payload=content)),
            (K.ALIGNMENT, TaskResult(TaskStatus.FAILED, payload=object())),
        )
    )

    [command] = owner.commands
    assert command.asset_ids == ("a",) and command.story_plan is story_plan
    assert command.content_analyses == {"a": content}
    assert (
        command.alignments == {} and command.video_analyses == {} and command.audio_timelines == {}
    )
    assert command.config.profile == "tutorial" and command.config.strategy is Strategy.CONCISE
    assert command.config.target is not None and command.config.target.maximum == 90.0
    assert result.status is TaskStatus.SUCCEEDED and result.payload is not None
    assert result.facts["pacing_asset_id"] == "pacing-doc"


def test_a_stored_pacing_analysis_is_reported_as_reused() -> None:
    owner = Recorder(Ok(pacing_result(created=False)))

    assert executors(pacing_analyzer=owner).pacing(job(K.PACING)).status is TaskStatus.REUSED


def test_a_partial_analysis_is_partial_and_says_what_was_missing() -> None:
    owner = Recorder(Ok(pacing_result(partial=True)))

    result = executors(pacing_analyzer=owner).pacing(job(K.PACING))

    assert result.status is TaskStatus.PARTIAL and result.payload is not None


def test_a_pacing_error_is_a_failed_task_with_the_reason() -> None:
    result = executors(pacing_analyzer=Recorder(FAILING)).pacing(job(K.PACING))

    assert result.status is TaskStatus.FAILED and "Cannot decode" in result.detail


def test_the_edit_task_is_handed_the_pacing_analysis_of_the_same_run() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import facts_for, plan_of

    rec = talk([S("the screen is bright and sharp"), S("the speaker is loud", gap=0.4)])
    edited = edit(story(rec, [Sec("a", (0, 1), "essential")]), rec)
    owner = Recorder(Ok(EditResult(edited, make_asset("edit-doc"), created=True)))
    analysis = pacing_result().analysis

    executors(edit_planner=owner).edit_plan(
        job(
            K.EDIT_PLAN,
            (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=plan_of(facts_for(BATTERIES, "a")))),
            (K.PACING, TaskResult(TaskStatus.SUCCEEDED, payload=analysis)),
        )
    )

    [command] = owner.commands
    assert command.pacing is analysis


def test_every_task_kind_still_has_an_executor() -> None:
    assert set(executors().table()) == set(TaskKind)
