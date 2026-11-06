"""The Layer 5 build task: its place in the graph, what the builder is handed, how it ends."""

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
from media_house.modules.timeline_builder.application.contracts import (
    BuildTimelineCommand,
    ConflictPolicy,
    FitMode,
    TimelineNotBuildable,
)
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.unit.media_pipeline.test_executors import Recorder, executors, job
from tests.unit.media_pipeline.test_layer4_tasks import INSPECTED, STYLE
from tests.unit.timeline_builder import fixtures as bf

K = TaskKind


def tid(kind: TaskKind) -> TaskId:
    return TaskId("a", kind)


def spec_result() -> TaskResult:
    return TaskResult(
        TaskStatus.SUCCEEDED, facts={"plan_asset_id": "tl-doc"}, payload=bf.compiled()
    )


def built_result(created: bool = True) -> Any:
    return SimpleNamespace(timeline=bf.built(), asset=make_asset("bt-doc"), created=created)


# --- the stage table -------------------------------------------------------------------------
def test_the_build_is_opt_in_and_pulls_in_the_timeline_with_what_it_needs() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=frozenset({K.TIMELINE_BUILD}))
    spec = next(s for s in plan.specs if s.id.kind is K.TIMELINE_BUILD)

    assert K.TIMELINE_BUILD in LAYER5_STAGES and not LAYER5_STAGES & DEFAULT_STAGES
    assert spec.needs == {tid(K.TIMELINE)}
    assert K.TIMELINE in plan.implied and K.VISUAL_STYLE in plan.implied


def test_nothing_waits_for_the_build() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=LAYER5_STAGES)

    assert tid(K.TIMELINE_BUILD) not in {u for s in plan.specs for u in s.upstream}


# --- the executor ----------------------------------------------------------------------------
def test_the_builder_is_handed_the_specification_of_the_run_and_its_document() -> None:
    owner = Recorder(Ok(built_result()))

    out = executors(timeline_builder=owner).timeline_build(
        job(K.TIMELINE_BUILD, STYLE, INSPECTED, (K.TIMELINE, spec_result()))
    )

    [command] = owner.commands
    assert isinstance(command, BuildTimelineCommand)
    assert command.spec is not None and command.spec_asset_id == "tl-doc"
    assert command.config.conflict_policy is ConflictPolicy.REVIEW
    assert command.config.fit_mode is FitMode.CONTAIN and not command.full_rebuild
    assert out.payload is not None


def test_the_task_reports_the_built_timeline_in_its_facts() -> None:
    built = built_result()
    owner = Recorder(Ok(built))

    out = executors(timeline_builder=owner).timeline_build(
        job(K.TIMELINE_BUILD, (K.TIMELINE, spec_result()))
    )

    t = built.timeline
    assert out.facts["document_type"] == "built_timeline" and out.facts["plan_asset_id"] == "bt-doc"
    assert out.facts["timeline_id"] == t.timeline_id and out.facts["build_hash"]
    assert (out.facts["video_clips"], out.facts["audio_clips"]) == (
        t.counts.video_clips,
        t.counts.audio_clips,
    )
    assert out.facts["mode"] == "new" and out.facts["state"] == t.status.value
    expected = TaskStatus.PARTIAL if t.status.value == "partial" else TaskStatus.SUCCEEDED
    assert out.status is expected


def test_an_identical_build_is_reused() -> None:
    owner = Recorder(Ok(built_result(created=False)))

    out = executors(timeline_builder=owner).timeline_build(
        job(K.TIMELINE_BUILD, (K.TIMELINE, spec_result()))
    )

    assert out.status in (TaskStatus.REUSED, TaskStatus.PARTIAL)


def test_the_run_settings_reach_the_builder() -> None:
    owner = Recorder(Ok(built_result()))
    settings = StageSettings(
        timeline_conflict_policy="preserve", timeline_fit="cover", timeline_full_rebuild=True
    )

    executors(settings, timeline_builder=owner).timeline_build(
        job(K.TIMELINE_BUILD, (K.TIMELINE, spec_result()))
    )

    command = owner.commands[0]
    assert command.config.conflict_policy is ConflictPolicy.PRESERVE
    assert command.config.fit_mode is FitMode.COVER and command.full_rebuild


def test_a_wrong_setting_fails_clearly_without_calling_the_builder() -> None:
    owner = Recorder(Ok(built_result()))

    out = executors(
        StageSettings(timeline_conflict_policy="maybe"), timeline_builder=owner
    ).timeline_build(job(K.TIMELINE_BUILD, (K.TIMELINE, spec_result())))

    assert out.status is TaskStatus.FAILED and "preserve, replace or review" in out.detail
    assert owner.commands == []


def test_without_a_specification_there_is_nothing_to_build_and_the_builder_is_never_asked() -> None:
    owner = Recorder(Ok(built_result()))

    out = executors(timeline_builder=owner).timeline_build(job(K.TIMELINE_BUILD))

    assert out.status is TaskStatus.FAILED and "no specification" in out.detail
    assert owner.commands == []


def test_a_timeline_that_cannot_be_built_is_a_failure_with_the_reasons() -> None:
    owner = Recorder(Err(TimelineNotBuildable(["rec1 is not in the library any more"])))

    out = executors(timeline_builder=owner).timeline_build(
        job(K.TIMELINE_BUILD, (K.TIMELINE, spec_result()))
    )

    assert out.status is TaskStatus.FAILED
    assert "timeline_builder.not_buildable" in out.detail and "rec1" in out.detail
