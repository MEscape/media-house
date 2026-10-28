"""Executors against fake owners: stream facts, CPU-only settings, honest statuses."""

from dataclasses import replace
from typing import Any

from media_house.core.infrastructure.system_clock import SystemClock
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.media_inspection.domain.model import Integrity
from media_house.modules.media_pipeline.application.executors import ModuleExecutors, StageSettings
from media_house.modules.media_pipeline.application.tasks import TaskJob, TaskResult
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, ValidationError
from tests.support.asset_dto import make_asset
from tests.support.inspection_fakes import audio, inspect, observed, video

K = TaskKind
ASSET = "a"


class Recorder:
    """Stands in for any owner: records the command, answers with a canned result."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.commands: list[Any] = []

    def execute(self, command: Any, ctx: JobContext) -> Any:
        self.commands.append(command)
        return self.answer

    analyze = execute  # RelationshipDetection answers a single asset with ``analyze``


def executors(settings: StageSettings | None = None, **owners: Any) -> ModuleExecutors:
    fail = Recorder(Err(ValidationError("unexpected call")))
    return ModuleExecutors(
        library=owners.get("library", fail),
        clock=owners.get("clock", SystemClock()),
        inspector=owners.get("inspector", fail),
        proxies=owners.get("proxies", fail),
        audio_improver=owners.get("audio_improver", fail),
        video_improver=owners.get("video_improver", fail),
        audio_analyzer=owners.get("audio_analyzer", fail),
        video_analyzer=owners.get("video_analyzer", fail),
        content_analyzer=owners.get("content_analyzer", fail),
        aligner=owners.get("aligner", fail),
        story_planner=owners.get("story_planner", fail),
        pacing_analyzer=owners.get("pacing_analyzer", fail),
        edit_planner=owners.get("edit_planner", fail),
        relationships=owners.get("relationships", fail),
        settings=settings,
    )


def inspection(*, audios: int, readable: bool = True) -> TaskResult:
    media = observed(
        videos=(video(),),
        audios=tuple(audio() for _ in range(audios)),
        integrity=Integrity(readable=readable, decode_checked=readable),
    )
    result = InspectionResult(inspect(media), make_asset("insp"), created=True)
    return TaskResult(TaskStatus.SUCCEEDED, payload=result)


def job(kind: K, *upstream: tuple[K, TaskResult]) -> TaskJob:
    return TaskJob(
        TaskId(ASSET, kind),
        {TaskId(ASSET, k): r for k, r in upstream},
        JobContext.detached(),
    )


FAILING = Err(ValidationError("decoder says no", user_message="Cannot decode."))


def test_proven_silence_skips_audio_work_without_calling_the_owners() -> None:
    owner = Recorder(FAILING)
    ex = executors(audio_improver=owner, audio_analyzer=owner)
    given = (K.INSPECT, inspection(audios=0))

    improved = ex.improve_audio(job(K.AUDIO_IMPROVEMENT, given))
    analysed = ex.analyze_audio(job(K.AUDIO_ANALYSIS, given))

    assert improved.status is TaskStatus.SKIPPED_UNAVAILABLE
    assert analysed.status is TaskStatus.SKIPPED_UNAVAILABLE
    assert owner.commands == []


def test_missing_inspection_never_means_no_audio() -> None:
    owner = Recorder(FAILING)
    ex = executors(audio_improver=owner)

    result = ex.improve_audio(job(K.AUDIO_IMPROVEMENT))  # no inspection result at all

    assert owner.commands  # the owner was asked and decided
    assert result.status is TaskStatus.FAILED


def test_failed_inspection_never_means_no_audio() -> None:
    owner = Recorder(FAILING)
    ex = executors(audio_improver=owner)
    failed = TaskResult(TaskStatus.FAILED, "ffprobe crashed")

    result = ex.improve_audio(job(K.AUDIO_IMPROVEMENT, (K.INSPECT, failed)))

    assert owner.commands
    assert result.status is TaskStatus.FAILED


def test_unreadable_media_is_not_reported_as_silent() -> None:
    owner = Recorder(FAILING)
    ex = executors(audio_analyzer=owner)

    result = ex.analyze_audio(
        job(K.AUDIO_ANALYSIS, (K.INSPECT, inspection(audios=0, readable=False)))
    )

    assert owner.commands  # an unusable inspection proves nothing about streams
    assert result.status is TaskStatus.FAILED


def test_audio_decoder_failure_with_an_audio_stream_present_is_a_failure() -> None:
    owner = Recorder(
        Err(ValidationError("x", user_message="This file could not be read as audio."))
    )
    ex = executors(audio_improver=owner)

    result = ex.improve_audio(job(K.AUDIO_IMPROVEMENT, (K.INSPECT, inspection(audios=1))))

    assert result.status is TaskStatus.FAILED
    assert "could not be read as audio" in result.detail


def test_owner_no_audio_track_error_is_unavailable_not_failed() -> None:
    class NoAudio(ValidationError):
        code = "audio_intelligence.no_audio_track"

    owner = Recorder(Err(NoAudio("none", user_message="No audio track.")))

    result = executors(audio_analyzer=owner).analyze_audio(job(K.AUDIO_ANALYSIS))

    assert result.status is TaskStatus.SKIPPED_UNAVAILABLE


def test_cpu_only_sets_every_device_and_default_leaves_them_alone() -> None:
    audio_owner = Recorder(FAILING)
    video_owner = Recorder(FAILING)
    content_owner = Recorder(FAILING)

    for settings in (StageSettings(), StageSettings(cpu_only=True)):
        ex = executors(
            settings,
            audio_analyzer=audio_owner,
            video_analyzer=video_owner,
            content_analyzer=content_owner,
        )
        ex.analyze_audio(job(K.AUDIO_ANALYSIS))
        ex.analyze_video(job(K.VIDEO_ANALYSIS))
        ex.analyze_content(job(K.CONTENT_ANALYSIS))

    auto_audio, cpu_audio = audio_owner.commands
    auto_video, cpu_video = video_owner.commands
    auto_content, cpu_content = content_owner.commands
    assert auto_audio.config.transcription.device == "auto"
    assert cpu_audio.config.transcription.device == "cpu"
    assert auto_video.runtime.device.value == "auto"
    assert cpu_video.runtime.device.value == "cpu"
    assert auto_content.config.embedding.device == "auto"
    assert cpu_content.config.embedding.device == "cpu"


def test_content_receives_the_upstream_timeline_only_when_one_was_delivered() -> None:
    owner = Recorder(FAILING)
    ex = executors(content_analyzer=owner)
    timeline = replace  # any object that is not a timeline must be ignored, not passed on

    ex.analyze_content(
        job(
            K.CONTENT_ANALYSIS,
            (K.AUDIO_ANALYSIS, TaskResult(TaskStatus.SUCCEEDED, payload=timeline)),
        )
    )

    assert owner.commands[0].audio_timeline is None
    assert owner.commands[0].video_analysis is None


def test_peeks_exist_only_for_owners_with_an_exact_side_effect_free_check() -> None:
    assert set(executors().peeks()) == {K.INSPECT, K.THUMBNAIL, K.PROXY}  # story has none yet


def test_a_damaged_media_verdict_fails_the_inspection_task_with_the_evidence() -> None:
    from tests.support.inspection_fakes import codes

    media = observed(
        videos=(video(),),
        audios=(audio(),),
        integrity=Integrity(
            readable=False, decode_checked=False, read_message_count=3, decode_message_count=2
        ),
    )
    broken = InspectionResult(inspect(media), make_asset("insp"), created=True)
    owner = Recorder(Ok(broken))

    result = executors(inspector=owner).inspect(job(K.INSPECT))

    assert codes(broken.inspection)  # the fixture really produced findings
    assert result.status is TaskStatus.FAILED
    assert "damaged or unusable" in result.detail
    assert result.warnings  # the findings travel with the failure
    assert result.payload is None  # nothing downstream may treat it as a sound inspection


def test_a_sound_inspection_is_delivered_with_its_payload() -> None:
    sound = inspection(audios=1).payload
    assert isinstance(sound, InspectionResult)
    owner = Recorder(Ok(sound))

    fresh = executors(inspector=owner).inspect(job(K.INSPECT))

    assert fresh.status is TaskStatus.SUCCEEDED and fresh.payload is sound


# --- story ------------------------------------------------------------------------------------
def story_result(*stages: tuple[str, str], created: bool = True):  # type: ignore[no-untyped-def]
    from media_house.modules.story_architecture.application.contracts import (
        StageState,
        StoryResult,
    )
    from media_house.modules.story_architecture.domain.model import StageReport
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import facts_for, plan_of

    plan = plan_of(facts_for(BATTERIES))
    plan = replace(plan, stages=tuple(StageReport(stage=n, state=StageState(s)) for n, s in stages))
    return StoryResult(plan, make_asset("story-doc"), created=created)


def test_story_is_handed_the_upstream_results_and_never_asked_to_compute_them() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import analysis_for

    analysis = analysis_for(BATTERIES, ASSET)
    owner = Recorder(Ok(story_result(("sections", "ok"))))
    ex = executors(StageSettings(story_format="review", story_llm=True), story_planner=owner)

    result = ex.story(
        job(
            K.STORY,
            (K.CONTENT_ANALYSIS, TaskResult(TaskStatus.SUCCEEDED, payload=analysis)),
            (K.ALIGNMENT, TaskResult(TaskStatus.FAILED, payload=object())),
        )
    )

    [command] = owner.commands
    assert command.asset_ids == (ASSET,)
    assert command.content_analyses == {ASSET: analysis}
    assert (
        command.alignments == {}
    )  # a failed alignment delivered nothing; the planner reads the store
    assert command.acquire.value == "stored"  # the pipeline never asks the planner to analyse
    assert command.config.format == "review" and command.llm is True
    assert result.status is TaskStatus.SUCCEEDED
    assert result.payload is not None and result.facts["story_asset_id"] == "story-doc"


def test_a_stored_plan_is_reported_as_reused() -> None:
    owner = Recorder(Ok(story_result(("sections", "ok"), created=False)))

    result = executors(story_planner=owner).story(job(K.STORY))

    assert result.status is TaskStatus.REUSED


def test_a_missing_picture_side_is_not_a_degradation_but_a_failed_stage_is() -> None:
    absent = Recorder(Ok(story_result(("visuals", "not_available"))))
    failed = Recorder(Ok(story_result(("refinement", "failed"))))
    declined = Recorder(Ok(story_result(("refinement", "not_available"))))

    assert executors(story_planner=absent).story(job(K.STORY)).status is TaskStatus.SUCCEEDED
    broken = executors(story_planner=failed).story(job(K.STORY))
    assert broken.status is TaskStatus.PARTIAL and broken.warnings == ("refinement: failed",)
    assert executors(story_planner=declined).story(job(K.STORY)).status is TaskStatus.PARTIAL


def test_a_story_error_is_a_failed_task_with_the_reason() -> None:
    result = executors(story_planner=Recorder(FAILING)).story(job(K.STORY))

    assert result.status is TaskStatus.FAILED and "Cannot decode" in result.detail


# --- relationships ----------------------------------------------------------------------------
def analysis_of(*results, available: bool = True):  # type: ignore[no-untyped-def]
    from media_house.modules.asset_relationships.application.contracts import (
        AssetAnalysis,
        DetectorResult,
        MarkOutcome,
    )

    return AssetAnalysis(
        ASSET,
        available,
        tuple(DetectorResult(name, MarkOutcome.NONE, **flags) for name, flags in results),
    )


def test_relationships_ask_for_every_detector_on_this_asset_only() -> None:
    from media_house.modules.asset_relationships.application.contracts import DETECTOR_IDS

    owner = Recorder(Ok(analysis_of(("wall_clock_overlap", {"proposed": 2}))))

    result = executors(relationships=owner).relationships(job(K.RELATIONSHIPS))

    [command] = owner.commands
    assert command.asset_id == ASSET and command.detectors == frozenset(DETECTOR_IDS)
    assert result.status is TaskStatus.SUCCEEDED and result.facts["proposed"] == 2


def test_an_unchanged_analysis_is_reused_and_a_missing_inspection_is_skipped_not_probed() -> None:
    unchanged = Recorder(Ok(analysis_of(("wall_clock_overlap", {"unchanged": True}))))
    absent = Recorder(Ok(analysis_of(available=False)))

    assert (
        executors(relationships=unchanged).relationships(job(K.RELATIONSHIPS)).status
        is TaskStatus.REUSED
    )
    skipped = executors(relationships=absent).relationships(job(K.RELATIONSHIPS))
    assert skipped.status is TaskStatus.SKIPPED_UNAVAILABLE and "not probed" in skipped.detail


def test_a_capped_asset_is_partial_and_says_so() -> None:
    owner = Recorder(Ok(analysis_of(("wall_clock_overlap", {"proposed": 25, "capped": 3}))))

    result = executors(relationships=owner).relationships(job(K.RELATIONSHIPS))

    assert result.status is TaskStatus.PARTIAL and result.warnings == (
        "wall_clock_overlap: capped",
    )
