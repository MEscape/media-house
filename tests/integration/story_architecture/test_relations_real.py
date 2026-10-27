"""A story over several real recordings: relations come from the real detectors and the real
lineage, and a person's decision changes how far the plan relies on them."""

import shutil
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.asset_relationships.application.contracts import (
    DETECTOR_IDS,
    DetectCommand,
    RelationshipCatalog,
    RelationshipDetection,
    RelationshipEditor,
    RelationshipQuery,
    RelationshipType,
)
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalyzer,
    ContentConfig,
    Profile,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.story_architecture.application.contracts import (
    Certainty,
    PlanStoryCommand,
    Priority,
    StoryPlanner,
    Treatment,
    validate_plan,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from tests.support.ci_texts import BATTERIES, GARDEN, lines_to_transcript
from tests.support.media_factories import ffmpeg, make_wav

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required",
    ),
]
CTX = JobContext.detached()


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield application
    application.shutdown()


def analyse(app: Application, asset_id: str, lines: tuple[str, ...]) -> None:
    done = app.container.resolve(ContentAnalyzer).execute(
        AnalyzeContentCommand(
            source_asset_id=asset_id,
            transcript=lines_to_transcript(lines),
            config=ContentConfig(profile=Profile.FAST),
        ),
        CTX,
    )
    assert isinstance(done, Ok), done


def tagged_clip(path: Path, take: str, start: str) -> Path:
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=6:size=160x120:rate=25",
        "-metadata", f"creation_time={start}", "-metadata", "scene=12A",
        "-metadata", f"take={take}", "-metadata", "device=Cam One",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip
    return path


def test_detected_takes_are_noted_until_a_person_confirms_them(
    app: Application, tmp_path: Path
) -> None:
    library = app.container.resolve(MediaLibrary)
    ids = []
    for take, start in (("1", "2026-03-04T05:00:00Z"), ("2", "2026-03-04T09:00:00Z")):
        imported = library.import_file(tagged_clip(tmp_path / f"take{take}.mkv", take, start))
        assert isinstance(imported, Ok), imported
        ids.append(imported.value.asset.id)
        inspected = app.container.resolve(MediaInspector).execute(InspectMediaCommand(ids[-1]), CTX)
        assert isinstance(inspected, Ok), inspected
    analyse(app, ids[0], BATTERIES)
    analyse(app, ids[1], GARDEN)
    detected = app.container.resolve(RelationshipDetection).detect(
        DetectCommand(frozenset(DETECTOR_IDS)), CTX
    )
    assert isinstance(detected, Ok) and detected.value.proposed == 1
    planner = app.container.resolve(StoryPlanner)
    command = PlanStoryCommand(tuple(ids))

    before = planner.execute(command, CTX)

    assert isinstance(before, Ok)
    [relation] = before.value.plan.asset_relations
    assert relation.relation == RelationshipType.ALTERNATE_TAKE_OF.value
    assert relation.certainty is Certainty.EVIDENCE_SUPPORTED  # detected, so only a possibility
    assert relation.treatment is Treatment.NOTED
    assert "production_tag_match" in relation.evidence[0]

    page = app.container.resolve(RelationshipCatalog).relationships_of(
        ids[0], RelationshipQuery(include_projected=False)
    )
    assert isinstance(page, Ok)
    confirmed = app.container.resolve(RelationshipEditor).confirm(
        page.value.items[0].relationship_id
    )
    assert isinstance(confirmed, Ok)
    after = planner.execute(command, CTX)

    assert isinstance(after, Ok) and after.value.created  # the decision changed the cache key
    assert after.value.plan.plan_version == 2
    assert after.value.plan.supersedes == before.value.asset.id  # the first plan is still there
    [now] = after.value.plan.asset_relations
    assert now.certainty is Certainty.CONFIRMED and now.treatment is Treatment.ALTERNATE_TAKES
    assert validate_plan(after.value.plan) == []


def test_a_derived_copy_in_the_story_is_a_recorded_redundancy(
    app: Application, tmp_path: Path
) -> None:
    library = app.container.resolve(MediaLibrary)
    original = library.import_file(make_wav(tmp_path / "talk.wav", seconds=2.0))
    assert isinstance(original, Ok)
    source = original.value.asset.id
    other = tmp_path / "improved.wav"
    make_wav(other, seconds=2.0, rate=22_050)
    derived = library.register_derived(
        source, other, operation="audio_improvement", config={"profile": "test"}
    )
    assert isinstance(derived, Ok)
    copy_id = derived.value.asset.id
    analyse(app, source, BATTERIES)
    analyse(app, copy_id, BATTERIES)

    planned = app.container.resolve(StoryPlanner).execute(PlanStoryCommand((source, copy_id)), CTX)

    assert isinstance(planned, Ok)
    plan = planned.value.plan
    [relation] = plan.asset_relations
    assert relation.certainty is Certainty.RECORDED  # the library says so; nobody inferred it
    assert relation.treatment is Treatment.SAME_RECORDING
    redundant = {
        s.sources[0].asset_id for s in plan.sections if s.priority is Priority.REDUNDANT_CANDIDATE
    }
    assert redundant == {copy_id}
    assert validate_plan(plan) == []
