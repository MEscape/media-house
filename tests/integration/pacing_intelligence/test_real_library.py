"""Pacing Intelligence against the real composition root and the real Media Library.

Only the speech is synthetic: a WAV is imported so a real asset exists, the content analysis is
produced by the real Content Intelligence from a supplied transcript, the story plan by the real
Story Architecture, and a scripted audio timeline (no speech model) is stored the way the audio
analyzer stores its own. Everything after that is the production path: stored analyses are read
through their catalogs and the pacing analysis is stored as a derived library document that the
real editing stage then consumes. No footage or model is involved, so nothing here says how good
the recommendations are on real material.
"""

import json
import shutil
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_intelligence.application.contracts import timeline_to_json
from media_house.modules.audio_intelligence.domain.analysis.config import ANALYSIS_OPERATION
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalyzer,
    ContentConfig,
    Profile,
)
from media_house.modules.editing_intelligence.application.contracts import (
    EditPlanner,
    PlanEditCommand,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaLibrary,
)
from media_house.modules.media_pipeline.application.contracts import (
    MediaPipeline,
    RunPipelineCommand,
    TaskKind,
)
from media_house.modules.pacing_intelligence.application.contracts import (
    DOCUMENT_TYPE,
    AnalyzePacingCommand,
    MissingEvidence,
    PacingAnalyzer,
    PacingCatalog,
    PacingConfig,
    Severity,
)
from media_house.modules.story_architecture.application.contracts import (
    PlanStoryCommand,
    StoryConfig,
    StoryPlanner,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from media_house.shared.filesystem import AppPaths
from tests.support.analysis_fakes import timeline
from tests.support.ci_texts import THREE_TOPICS, lines_to_transcript
from tests.support.media_factories import make_wav

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recording",
    ),
]

SECONDS_PER_WORD = 0.1


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


def store_audio(app: Application, asset_id: str, seconds: float) -> None:
    """The scripted audio timeline, stored where the audio catalog looks for it."""
    transcript = lines_to_transcript(THREE_TOPICS, seconds_per_word=SECONDS_PER_WORD, gap=0.1)
    groups = [[(w.text, w.start, w.end) for w in segment.words] for segment in transcript.segments]
    library = app.container.resolve(MediaLibrary)
    source = library.get(asset_id)
    assert isinstance(source, Ok), source
    stored = DerivedDocuments(library, app.container.resolve(AppPaths)).store(
        source.value,
        timeline_to_json(timeline(groups, duration=seconds)),
        filename="timeline.json",
        spec=DocumentSpec(ANALYSIS_OPERATION, 1, {"case": "pacing-integration"}),
        display_name="timeline",
        metadata={},
    )
    assert isinstance(stored, Ok), stored


def prepare(app: Application, asset_id: str, seconds: float) -> None:
    """Content analysis from a supplied transcript, the story plan and the audio, all stored."""
    ctx = JobContext.detached()
    analysed = app.container.resolve(ContentAnalyzer).execute(
        AnalyzeContentCommand(
            source_asset_id=asset_id,
            transcript=lines_to_transcript(
                THREE_TOPICS, seconds_per_word=SECONDS_PER_WORD, gap=0.1
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
    store_audio(app, asset_id, seconds)


def test_the_analyzer_is_wired_and_refuses_a_recording_with_nothing_to_analyse(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 10.0)

    result = app.container.resolve(PacingAnalyzer).execute(
        AnalyzePacingCommand((asset_id,)), JobContext.detached()
    )

    assert isinstance(result, Err) and isinstance(result.error, MissingEvidence)
    assert app.container.resolve(PacingCatalog).history(asset_id) == []


def test_stored_results_become_a_stored_pacing_analysis_that_reads_back_and_is_reused(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 60.0)
    prepare(app, asset_id, 60.0)
    library = app.container.resolve(MediaLibrary)
    before = library.get(asset_id)
    analyzer = app.container.resolve(PacingAnalyzer)
    ctx = JobContext.detached()
    command = AnalyzePacingCommand((asset_id,), PacingConfig(profile="tutorial"))

    first = analyzer.execute(command, ctx)
    again = analyzer.execute(command, ctx)

    assert isinstance(first, Ok) and isinstance(again, Ok), first
    assert first.value.created and not again.value.created
    assert again.value.analysis == first.value.analysis
    analysis = first.value.analysis
    assert analysis.passages and analysis.sections and not analysis.issues_of(Severity.ERROR)
    assert analysis.story is not None and analysis.profile == "tutorial"
    path = library.local_path(first.value.asset.id)
    assert isinstance(path, Ok)
    assert json.loads(path.value.read_text(encoding="utf-8"))["document_type"] == DOCUMENT_TYPE
    assert first.value.asset.derivation is not None
    assert first.value.asset.derivation.source_asset_id == asset_id
    assert library.get(asset_id) == before  # the recording itself is untouched
    catalog = app.container.resolve(PacingCatalog)
    latest = catalog.latest(asset_id, profile="tutorial")
    assert isinstance(latest, Ok) and latest.value is not None and latest.value[0] == analysis
    assert [a.id for a in catalog.history(asset_id)] == [first.value.asset.id]


def test_a_different_profile_is_a_new_stored_analysis_and_the_old_one_stays(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 60.0)
    prepare(app, asset_id, 60.0)
    analyzer = app.container.resolve(PacingAnalyzer)
    ctx = JobContext.detached()

    one = analyzer.execute(AnalyzePacingCommand((asset_id,), PacingConfig(profile="tutorial")), ctx)
    two = analyzer.execute(
        AnalyzePacingCommand((asset_id,), PacingConfig(profile="documentary")), ctx
    )

    assert isinstance(one, Ok) and isinstance(two, Ok)
    assert one.value.asset.id != two.value.asset.id
    assert len(app.container.resolve(PacingCatalog).history(asset_id)) == 2


def test_the_real_editing_stage_reads_the_stored_pacing_analysis(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 60.0)
    prepare(app, asset_id, 60.0)
    ctx = JobContext.detached()
    analysed = app.container.resolve(PacingAnalyzer).execute(
        AnalyzePacingCommand((asset_id,), PacingConfig(profile="tutorial")), ctx
    )
    assert isinstance(analysed, Ok), analysed

    planned = app.container.resolve(EditPlanner).execute(PlanEditCommand((asset_id,)), ctx)

    assert isinstance(planned, Ok), planned
    kinds = {(i.kind, i.how.value) for i in planned.value.plan.inputs}
    assert ("pacing_analysis", "reused") in kinds
    stage = next(s for s in planned.value.plan.stages if s.stage == "pacing")
    assert stage.state.value == "ok"


def test_without_a_stored_pacing_analysis_the_editing_stage_uses_its_baseline(
    app: Application, tmp_path: Path
) -> None:
    asset_id = import_recording(app, tmp_path, 60.0)
    prepare(app, asset_id, 60.0)

    planned = app.container.resolve(EditPlanner).execute(
        PlanEditCommand((asset_id,)), JobContext.detached()
    )

    assert isinstance(planned, Ok), planned
    stage = next(s for s in planned.value.plan.stages if s.stage == "pacing")
    assert stage.state.value == "not_available"


def test_the_pipeline_runs_pacing_by_default_and_the_edit_plan_waits_for_it(
    app: Application,
) -> None:
    pipeline = app.container.resolve(MediaPipeline)
    ctx = JobContext.detached()

    default = pipeline.execute(RunPipelineCommand(paths=(Path("talk.wav"),), dry_run=True), ctx)
    edit = pipeline.execute(
        RunPipelineCommand(
            paths=(Path("talk.wav"),),
            stages=frozenset({TaskKind.EDIT_PLAN, TaskKind.PACING}),
            dry_run=True,
        ),
        ctx,
    )

    assert isinstance(default, Ok) and isinstance(edit, Ok)
    assert TaskKind.PACING in {t.kind for t in default.value.assets[0].tasks}
    plan = next(t for t in edit.value.assets[0].tasks if t.kind is TaskKind.EDIT_PLAN)
    assert TaskKind.PACING in plan.wants
