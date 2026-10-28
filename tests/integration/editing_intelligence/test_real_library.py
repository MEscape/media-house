"""Editing Intelligence against the real composition root and the real Media Library.

Only the audio and the transcript are synthetic: a WAV is imported so a real asset exists, the
content analysis is produced by the real Content Intelligence from a supplied transcript, and the
story plan by the real Story Architecture. Everything after that is the production path: stored
analyses are read through their catalogs and the edit plan is stored as a derived library document.
No speech model or footage is involved, so nothing here says how good the edit is on real material.
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
from media_house.modules.editing_intelligence.application.contracts import (
    EditCatalog,
    EditConfig,
    EditPlanner,
    InvalidEditPlan,
    MissingStoryPlan,
    PlanEditCommand,
    Severity,
    TrackKind,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import (
    MediaPipeline,
    RunPipelineCommand,
    TaskKind,
)
from media_house.modules.story_architecture.application.contracts import (
    PlanStoryCommand,
    StoryConfig,
    StoryPlanner,
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


def import_recording(app: Application, tmp_path: Path, seconds: float) -> str:
    library = app.container.resolve(MediaLibrary)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    imported = library.import_file(make_wav(media / "talk.wav", seconds=seconds))
    assert isinstance(imported, Ok), imported
    return imported.value.asset.id


def prepare(app: Application, asset_id: str, *, seconds_per_word: float) -> None:
    """Content analysis from a supplied transcript, then the story plan, both stored."""
    ctx = JobContext.detached()
    analysed = app.container.resolve(ContentAnalyzer).execute(
        AnalyzeContentCommand(
            source_asset_id=asset_id,
            transcript=lines_to_transcript(
                THREE_TOPICS, seconds_per_word=seconds_per_word, gap=0.1
            ),
            config=ContentConfig(profile=Profile.FAST),
        ),
        ctx,
    )
    assert isinstance(analysed, Ok), analysed
    planned = app.container.resolve(StoryPlanner).execute(
        PlanStoryCommand((asset_id,), StoryConfig("review")), ctx
    )
    assert isinstance(planned, Ok), planned


def test_the_planner_is_wired_and_needs_a_story_first(app: Application, tmp_path: Path) -> None:
    asset_id = import_recording(app, tmp_path, 10.0)
    planner = app.container.resolve(EditPlanner)

    result = planner.execute(PlanEditCommand((asset_id,)), JobContext.detached())

    assert isinstance(result, Err) and isinstance(result.error, MissingStoryPlan)


def test_a_stored_story_becomes_a_stored_edit_plan_that_reads_back_and_is_reused(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 60.0)
    prepare(app, asset_id, seconds_per_word=0.1)
    library = app.container.resolve(MediaLibrary)
    before = library.get(asset_id)
    planner = app.container.resolve(EditPlanner)
    ctx = JobContext.detached()

    first = planner.execute(PlanEditCommand((asset_id,), EditConfig(profile="review")), ctx)
    again = planner.execute(PlanEditCommand((asset_id,), EditConfig(profile="review")), ctx)

    assert isinstance(first, Ok) and isinstance(again, Ok), first
    assert first.value.created and not again.value.created
    assert again.value.plan == first.value.plan
    plan = first.value.plan
    assert plan.track(TrackKind.AUDIO) and not plan.issues_of(Severity.ERROR)
    path = library.local_path(first.value.asset.id)
    assert isinstance(path, Ok)
    assert json.loads(path.value.read_text(encoding="utf-8"))["document_type"] == "edit_plan"
    assert first.value.asset.derivation is not None
    assert first.value.asset.derivation.source_asset_id == asset_id
    assert library.get(asset_id) == before  # the recording itself is untouched
    catalog = app.container.resolve(EditCatalog)
    latest = catalog.latest(asset_id, profile="review")
    assert isinstance(latest, Ok) and latest.value is not None and latest.value[0] == plan
    assert [a.id for a in catalog.history(asset_id)] == [first.value.asset.id]
    clip = plan.track(TrackKind.AUDIO)[0]
    assert clip.source.asset_id == asset_id and clip.source.end.value <= 60.1


def test_a_range_beyond_the_real_recording_is_refused_and_nothing_is_stored(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 2.0)  # the transcript below runs far longer
    prepare(app, asset_id, seconds_per_word=0.4)
    planner = app.container.resolve(EditPlanner)

    result = planner.execute(PlanEditCommand((asset_id,)), JobContext.detached())

    assert isinstance(result, Err) and isinstance(result.error, InvalidEditPlan)
    assert "after the recording ends" in str(result.error)
    assert app.container.resolve(EditCatalog).history(asset_id) == []


def test_the_pipeline_is_wired_to_the_planner_and_plans_the_edit_after_the_story(
    app: Application,
) -> None:
    pipeline = app.container.resolve(MediaPipeline)

    report = pipeline.execute(
        RunPipelineCommand(
            paths=(Path("talk.wav"),), stages=frozenset({TaskKind.EDIT_PLAN}), dry_run=True
        ),
        JobContext.detached(),
    )

    assert isinstance(report, Ok)
    [asset] = report.value.assets
    edit = next(t for t in asset.tasks if t.kind is TaskKind.EDIT_PLAN)
    assert TaskKind.STORY in edit.needs and not edit.implied
    assert next(t for t in asset.tasks if t.kind is TaskKind.STORY).implied
