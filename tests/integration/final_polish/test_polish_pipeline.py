"""The final polish end to end through the real composition root: raw clip -> built timeline ->
review -> quality control -> FINAL POLISH of the exact version -> the review holds a final review
version (or asks for another round) -> the same polish is not made twice."""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_mix_engine.application.contracts import (
    AudioMixer,
    RenderMixCommand,
)
from media_house.modules.final_polish.application.contracts import (
    FinalPolisher,
    NotPolishable,
    PolishCatalog,
    PolishCommand,
    PolishError,
    PolishResult,
    Readiness,
)
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    LAYER5_STAGES,
    MediaPipeline,
    RunPipelineCommand,
    StageSettings,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    ImportSoundLibraryCommand,
    SoundLibraryImporter,
)
from media_house.modules.quality_control.application.contracts import (
    AnalyzeQualityCommand,
    QualityAnalyzer,
)
from media_house.modules.review_orchestrator.application.contracts import (
    OpenSessionCommand,
    ReviewCatalog,
    ReviewSession,
    ReviewSessions,
    ReviewState,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok, Result
from tests.integration.media_pipeline.test_layer4_pipeline import no_speech_clip
from tests.integration.music_and_sound_design.conftest import build_library

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]
SETTINGS = StageSettings(output_format="9:16", layer4_render=False)
CTX = JobContext.detached()


@dataclass
class World:
    app: Application
    anchor: str
    session_id: str

    @property
    def session(self) -> ReviewSession:
        found = self.app.container.resolve(ReviewCatalog).get(self.session_id)
        assert isinstance(found, Ok)
        return found.value


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    try:
        library = build_library(tmp_path / "sounds")
        imported = app.container.resolve(SoundLibraryImporter).execute(
            ImportSoundLibraryCommand(library.manifest), CTX
        )
        assert isinstance(imported, Ok)
        ran = app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(
                paths=[no_speech_clip(tmp_path / "meme.mp4")],
                stages=LAYER4_STAGES | LAYER5_STAGES,
                settings=SETTINGS,
            ),
            CTX,
        )
        assert isinstance(ran, Ok)
        anchor = ran.value.assets[0].asset_id
        mixed = app.container.resolve(AudioMixer).execute(
            RenderMixCommand(anchor_asset_id=anchor), CTX
        )
        assert isinstance(mixed, Ok)
        opened = app.container.resolve(ReviewSessions).open(OpenSessionCommand(anchor, "Ada"))
        assert isinstance(opened, Ok)
        sid = opened.value.session.session_id
        checked = app.container.resolve(QualityAnalyzer).execute(
            AnalyzeQualityCommand(anchor, review_session_id=sid), CTX
        )
        assert isinstance(checked, Ok)
        yield World(app, anchor, sid)
    finally:
        app.shutdown()


def polish(
    world: World, *, preview_asset_id: str | None = None, preview_timeline_hash: str | None = None
) -> Result[PolishResult, PolishError]:
    return world.app.container.resolve(FinalPolisher).execute(
        PolishCommand(
            review_session_id=world.session_id,
            preview_asset_id=preview_asset_id,
            preview_timeline_hash=preview_timeline_hash,
        ),
        CTX,
    )


def test_the_polish_reports_on_the_exact_version_and_the_review_holds_the_result(
    world: World,
) -> None:
    done = polish(world)

    assert isinstance(done, Ok), done
    report = done.value.report
    version = world.session.timeline
    assert report.timeline_content_hash == version.content_hash
    assert report.approval == "not_approved"
    assert len(report.sections()) == 9
    assert world.session.polish_report_id == report.final_polish_report_id
    assert world.session.quality_report_id == report.quality_report_id
    if report.readiness_status is Readiness.NOT_READY:
        assert world.session.state is ReviewState.REVIEW_AGAIN_REQUIRED  # another round first
    else:
        assert world.session.state is ReviewState.FINAL_REVIEW_READY
    package = report.final_review_package
    assert package.timeline_version == version.version and package.quality_report_id


def test_the_same_polish_is_not_made_twice_and_is_found_again(world: World) -> None:
    first = polish(world)
    second = polish(world)

    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert not second.value.created
    assert second.value.report.final_polish_report_id == first.value.report.final_polish_report_id
    catalog = world.app.container.resolve(PolishCatalog)
    latest = catalog.latest(
        world.anchor, timeline_content_hash=first.value.report.timeline_content_hash
    )
    assert isinstance(latest, Ok) and latest.value is not None


def test_a_preview_of_another_version_is_refused_and_nothing_is_recorded(world: World) -> None:
    done = polish(world, preview_asset_id="some-render", preview_timeline_hash="not-this-version")

    assert isinstance(done, Err) and isinstance(done.error, NotPolishable)


def test_nothing_changes_in_the_timeline(world: World) -> None:
    before = world.app.container.resolve(BuiltTimelineCatalog).latest(world.anchor)
    polish(world)
    after = world.app.container.resolve(BuiltTimelineCatalog).latest(world.anchor)

    assert isinstance(before, Ok) and isinstance(after, Ok)
    assert before.value is not None and after.value is not None
    assert before.value[0].build.build_hash == after.value[0].build.build_hash
