"""The ``continuity`` task: its place in the graph, what it is handed, the plan -> sequence map."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.continuity_intelligence.application.analyze_continuity import (
    ContinuityResult,
)
from media_house.modules.continuity_intelligence.domain.errors import NothingToJudge
from media_house.modules.continuity_intelligence.domain.values import ResultState
from media_house.modules.editing_intelligence.application.contracts import EditPlan, TrackKind
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import DEFAULT_STAGES
from media_house.modules.media_pipeline.application.sequence import sequence_of
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.support.cont_fakes import jump_analysis
from tests.support.edit_fakes import S, Sec, edit, story, talk
from tests.unit.media_pipeline.test_executors import FAILING, Recorder, executors, job

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


def an_edit() -> EditPlan:
    rec = talk([S("the screen is bright and sharp"), S("the speaker is loud", gap=2.5)])
    return edit(story(rec, [Sec("a", (0,), "essential"), Sec("b", (1,), "essential")]), rec)


# --- the stage table -------------------------------------------------------------------------
def test_continuity_is_opt_in_and_needs_the_edit_plan() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.CONTINUITY})
    spec = next(s for s in plan.specs if s.id.kind is K.CONTINUITY)

    assert K.CONTINUITY not in DEFAULT_STAGES
    assert spec.needs == {tid(K.EDIT_PLAN)}
    assert {K.EDIT_PLAN, K.STORY, K.CONTENT_ANALYSIS} <= {s.id.kind for s in plan.specs}


def test_continuity_waits_for_the_other_analyses_when_they_are_in_the_graph() -> None:
    plan = plan_asset(
        "a",
        has_video=True,
        has_audio=True,
        requested={K.CONTINUITY, K.VIDEO_ANALYSIS, K.ALIGNMENT, K.BROLL},
    )
    spec = next(s for s in plan.specs if s.id.kind is K.CONTINUITY)

    assert spec.wants >= {tid(K.VIDEO_ANALYSIS), tid(K.ALIGNMENT), tid(K.BROLL)}


# --- the executor ----------------------------------------------------------------------------
def result(*, created: bool = True, partial: bool = False) -> ContinuityResult:
    found = jump_analysis()
    if partial:
        found = replace(found, state=ResultState.PARTIAL)
    return ContinuityResult(found, make_asset("cont-doc"), created=created)


def upstream(plan: EditPlan) -> tuple[TaskKind, TaskResult]:
    return (K.EDIT_PLAN, TaskResult(TaskStatus.SUCCEEDED, payload=plan))


def test_continuity_is_handed_the_plan_of_the_same_run_as_a_sequence() -> None:
    plan = an_edit()
    owner = Recorder(Ok(result()))
    ex = executors(
        StageSettings(continuity_profile="cinematic_dialogue", continuity_strictness=1.2),
        continuity_analyzer=owner,
    )

    out = ex.continuity(job(K.CONTINUITY, upstream(plan)))

    [command] = owner.commands
    assert command.sequence.edit_plan_id == plan.edit_plan_id
    assert command.sequence.edit_plan_version == plan.plan_version
    assert command.config.profile == "cinematic_dialogue" and command.config.strictness == 1.2
    assert out.status is TaskStatus.SUCCEEDED and out.facts["continuity_asset_id"] == "cont-doc"


def test_without_an_edit_plan_there_is_nothing_to_judge() -> None:
    assert executors().continuity(job(K.CONTINUITY)).status is TaskStatus.SKIPPED_DEPENDENCY


def test_reused_partial_error_and_no_cuts_are_reported_for_what_they_are() -> None:
    given = upstream(an_edit())

    def run(owner: Recorder):  # type: ignore[no-untyped-def]
        return executors(continuity_analyzer=owner).continuity(job(K.CONTINUITY, given))

    assert run(Recorder(Ok(result(created=False)))).status is TaskStatus.REUSED
    assert run(Recorder(Ok(result(partial=True)))).status is TaskStatus.PARTIAL
    failed = run(Recorder(FAILING))
    assert failed.status is TaskStatus.FAILED and "Cannot decode" in failed.detail
    assert run(Recorder(Err(NothingToJudge()))).status is TaskStatus.SKIPPED_UNAVAILABLE


def test_the_sequence_keeps_source_ranges_timeline_places_and_the_format() -> None:
    plan = an_edit()

    seq = sequence_of(plan)

    mains = [c for c in seq.clips if c.layer == 0]
    pictures = [p for p in plan.track(TrackKind.VIDEO) if p.layer == 0]
    assert [c.clip_id for c in mains] == [p.placement_id for p in pictures]
    first = pictures[0]
    assert mains[0].source_start == Fraction(
        first.source.start.numerator, first.source.start.denominator
    )
    assert mains[0].timeline_end - mains[0].timeline_start == Fraction(
        first.timeline_duration.numerator, first.timeline_duration.denominator
    )
    assert seq.story_format == plan.target_format


def test_every_task_kind_still_has_an_executor() -> None:
    assert set(executors().table()) == set(TaskKind)
