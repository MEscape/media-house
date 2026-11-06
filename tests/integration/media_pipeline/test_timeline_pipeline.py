"""Layer 5 through the real composition root: raw clip -> Layer 4 plans -> timeline specification.

A synthetic clip has no speech, so the no-speech path applies end to end: one picture clip and its
own sound, ``edit=None``. The compiler must read the REAL stored Layer 4 plans of the run, verify
the real files through the library and the stored inspection, bind the channel identity of the
style, store the specification as a document of the clip, and a second run must compile nothing.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.edit_plan_compiler.application.contracts import (
    IssueClass,
    TimelineCatalog,
    TrackRole,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    LAYER5_STAGES,
    MediaPipeline,
    PipelineReport,
    RunPipelineCommand,
    StageSettings,
    TaskKind,
    TaskStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from tests.integration.media_pipeline.test_layer4_pipeline import no_speech_clip

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]

K = TaskKind
SETTINGS = StageSettings(output_format="9:16", layer4_render=False)
STAGES = LAYER4_STAGES | LAYER5_STAGES


@pytest.fixture
def application(tmp_path: Path) -> Iterator[Application]:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield app
    app.shutdown()


def run(app: Application, clip: Path, stages: frozenset[TaskKind] = STAGES) -> PipelineReport:
    result = app.container.resolve(MediaPipeline).execute(
        RunPipelineCommand(paths=[clip], stages=stages, settings=SETTINGS), JobContext.detached()
    )
    assert isinstance(result, Ok)
    return result.value


def test_a_clip_is_compiled_into_a_stored_timeline_in_its_channel_identity_and_reused(
    application: Application, tmp_path: Path
) -> None:
    clip = no_speech_clip(tmp_path / "meme.mp4")

    first = run(application, clip)

    asset = first.assets[0]
    timeline = asset.task(K.TIMELINE)
    assert timeline is not None
    assert timeline.status in (TaskStatus.SUCCEEDED, TaskStatus.PARTIAL), timeline.detail
    package: Any = json.loads(json.dumps(first.to_dict()))["assets"][0]["timeline"]
    assert package["document_type"] == "timeline_specification"
    assert package["video_items"] == 1 and package["audio_items"] >= 1

    catalog = application.container.resolve(TimelineCatalog)
    loaded = catalog.load(package["plan_asset_id"])
    assert isinstance(loaded, Ok)
    spec = loaded.value
    assert spec.anchor_asset_id == asset.asset_id and spec.edit is None
    [video] = spec.video
    assert video.asset_id == asset.asset_id == video.original_asset_id
    assert video.track_id == "V1" and (video.start_frame, video.end_frame) == (0, 100)
    tracks = {t.track_id: t.role for t in spec.tracks}
    assert tracks["V1"] is TrackRole.PRIMARY
    assert any(tracks[a.track_id] is TrackRole.DIALOGUE for a in spec.audio)

    # The channel identity is a REFERENCE to the style of this run, verified by its fingerprint.
    style = first.to_dict()
    style_fp = json.loads(json.dumps(style))["assets"][0]["layer4"]["style"]["fingerprint"]
    assert spec.creator.style_fingerprint == style_fp
    assert spec.creator.output_width == 1080 and spec.creator.output_height == 1920
    assert all(i.klass is not IssueClass.CRITICAL for i in spec.issues)

    # Every Layer 4 plan of the run is an input of the specification.
    kinds = {i.kind for i in spec.inputs}
    assert {"visual_style", "graphics", "captions", "transitions", "vfx", "sound_design"} <= kinds

    # The real file was verified: present, traced, with its inspected frame rate.
    used = {a.asset_id: a for a in spec.assets}
    assert used[asset.asset_id].available and used[asset.asset_id].frame_rate is not None
    derived = {
        a.id for a in application.container.resolve(MediaLibrary).list_derived(asset.asset_id)
    }
    assert package["plan_asset_id"] in derived

    again = run(application, clip)

    # Same inputs: the stored specification is served, not recompiled (the task stays PARTIAL
    # because the specification itself is partial: unbound brand assets, no music in the library).
    repeated = again.assets[0].timeline_package()
    assert repeated is not None
    assert repeated["plan_asset_id"] == package["plan_asset_id"]
    assert repeated["spec_hash"] == package["spec_hash"] and repeated["plan_version"] == 1
    assert len(catalog.history(asset.asset_id)) == 1


def test_asking_for_the_timeline_alone_pulls_in_only_the_style_it_needs(
    application: Application, tmp_path: Path
) -> None:
    clip = no_speech_clip(tmp_path / "solo.mp4")

    report = run(application, clip, LAYER5_STAGES)

    asset = report.assets[0]
    timeline = asset.task(K.TIMELINE)
    assert timeline is not None and timeline.status in (TaskStatus.SUCCEEDED, TaskStatus.PARTIAL)
    assert asset.task(K.VISUAL_STYLE) is not None
    graphics = asset.task(K.GRAPHICS)
    assert graphics is not None and graphics.status is TaskStatus.SKIPPED_DISABLED
    loaded = application.container.resolve(TimelineCatalog).load(
        str(timeline.facts["plan_asset_id"])
    )
    assert isinstance(loaded, Ok)
    assert not loaded.value.overlays and not loaded.value.automation
