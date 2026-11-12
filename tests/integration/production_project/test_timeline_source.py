"""The timeline QC reads a REAL built timeline: raw clip -> pipeline -> stored build -> facts ->
the project -> the gate. No timeline is faked."""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    LAYER5_STAGES,
    MediaPipeline,
    RunPipelineCommand,
    StageSettings,
)
from media_house.modules.production_project.application.contracts import ProductionWorkflow
from media_house.modules.production_project.application.ports import TimelineSource
from media_house.modules.production_project.domain.project import ProjectSettings
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from tests.integration.media_pipeline.test_layer4_pipeline import no_speech_clip

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]
CTX = JobContext.detached()


@dataclass
class World:
    app: Application
    anchor: str


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    try:
        clip = no_speech_clip(tmp_path / "clip.mp4")
        ran = app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(
                paths=[clip],
                stages=LAYER4_STAGES | LAYER5_STAGES,
                settings=StageSettings(output_format="9:16", layer4_render=False),
            ),
            CTX,
        )
        assert isinstance(ran, Ok)
        yield World(app, ran.value.assets[0].asset_id)
    finally:
        app.shutdown()


def test_the_facts_come_from_the_stored_build(world: World) -> None:
    found = world.app.container.resolve(BuiltTimelineCatalog).latest(world.anchor)
    assert isinstance(found, Ok) and found.value is not None
    built = found.value[0]

    facts = world.app.container.resolve(TimelineSource).facts(world.anchor)

    assert isinstance(facts, Ok)
    expected = built.settings.duration_frames * (
        built.settings.frame_rate.denominator / built.settings.frame_rate.numerator
    )
    assert facts.value.duration_seconds == pytest.approx(expected)
    assert set(facts.value.used_assets) == {
        c.source.original_asset_id for c in built.video_clips() if c.source and c.enabled
    } | {c.source.original_asset_id for c in built.audio_clips() if c.source and c.enabled}
    assert facts.value.transitions == tuple(t.kind for t in built.transitions)


def test_an_unknown_anchor_is_a_plain_not_found(world: World) -> None:
    facts = world.app.container.resolve(TimelineSource).facts("nope")

    assert isinstance(facts, Err)
    assert "Timeline" in facts.error.user_message


def test_attached_to_a_project_the_gate_checks_the_timeline(world: World) -> None:
    workflow = world.app.container.resolve(ProductionWorkflow)
    created = workflow.create_project(
        ProjectSettings("lidlnico", "Lidl Test", "Lidl", "ranking", 2026)
    )
    assert isinstance(created, Ok)
    project_id = created.value.project_id

    attached = workflow.attach_timeline(project_id, world.anchor)

    assert isinstance(attached, Ok)
    assert attached.value.timeline is not None
    assert attached.value.timeline.duration_seconds > 0
    assert isinstance(workflow.check_gate(project_id), Ok)
    missing = workflow.attach_timeline(project_id, "nope")
    assert isinstance(missing, Err)
