"""The real editing stage consumes the stored B-roll analysis: footage of other recordings.

Real: composition root, library, content analyzer, story planner, B-roll analyzer (toy encoders),
editing planner. Synthetic: the speech timeline and what each clip shows. This proves the plumbing
(the analysis is found, its candidates are loaded as cover evidence, a cover clip of ANOTHER
recording is placed and the plan names that recording as a source); it says nothing about edit
quality on real footage.
"""

import shutil
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.audio_intelligence.application.contracts import timeline_to_json
from media_house.modules.audio_intelligence.domain.analysis.config import ANALYSIS_OPERATION
from media_house.modules.broll_intelligence.application.contracts import (
    AnalyzeBrollCommand,
    BrollCatalog,
    BrollConfig,
)
from media_house.modules.editing_intelligence.application.contracts import (
    EditPlanner,
    PlanEditCommand,
    Severity,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaLibrary,
)
from media_house.modules.story_architecture.application.contracts import (
    PlanStoryCommand,
    StoryConfig,
    StoryPlanner,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.support.analysis_fakes import timeline
from tests.support.broll_library import LINES, analyzer, import_talk, stock
from tests.support.ci_texts import lines_to_transcript

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recordings",
    ),
]


def store_audio(app: Application, asset_id: str) -> None:
    transcript = lines_to_transcript(LINES, seconds_per_word=0.3, gap=0.4)
    groups = [[(w.text, w.start, w.end) for w in segment.words] for segment in transcript.segments]
    library = app.container.resolve(MediaLibrary)
    source = library.get(asset_id)
    assert isinstance(source, Ok), source
    stored = DerivedDocuments(library, app.container.resolve(AppPaths)).store(
        source.value,
        timeline_to_json(timeline(groups, duration=20.0)),
        filename="timeline.json",
        spec=DocumentSpec(ANALYSIS_OPERATION, 1, {"case": "broll-editing"}),
        display_name="timeline",
        metadata={},
    )
    assert isinstance(stored, Ok), stored


def prepare(app: Application, tmp_path: Path) -> tuple[str, dict[str, str]]:
    talk = import_talk(app, tmp_path)
    store_audio(app, talk)
    clips = stock(app, tmp_path)
    planned = app.container.resolve(StoryPlanner).execute(
        PlanStoryCommand((talk,), StoryConfig("review")), JobContext.detached()
    )
    assert isinstance(planned, Ok), planned
    return talk, clips


def test_the_edit_plan_covers_a_passage_with_footage_of_another_recording(
    app: Application, tmp_path: Path
) -> None:
    talk, clips = prepare(app, tmp_path)
    broll = analyzer(app).execute(AnalyzeBrollCommand((talk,)), JobContext.detached())
    assert isinstance(broll, Ok), broll
    offered = {c.asset_id for r in broll.value.analysis.recommendations for c in r.candidates}
    assert clips["pot"] in offered or clips["harbor"] in offered

    planned = app.container.resolve(EditPlanner).execute(
        PlanEditCommand((talk,)), JobContext.detached()
    )

    assert isinstance(planned, Ok), planned
    plan = planned.value.plan
    assert ("broll_analysis", "reused") in {(i.kind, i.how.value) for i in plan.inputs}
    assert next(s for s in plan.stages if s.stage == "broll").state.value == "ok"
    assert [i for i in plan.issues if i.severity is Severity.ERROR] == []
    chosen = [r.chosen for r in plan.coverage if r.chosen is not None]
    covers = [c for c in chosen if c.asset_id != talk]
    assert covers, plan.coverage
    assert {c.asset_id for c in covers} <= {clips["pot"], clips["harbor"]}
    assert {s.asset_id for s in plan.sources} >= {talk, *(c.asset_id for c in covers)}


def test_without_a_stored_broll_analysis_the_editor_says_so_and_still_plans(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)

    planned = app.container.resolve(EditPlanner).execute(
        PlanEditCommand((talk,)), JobContext.detached()
    )

    assert isinstance(planned, Ok), planned
    assert next(s for s in planned.value.plan.stages if s.stage == "broll").state.value == (
        "not_available"
    )
    assert app.container.resolve(BrollCatalog).history(talk) == []


def test_a_new_broll_analysis_makes_a_new_edit_plan_version(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)
    planner = app.container.resolve(EditPlanner)
    ctx = JobContext.detached()
    before = planner.execute(PlanEditCommand((talk,)), ctx)
    analysed = analyzer(app).execute(AnalyzeBrollCommand((talk,), BrollConfig("tutorial")), ctx)
    assert isinstance(before, Ok) and isinstance(analysed, Ok)

    after = planner.execute(PlanEditCommand((talk,)), ctx)
    again = planner.execute(PlanEditCommand((talk,)), ctx)

    assert isinstance(after, Ok) and isinstance(again, Ok)
    assert after.value.created and after.value.plan.plan_version == 2
    assert after.value.plan.supersedes == before.value.asset.id
    assert not again.value.created  # the identical inputs hit the cache
