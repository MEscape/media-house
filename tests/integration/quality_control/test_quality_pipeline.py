"""Quality control end to end through the real composition root and real FFmpeg:

raw clip -> Layer 4 -> specification -> built timeline -> audio mix -> a PREVIEW render (made here
with a black second in it) -> QUALITY REPORT with evidence -> bound to a review -> reused when
nothing changed -> refused for a preview of another version.
"""

import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_library.application.contracts import MediaLibrary
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
    QualityCatalog,
    Severity,
    StaleStep,
    TechnicalStatus,
)
from media_house.modules.review_orchestrator.application.contracts import (
    OpenSessionCommand,
    ReviewCatalog,
    ReviewSessions,
    ReviewState,
)
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from tests.integration.media_pipeline.test_layer4_pipeline import no_speech_clip
from tests.integration.music_and_sound_design.conftest import build_library

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]
SETTINGS = StageSettings(output_format="9:16", layer4_render=False)
STAGES = LAYER4_STAGES | LAYER5_STAGES
CTX = JobContext.detached()


@dataclass
class World:
    app: Application
    anchor: str
    root: Path

    @property
    def timeline(self) -> BuiltTimeline:
        found = self.app.container.resolve(BuiltTimelineCatalog).latest(self.anchor)
        assert isinstance(found, Ok) and found.value is not None
        return found.value[0]

    def preview(self, name: str, *, black: tuple[float, float] | None, audio: bool = True) -> str:
        """A render of the timeline: its size, rate and length; optionally a black stretch."""
        t = self.timeline
        rate = t.settings.frame_rate
        seconds = t.settings.duration_frames * rate.denominator / rate.numerator
        path = self.root / f"{name}.mp4"
        size = f"{t.settings.width}x{t.settings.height}"
        video = f"testsrc2=size={size}:rate={rate.numerator}/{rate.denominator}:duration={seconds}"
        args = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", video]
        if audio:
            args += [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={seconds}:sample_rate=48000",
            ]
        if black:
            args += [
                "-vf",
                f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{black[0]},{black[1]})'",
            ]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            args += ["-c:a", "aac", "-shortest"]
        subprocess.run([*args, str(path)], check=True)  # noqa: S603
        imported = self.app.container.resolve(MediaLibrary).import_file(path)
        assert isinstance(imported, Ok)
        return imported.value.asset.id

    def analyze(self, **kw: object):  # type: ignore[no-untyped-def]
        command = AnalyzeQualityCommand(anchor_asset_id=self.anchor, **kw)  # type: ignore[arg-type]
        return self.app.container.resolve(QualityAnalyzer).execute(command, CTX)


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
        clip = no_speech_clip(tmp_path / "meme.mp4")
        ran = app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(paths=[clip], stages=STAGES, settings=SETTINGS), CTX
        )
        assert isinstance(ran, Ok)
        yield World(app, ran.value.assets[0].asset_id, tmp_path)
    finally:
        app.shutdown()


def test_a_timeline_alone_is_checked_and_the_render_checks_say_they_did_not_run(
    world: World,
) -> None:
    result = world.analyze()

    assert isinstance(result, Ok), result
    report = result.value.report
    assert report.timeline_content_hash == world.timeline.build.build_hash
    assert report.approval == "not_approved" and report.preview_asset_id == ""
    skipped = {c.name: c.state.value for c in report.analysis_scope}
    assert skipped["timeline structure"] == "ran"
    assert skipped["pictures"] == "not_run" and skipped["black frames"] == "not_run"
    assert report.visual_quality_indicators.state.value in {"ok", "warning", "problem"}
    assert not [i for i in report.detected_issues if i.category.value == "technical_video"]


def test_black_frames_in_the_preview_are_found_with_a_picture_as_evidence(world: World) -> None:
    preview = world.preview("preview-black", black=(1.0, 2.0))

    result = world.analyze(
        preview_asset_id=preview, preview_timeline_hash=world.timeline.build.build_hash
    )

    assert isinstance(result, Ok), result
    report = result.value.report
    black = [i for i in report.detected_issues if i.code == "video.black_frames"]
    assert len(black) == 1
    found = black[0]
    assert found.severity is Severity.CRITICAL and found.blocks_export
    rate = world.timeline.settings.frame_rate
    assert abs((found.start_frame or 0) - round(rate.numerator / rate.denominator)) <= 3
    assert report.technical_status is TechnicalStatus.BLOCKED
    assert found.issue_id in report.export_blocking_issues
    # the evidence is a picture of THIS version, kept in the library
    assert found.evidence_assets
    library = world.app.container.resolve(MediaLibrary)
    derived = {a.id: a for a in library.list_derived(preview)}
    asset = derived[found.evidence_assets[0]]
    assert asset.metadata["timeline_version"] == world.timeline.build.build_version
    assert report.timeline_content_hash == world.timeline.build.build_hash


def test_a_preview_without_sound_is_critical_and_one_that_matches_is_clean(world: World) -> None:
    silent = world.analyze(
        preview_asset_id=world.preview("preview-silent", black=None, audio=False)
    )
    clean = world.analyze(preview_asset_id=world.preview("preview-good", black=None))

    assert isinstance(silent, Ok) and isinstance(clean, Ok)
    assert "audio.missing" in {i.code for i in silent.value.report.detected_issues}
    problems = {
        i.code
        for i in clean.value.report.detected_issues
        if i.category.value in {"technical_video"}
    }
    assert not problems, problems


def test_a_preview_of_another_version_of_the_timeline_is_refused(world: World) -> None:
    preview = world.preview("preview-other", black=None)

    result = world.analyze(preview_asset_id=preview, preview_timeline_hash="not-this-version")

    assert isinstance(result, Err) and isinstance(result.error, StaleStep)


def test_an_identical_analysis_is_not_done_again_and_a_report_is_found_in_the_catalog(
    world: World,
) -> None:
    preview = world.preview("preview-again", black=(1.0, 2.0))
    first = world.analyze(preview_asset_id=preview)
    second = world.analyze(preview_asset_id=preview)

    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert first.value.created and not second.value.created
    assert second.value.report.quality_report_id == first.value.report.quality_report_id
    catalog = world.app.container.resolve(QualityCatalog)
    latest = catalog.latest(world.anchor, timeline_id=world.timeline.timeline_id)
    assert isinstance(latest, Ok) and latest.value is not None
    assert latest.value[0].content_hash == first.value.report.content_hash


def test_the_report_goes_to_the_review_and_nothing_else_changes(world: World) -> None:
    sessions = world.app.container.resolve(ReviewSessions)
    opened = sessions.open(OpenSessionCommand(world.anchor, "Ada"))
    assert isinstance(opened, Ok)
    sid = opened.value.session.session_id
    before = world.timeline.build.build_hash

    result = world.analyze(review_session_id=sid)

    assert isinstance(result, Ok), result
    session = world.app.container.resolve(ReviewCatalog).get(sid)
    assert isinstance(session, Ok)
    assert session.value.quality_report_id == result.value.report.quality_report_id
    assert session.value.state is ReviewState.REVIEW_READY  # ready for a person, not approved
    assert world.timeline.build.build_hash == before  # quality control changes no timeline
