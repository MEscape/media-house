"""Story Architecture against the real composition root and the real Media Library.

Only the audio and the transcript are synthetic: a WAV is imported so a real asset exists, and the
content analysis is produced by the real Content Intelligence from a supplied transcript (stored in
the library like any other). Everything after that is the production path.
"""

import json
import shutil
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalyzer,
    ContentConfig,
    Profile,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import MediaPipeline
from media_house.modules.story_architecture.application.contracts import (
    MissingAnalysis,
    PlanStoryCommand,
    StoryCatalog,
    StoryConfig,
    StoryPlanner,
    validate_plan,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from tests.support.ci_texts import THREE_TOPICS, lines_to_transcript
from tests.support.media_factories import make_wav

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recording",
    ),
]


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield application
    application.shutdown()


def import_recording(app: Application, tmp_path: Path) -> str:
    library = app.container.resolve(MediaLibrary)
    media = tmp_path / "media"
    media.mkdir()
    imported = library.import_file(make_wav(media / "talk.wav"))
    assert isinstance(imported, Ok), imported
    return imported.value.asset.id


def analyse(app: Application, asset_id: str) -> None:
    result = app.container.resolve(ContentAnalyzer).execute(
        AnalyzeContentCommand(
            source_asset_id=asset_id,
            transcript=lines_to_transcript(THREE_TOPICS),
            config=ContentConfig(profile=Profile.FAST),
        ),
        JobContext.detached(),
    )
    assert isinstance(result, Ok), result


def test_the_planner_is_wired_and_plans_from_the_stored_analysis(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path)
    planner = app.container.resolve(StoryPlanner)
    ctx = JobContext.detached()

    missing = planner.execute(PlanStoryCommand((asset_id,)), ctx)
    assert isinstance(missing, Err) and isinstance(missing.error, MissingAnalysis)

    analyse(app, asset_id)
    first = planner.execute(PlanStoryCommand((asset_id,), StoryConfig("review")), ctx)
    again = planner.execute(PlanStoryCommand((asset_id,), StoryConfig("review")), ctx)

    assert isinstance(first, Ok) and isinstance(again, Ok)
    assert first.value.created and not again.value.created
    assert again.value.plan == first.value.plan
    assert validate_plan(first.value.plan) == []
    assert next(r.how for r in first.value.plan.inputs) == "reused"


def test_the_plan_is_a_derived_library_document_that_can_be_read_back(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path)
    analyse(app, asset_id)
    planner = app.container.resolve(StoryPlanner)
    result = planner.execute(PlanStoryCommand((asset_id,)), JobContext.detached())
    assert isinstance(result, Ok)

    library = app.container.resolve(MediaLibrary)
    catalog = app.container.resolve(StoryCatalog)
    path = library.local_path(result.value.asset.id)
    assert isinstance(path, Ok)
    document = json.loads(path.value.read_text(encoding="utf-8"))
    latest = catalog.latest(asset_id)

    assert document["document_type"] == "story_plan"  # the library accepts documents that say so
    assert result.value.asset.derivation is not None
    assert result.value.asset.derivation.source_asset_id == asset_id  # the source is untouched
    assert isinstance(latest, Ok) and latest.value is not None
    assert latest.value[0] == result.value.plan
    assert [a.id for a in catalog.history(asset_id)] == [result.value.asset.id]


def test_a_changed_configuration_adds_a_version_and_keeps_the_first(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path)
    analyse(app, asset_id)
    planner = app.container.resolve(StoryPlanner)
    ctx = JobContext.detached()

    first = planner.execute(PlanStoryCommand((asset_id,), StoryConfig("tutorial")), ctx)
    second = planner.execute(
        PlanStoryCommand((asset_id,), StoryConfig("tutorial", audience="beginners")), ctx
    )

    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert (second.value.plan.plan_version, second.value.plan.supersedes) == (
        2,
        first.value.asset.id,
    )
    assert len(app.container.resolve(StoryCatalog).history(asset_id)) == 2


def test_the_pipeline_is_wired_to_the_planner_and_plans_the_story_after_the_content(
    app: Application,
) -> None:
    from media_house.modules.media_pipeline.application.contracts import (
        DEFAULT_STAGES,
        RunPipelineCommand,
        TaskKind,
    )

    pipeline = app.container.resolve(MediaPipeline)
    report = pipeline.execute(
        RunPipelineCommand(paths=(Path("talk.wav"),), dry_run=True), JobContext.detached()
    )

    assert TaskKind.STORY in DEFAULT_STAGES
    assert isinstance(report, Ok)
    [asset] = report.value.assets
    story = next(t for t in asset.tasks if t.kind is TaskKind.STORY)
    assert TaskKind.CONTENT_ANALYSIS in story.needs
    assert not story.implied  # it was asked for, not pulled in
