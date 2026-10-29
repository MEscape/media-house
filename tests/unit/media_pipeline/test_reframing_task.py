"""The ``reframing`` task: opt-in, needs a target, and what the edit plan is handed."""

from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import DEFAULT_STAGES
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.reframing_intelligence.application.analyze_reframing import (
    ReframingResult,
)
from media_house.modules.reframing_intelligence.domain.errors import NothingToFrame
from media_house.modules.reframing_intelligence.domain.profile import SafeArea
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.support.refr_fakes import T169, T916, analyse, shot, still
from tests.unit.media_pipeline.test_executors import FAILING, Recorder, executors, job

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


# --- the stage table -------------------------------------------------------------------------
def test_reframing_is_opt_in_and_needs_the_video_analysis() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.REFRAMING})
    spec = next(s for s in plan.specs if s.id.kind is K.REFRAMING)

    assert K.REFRAMING not in DEFAULT_STAGES
    assert spec.needs == {tid(K.VIDEO_ANALYSIS)}
    assert plan.implied >= {K.VIDEO_ANALYSIS}


def test_reframing_waits_for_the_story_and_the_alignment_when_they_are_in_the_graph() -> None:
    plan = plan_asset(
        "a", has_video=True, has_audio=True, requested={K.REFRAMING, K.STORY, K.ALIGNMENT}
    )
    spec = next(s for s in plan.specs if s.id.kind is K.REFRAMING)

    assert spec.wants >= {tid(K.STORY), tid(K.ALIGNMENT), tid(K.INSPECT)}
    assert tid(K.CONTINUITY) not in spec.upstream  # it runs after the plan that wants this


def test_the_edit_plan_waits_for_reframing_and_nothing_waits_for_the_edit_plan_cycle() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.REFRAMING, K.EDIT_PLAN})
    edit = next(s for s in plan.specs if s.id.kind is K.EDIT_PLAN)

    assert tid(K.REFRAMING) in edit.wants
    assert not any(tid(K.EDIT_PLAN) in s.upstream for s in plan.specs)


def test_a_picture_less_asset_cannot_be_reframed() -> None:
    plan = plan_asset("a", has_video=False, has_audio=True, requested={K.REFRAMING})

    assert K.REFRAMING in plan.not_applicable


# --- the executor ----------------------------------------------------------------------------
def result(*, created: bool = True, partial: bool = False) -> ReframingResult:
    shots = (
        (shot("s0", 0, 4, ()),)
        if partial
        else (shot("s0", 0, 4, (still("p", (0.4, 0.2, 0.6, 0.9)),)),)
    )
    return ReframingResult(analyse(*shots, target=T916), make_asset("refr-doc"), created=created)


def test_no_target_means_no_crop_and_the_owner_is_never_asked() -> None:
    owner = Recorder(Ok(result()))

    out = executors(StageSettings(), reframing_analyzer=owner).reframing(job(K.REFRAMING))

    assert out.status is TaskStatus.SKIPPED_UNAVAILABLE and "target format" in out.detail
    assert owner.commands == []


def test_the_owner_is_handed_the_target_profile_safe_areas_and_the_upstream_results() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import facts_for, plan_of

    story_plan = plan_of(facts_for(BATTERIES, "a"))
    owner = Recorder(Ok(result()))
    areas = (SafeArea("captions", 0.05, 0.7, 0.95, 0.98),)
    ex = executors(
        StageSettings(
            reframing_target="9:16",
            reframing_resolution=(720, 1280),
            reframing_profile="shorts_reels",
            reframing_safe_areas=areas,
        ),
        reframing_analyzer=owner,
    )

    out = ex.reframing(
        job(K.REFRAMING, (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=story_plan)))
    )

    [command] = owner.commands
    assert command.asset_ids == ("a",) and command.story_plan is story_plan
    assert (command.target.name, command.target.width, command.target.height) == ("9:16", 720, 1280)
    assert command.config.profile == "shorts_reels" and command.config.safe_areas == areas
    assert command.video_analyses == {} and command.alignments == {}  # nothing delivered
    assert out.status is TaskStatus.SUCCEEDED and out.payload is not None
    assert out.facts["reframing_asset_id"] == "refr-doc" and out.facts["target"] == "9:16"


def test_a_bad_target_is_a_failed_task_not_a_crash() -> None:
    owner = Recorder(Ok(result()))

    out = executors(StageSettings(reframing_target="wide"), reframing_analyzer=owner).reframing(
        job(K.REFRAMING)
    )

    assert out.status is TaskStatus.FAILED and owner.commands == []


def test_reused_partial_error_and_no_shots_are_reported_for_what_they_are() -> None:
    settings = StageSettings(reframing_target="9:16")

    def run(owner: Recorder):  # type: ignore[no-untyped-def]
        return executors(settings, reframing_analyzer=owner).reframing(job(K.REFRAMING))

    assert run(Recorder(Ok(result(created=False)))).status is TaskStatus.REUSED
    assert run(Recorder(Ok(result(partial=True)))).status is TaskStatus.PARTIAL
    failed = run(Recorder(FAILING))
    assert failed.status is TaskStatus.FAILED and "Cannot decode" in failed.detail
    assert run(Recorder(Err(NothingToFrame()))).status is TaskStatus.SKIPPED_UNAVAILABLE


def test_the_edit_task_is_handed_the_framing_of_the_same_run_and_its_target() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import facts_for, plan_of
    from tests.unit.media_pipeline.test_edit_plan_task import edit_result

    story_plan = plan_of(facts_for(BATTERIES, "a"))
    framing = result(partial=False).analysis
    owner = Recorder(Ok(edit_result()))
    ex = executors(StageSettings(reframing_target="16:9"), edit_planner=owner)

    ex.edit_plan(
        job(
            K.EDIT_PLAN,
            (K.STORY, TaskResult(TaskStatus.SUCCEEDED, payload=story_plan)),
            (K.REFRAMING, TaskResult(TaskStatus.SUCCEEDED, payload=framing)),
        )
    )

    [command] = owner.commands
    assert command.reframing is framing and command.reframing_target == "16:9"
    assert T169.name == "16:9"
