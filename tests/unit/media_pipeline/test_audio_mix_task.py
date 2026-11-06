"""The audio mix task: its place in the graph, what the engine is handed, how it ends."""

from types import SimpleNamespace
from typing import Any

from media_house.modules.audio_mix_engine.application.contracts import (
    JobState,
    NothingToMix,
    RenderMixCommand,
    Severity,
)
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.run_pipeline import (
    DEFAULT_STAGES,
    LAYER5_STAGES,
)
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.shared.errors import Err, Ok, ValidationError
from tests.support.asset_dto import make_asset
from tests.unit.media_pipeline.test_executors import Recorder, executors, job
from tests.unit.timeline_builder import fixtures as bf

K = TaskKind


def built_result() -> TaskResult:
    return TaskResult(TaskStatus.SUCCEEDED, payload=bf.built())


def done(
    state: JobState = JobState.COMPLETED, *, reused: bool = False, issues: tuple[Any, ...] = ()
) -> Any:
    measurement = SimpleNamespace(integrated_lufs=-14.1, true_peak_dbtp=-1.6)
    stems = (SimpleNamespace(reused=reused), SimpleNamespace(reused=reused))
    result = SimpleNamespace(
        state=state, profile="talking_head", delivery="youtube", master_trim_db=1.5, stems=stems,
        mix=SimpleNamespace(asset_id="mix-1", reused=reused, measurement=measurement),
        issues=issues, errors=("backend broke",) if state is JobState.FAILED else (),
        reused_stems=2 if reused else 0,
    )  # fmt: skip
    return SimpleNamespace(
        result=result, spec_asset=make_asset("spec-1"), result_asset=make_asset("res-1")
    )


def issue(severity: Severity, text: str) -> Any:
    return SimpleNamespace(severity=severity, message=text)


def test_the_task_is_opt_in_in_no_group_and_independent_of_resolve() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=frozenset({K.AUDIO_MIX}))
    spec = next(s for s in plan.specs if s.id.kind is K.AUDIO_MIX)

    assert K.AUDIO_MIX not in DEFAULT_STAGES and K.AUDIO_MIX not in LAYER5_STAGES
    assert spec.needs == {TaskId("a", K.TIMELINE_BUILD)}
    assert K.RESOLVE_EXECUTE not in plan.implied and K.TIMELINE_BUILD in plan.implied


def test_nothing_waits_for_the_mix() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=set(K))

    assert TaskId("a", K.AUDIO_MIX) not in {u for s in plan.specs for u in s.upstream}


def test_the_engine_is_handed_the_built_timeline_and_the_runs_choices() -> None:
    owner = Recorder(Ok(done()))

    executors(
        StageSettings(mix_profile="tutorial", mix_delivery="shorts"), audio_mixer=owner
    ).audio_mix(job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result())))

    [command] = owner.commands
    assert isinstance(command, RenderMixCommand) and command.timeline is not None
    assert (command.config.profile, command.config.delivery) == ("tutorial", "shorts")


def test_a_finished_mix_is_a_success_with_its_measurements() -> None:
    out = executors(audio_mixer=Recorder(Ok(done()))).audio_mix(
        job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.SUCCEEDED
    assert out.facts["mix_asset_id"] == "mix-1" and out.facts["integrated_lufs"] == -14.1
    assert (out.facts["stems"], out.facts["delivery"], out.facts["result_asset_id"]) == (
        2,
        "youtube",
        "res-1",
    )


def test_an_unchanged_mix_is_reused() -> None:
    out = executors(audio_mixer=Recorder(Ok(done(reused=True)))).audio_mix(
        job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.REUSED and out.facts["stems_reused"] == 2


def test_errors_against_the_delivery_make_it_partial_and_are_listed() -> None:
    bad = (issue(Severity.ERROR, "loudness out of range"), issue(Severity.WARNING, "music is loud"))

    out = executors(audio_mixer=Recorder(Ok(done(JobState.PARTIAL, issues=bad)))).audio_mix(
        job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.PARTIAL
    assert out.facts["errors"] == 1 and out.facts["warnings"] == 1
    assert out.warnings == ("loudness out of range", "music is loud")


def test_a_failed_render_is_a_failure_with_the_reason() -> None:
    out = executors(audio_mixer=Recorder(Ok(done(JobState.FAILED)))).audio_mix(
        job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.FAILED and "backend broke" in out.detail


def test_a_timeline_with_no_sound_is_unavailable_not_a_failure() -> None:
    out = executors(audio_mixer=Recorder(Err(NothingToMix()))).audio_mix(
        job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.SKIPPED_UNAVAILABLE


def test_any_other_error_is_a_failure() -> None:
    out = executors(audio_mixer=Recorder(Err(ValidationError("no")))).audio_mix(
        job(K.AUDIO_MIX, (K.TIMELINE_BUILD, built_result()))
    )

    assert out.status is TaskStatus.FAILED


def test_without_a_built_timeline_the_engine_is_never_asked() -> None:
    owner = Recorder(Ok(done()))

    out = executors(audio_mixer=owner).audio_mix(job(K.AUDIO_MIX))

    assert out.status is TaskStatus.FAILED and owner.commands == []
