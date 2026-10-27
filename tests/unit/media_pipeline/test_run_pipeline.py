"""The use case end to end with a fake library and fake executors: report truthfulness."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from media_house.modules.media_library.application.contracts import (
    ImportResultDto,
    MediaAssetDto,
    MediaType,
)
from media_house.modules.media_pipeline.application.report import PipelineReport
from media_house.modules.media_pipeline.application.run_pipeline import (
    RunPipeline,
    RunPipelineCommand,
)
from media_house.modules.media_pipeline.application.tasks import (
    TaskExecutor,
    TaskJob,
    TaskPeek,
    TaskResult,
)
from media_house.modules.media_pipeline.domain.values import TaskKind, TaskStatus
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, NotFoundError, Ok, ValidationError
from tests.support.asset_dto import make_asset

K = TaskKind


class FakeLibrary:
    """Only what the use case calls: ``import_file`` and ``get``."""

    def __init__(self, assets: Sequence[MediaAssetDto]) -> None:
        self._by_name = {a.original_filename: a for a in assets}
        self._by_id = {a.id: a for a in assets}
        self.imported: set[str] = set()

    def import_file(self, path: Path) -> Ok[ImportResultDto] | Err[ValidationError]:
        asset = self._by_name.get(path.name)
        if asset is None:
            return Err(ValidationError("unreadable", user_message="Cannot read this file."))
        created = asset.id not in self.imported
        self.imported.add(asset.id)
        return Ok(ImportResultDto(asset, created))

    def get(self, asset_id: str) -> Ok[MediaAssetDto] | Err[NotFoundError]:
        asset = self._by_id.get(asset_id)
        return Ok(asset) if asset else Err(NotFoundError("gone", user_message="Not in library."))


def table(overrides: dict[TaskKind, TaskResult] | None = None) -> dict[TaskKind, TaskExecutor]:
    answers = overrides or {}

    def make(kind: TaskKind) -> TaskExecutor:
        return lambda _job: answers.get(kind, TaskResult(TaskStatus.SUCCEEDED))

    return {k: make(k) for k in TaskKind}


class FakeSet:
    def __init__(self, executors: dict[TaskKind, TaskExecutor]) -> None:
        self._executors = executors

    def table(self) -> dict[TaskKind, TaskExecutor]:
        return self._executors

    def peeks(self) -> dict[TaskKind, TaskPeek]:
        return {}


def pipeline(assets: Sequence[MediaAssetDto], **answers: TaskResult) -> RunPipeline:
    by_kind = {TaskKind(k): v for k, v in answers.items()}
    return RunPipeline(FakeLibrary(assets), lambda _s: FakeSet(table(by_kind)))  # type: ignore[arg-type]


def run(p: RunPipeline, command: RunPipelineCommand) -> PipelineReport:
    result = p.execute(command, JobContext.detached())
    assert isinstance(result, Ok)
    return result.value


VIDEO = make_asset("v1", original_filename="a.mp4")
AUDIO = make_asset("s1", original_filename="a.wav", media_type=MediaType.AUDIO)
IMAGE = make_asset("i1", original_filename="a.png", media_type=MediaType.IMAGE)


def test_nothing_to_do_is_a_validation_error() -> None:
    result = pipeline([VIDEO]).execute(RunPipelineCommand(), JobContext.detached())

    assert isinstance(result, Err)
    assert isinstance(result.error, ValidationError)


def test_report_separates_new_work_reused_work_and_every_kind_of_skip() -> None:
    report = run(
        pipeline([VIDEO], audio_analysis=TaskResult(TaskStatus.REUSED, "stored transcript")),
        RunPipelineCommand(paths=[Path("a.mp4")], stages=frozenset({K.ALIGNMENT, K.INSPECT})),
    )
    tasks = {t.kind: t for t in report.assets[0].tasks}

    assert tasks[K.AUDIO_ANALYSIS].status is TaskStatus.REUSED
    assert tasks[K.AUDIO_ANALYSIS].implied  # not asked for; alignment needs it
    assert tasks[K.ALIGNMENT].status is TaskStatus.SUCCEEDED
    assert tasks[K.PROXY].status is TaskStatus.SKIPPED_DISABLED
    assert set(tasks) == set(TaskKind)  # every kind is accounted for
    assert report.counts()["reused"] == 1
    assert report.ok
    assert report.exit_code == 0


def test_failure_skips_dependents_and_gives_a_nonzero_exit() -> None:
    report = run(
        pipeline([VIDEO], video_analysis=TaskResult(TaskStatus.FAILED, "no decoder")),
        RunPipelineCommand(paths=[Path("a.mp4")], stages=frozenset({K.ALIGNMENT})),
    )
    tasks = {t.kind: t for t in report.assets[0].tasks}

    assert tasks[K.ALIGNMENT].status is TaskStatus.SKIPPED_DEPENDENCY
    assert not report.ok
    assert report.exit_code == 1


def test_stages_the_media_cannot_support_are_unavailable_not_disabled() -> None:
    report = run(
        pipeline([AUDIO]),
        RunPipelineCommand(
            asset_ids=["s1"], stages=frozenset({K.VIDEO_ANALYSIS, K.AUDIO_ANALYSIS})
        ),
    )
    tasks = {t.kind: t for t in report.assets[0].tasks}

    assert tasks[K.VIDEO_ANALYSIS].status is TaskStatus.SKIPPED_UNAVAILABLE
    assert tasks[K.AUDIO_ANALYSIS].status is TaskStatus.SUCCEEDED
    assert report.ok


def test_images_are_reported_not_processed() -> None:
    report = run(pipeline([IMAGE]), RunPipelineCommand(paths=[Path("a.png")]))

    assert report.assets[0].tasks == ()
    assert "not processed" in report.assets[0].notes[0]


def test_unreadable_input_is_a_failure_but_other_inputs_still_run() -> None:
    report = run(
        pipeline([VIDEO]),
        RunPipelineCommand(
            paths=[Path("missing.mp4"), Path("a.mp4")],
            asset_ids=["nope"],
            stages=frozenset({K.INSPECT}),
        ),
    )

    assert [f.source for f in report.failures] == ["missing.mp4", "nope"]
    assert report.assets[0].task(K.INSPECT).status is TaskStatus.SUCCEEDED  # type: ignore[union-attr]
    assert not report.ok


def test_same_content_given_twice_is_one_asset() -> None:
    report = run(
        pipeline([VIDEO]),
        RunPipelineCommand(paths=[Path("a.mp4")], asset_ids=["v1"], stages=frozenset({K.INSPECT})),
    )

    assert len(report.assets) == 1


def test_dry_run_plans_the_graph_and_executes_nothing() -> None:
    calls: list[str] = []

    def spy(job: TaskJob) -> TaskResult:
        calls.append(str(job.id))
        return TaskResult(TaskStatus.SUCCEEDED)

    library = FakeLibrary([VIDEO])
    p = RunPipeline(library, lambda _s: FakeSet(dict.fromkeys(TaskKind, spy)))  # type: ignore[arg-type]
    report = run(p, RunPipelineCommand(paths=[Path("a.mp4"), Path("notes.txt")], dry_run=True))
    alignment = report.assets[0].task(K.ALIGNMENT)

    assert calls == []
    assert library.imported == set()  # a dry run never touches the library
    assert report.assets[0].origin == "would_import"
    assert [f.source for f in report.failures] == ["notes.txt"]
    assert alignment is not None and alignment.status is None
    assert alignment.needs == (K.AUDIO_ANALYSIS, K.VIDEO_ANALYSIS)
    assert report.levels  # the graph is shown
    assert report.counts()["planned"] > 0


def test_report_serialises_to_plain_json() -> None:
    import json

    report = run(pipeline([VIDEO]), RunPipelineCommand(paths=[Path("a.mp4")]))

    data = json.loads(json.dumps(report.to_dict()))

    assert data["schema_version"] == 1
    assert data["assets"][0]["tasks"][0]["kind"]
    assert data["lane_capacity"] == {"light": 4, "cpu": 2, "gpu": 1}


def test_cancelled_run_reports_cancelled_tasks_and_is_not_ok() -> None:
    ctx = JobContext.detached()
    ctx.cancellation.cancel()

    result = pipeline([VIDEO]).execute(RunPipelineCommand(asset_ids=["v1"]), ctx)

    assert isinstance(result, Ok)
    report = result.value
    assert report.cancelled
    assert not report.ok
    assert {t.status for t in report.assets[0].tasks if t.lane} == {TaskStatus.CANCELLED}


def test_replace_keeps_unrelated_assets_independent() -> None:
    other = replace(VIDEO, id="v2", original_filename="b.mp4")
    report = run(
        pipeline([VIDEO, other], video_analysis=TaskResult(TaskStatus.SUCCEEDED)),
        RunPipelineCommand(
            paths=[Path("a.mp4"), Path("b.mp4")], stages=frozenset({K.VIDEO_ANALYSIS})
        ),
    )

    assert [a.asset_id for a in report.assets] == ["v1", "v2"]
    assert all(a.task(K.VIDEO_ANALYSIS).status is TaskStatus.SUCCEEDED for a in report.assets)  # type: ignore[union-attr]
