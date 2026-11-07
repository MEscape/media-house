"""The quality control task: its place in the graph, what the analyzer is handed, how it ends."""

from types import SimpleNamespace
from typing import Any

from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import (
    DEFAULT_STAGES,
    LAYER4_STAGES,
    LAYER5_STAGES,
)
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.quality_control.application.contracts import AnalyzeQualityCommand
from media_house.shared.errors import Err, Ok, ValidationError
from tests.support.asset_dto import make_asset
from tests.unit.media_pipeline.test_executors import Recorder, executors, job
from tests.unit.timeline_builder import fixtures as bf

K = TaskKind


def built_result() -> TaskResult:
    return TaskResult(TaskStatus.SUCCEEDED, payload=bf.built())


def done(*, blocking: int = 0, created: bool = True) -> Any:
    summary = SimpleNamespace(critical=blocking, high=1, medium=2, low=3, optional=0)
    issue = SimpleNamespace(description="a gap on the main picture")
    report = SimpleNamespace(
        quality_report_id="qr_1",
        technical_status=SimpleNamespace(value="blocked" if blocking else "warnings"),
        review_status=SimpleNamespace(value="needs_attention"),
        severity_summary=summary,
        export_blocking_issues=("q1",) * blocking,
        detected_issues=(issue,),
    )
    return SimpleNamespace(report=report, asset=make_asset("qr-asset"), created=created)


def test_the_task_is_opt_in_in_no_group_and_waits_for_nothing() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=frozenset({K.QUALITY_CONTROL}))
    spec = next(s for s in plan.specs if s.id.kind is K.QUALITY_CONTROL)

    assert K.QUALITY_CONTROL not in DEFAULT_STAGES | LAYER4_STAGES | LAYER5_STAGES
    assert TaskId("a", K.TIMELINE_BUILD) in spec.needs
    everything = plan_asset("a", has_video=True, has_audio=True, requested=set(K))
    assert TaskId("a", K.QUALITY_CONTROL) not in {u for s in everything.specs for u in s.upstream}


def test_it_needs_neither_the_mix_nor_resolve_it_reads_what_the_library_holds() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=frozenset({K.QUALITY_CONTROL}))
    spec = next(s for s in plan.specs if s.id.kind is K.QUALITY_CONTROL)

    assert K.AUDIO_MIX not in plan.implied and K.RESOLVE_EXECUTE not in plan.implied
    assert spec.needs == {TaskId("a", K.TIMELINE_BUILD)}


def test_the_analyzer_is_handed_the_timeline_and_nothing_to_render() -> None:
    owner = Recorder(Ok(done()))

    executors(StageSettings(), quality_analyzer=owner).quality_control(
        job(K.QUALITY_CONTROL, (K.TIMELINE_BUILD, built_result()))
    )

    [command] = owner.commands
    built = bf.built()
    assert isinstance(command, AnalyzeQualityCommand)
    assert (
        command.anchor_asset_id == built.anchor_asset_id
        and command.timeline_id == built.timeline_id
    )
    assert command.preview_asset_id is None and command.review_session_id is None


def test_a_report_is_a_success_with_its_counts_and_a_repeat_is_reused() -> None:
    first = executors(quality_analyzer=Recorder(Ok(done()))).quality_control(
        job(K.QUALITY_CONTROL, (K.TIMELINE_BUILD, built_result()))
    )
    again = executors(quality_analyzer=Recorder(Ok(done(created=False)))).quality_control(
        job(K.QUALITY_CONTROL, (K.TIMELINE_BUILD, built_result()))
    )

    assert first.status is TaskStatus.SUCCEEDED and again.status is TaskStatus.REUSED
    assert first.facts["quality_report_id"] == "qr_1" and first.facts["high"] == 1
    assert first.warnings == ("a gap on the main picture",)


def test_a_timeline_with_export_blocking_findings_is_partial_not_a_failed_run() -> None:
    out = executors(quality_analyzer=Recorder(Ok(done(blocking=2)))).quality_control(
        job(K.QUALITY_CONTROL, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.PARTIAL and out.facts["export_blocking"] == 2


def test_no_built_timeline_or_an_owner_error_is_a_failure() -> None:
    nothing = executors(quality_analyzer=Recorder(Ok(done()))).quality_control(
        job(K.QUALITY_CONTROL)
    )
    broke = executors(
        quality_analyzer=Recorder(Err(ValidationError("no", user_message="No.")))
    ).quality_control(job(K.QUALITY_CONTROL, (K.TIMELINE_BUILD, built_result())))

    assert nothing.status is TaskStatus.FAILED and broke.status is TaskStatus.FAILED
