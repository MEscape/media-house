"""Layer 4 through the real composition root: one pipeline from a raw clip to stored plans.

A synthetic clip (FFmpeg ``testsrc2`` + a sine tone) has no speech, so no edit plan exists and
every Layer 4 task takes the no-speech path: one clip of the whole recording, the inspected frame
rate, ``edit=None``. The plans must be stored as documents of the clip, made in ONE style (the
same fingerprint everywhere), and a second run must compute nothing. A synthesised sound library
gives the sound design music to choose, so its stem is really rendered (FFmpeg).
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.caption_engine.application.contracts import CaptionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    MediaPipeline,
    PipelineReport,
    RunPipelineCommand,
    StageSettings,
    TaskKind,
    TaskStatus,
)
from media_house.modules.motion_graphics_engine.application.contracts import GraphicsCatalog
from media_house.modules.music_and_sound_design.application.contracts import (
    ImportSoundLibraryCommand,
    SoundDesignCatalog,
    SoundLibraryImporter,
)
from media_house.modules.transition_engine.application.contracts import TransitionCatalog
from media_house.modules.vfx_engine.application.contracts import VfxCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from tests.integration.music_and_sound_design.conftest import build_library
from tests.support.media_factories import ffmpeg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]

K = TaskKind
#: 9:16: the speech-free Short (``shorts_meme``) the no-speech path exists for.
SETTINGS = StageSettings(output_format="9:16")


@pytest.fixture
def application(tmp_path: Path) -> Iterator[Application]:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield app
    app.shutdown()


def no_speech_clip(path: Path) -> Path:
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=48000",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
           str(path))  # fmt: skip
    return path


def run(app: Application, clip: Path) -> PipelineReport:
    result = app.container.resolve(MediaPipeline).execute(
        RunPipelineCommand(paths=[clip], stages=LAYER4_STAGES, settings=SETTINGS),
        JobContext.detached(),
    )
    assert isinstance(result, Ok)
    return result.value


def test_the_no_speech_path_stores_linked_plans_in_one_style_and_reuses_them(
    application: Application, tmp_path: Path
) -> None:
    library = build_library(tmp_path / "sounds")
    imported = application.container.resolve(SoundLibraryImporter).execute(
        ImportSoundLibraryCommand(library.manifest), JobContext.detached()
    )
    assert isinstance(imported, Ok)
    clip = no_speech_clip(tmp_path / "meme.mp4")

    first = run(application, clip)

    asset = first.assets[0]
    tasks = {t.kind: t for t in asset.tasks}
    for kind in LAYER4_STAGES:
        assert tasks[kind].status is TaskStatus.SUCCEEDED, (kind, tasks[kind].detail)
        assert tasks[kind].facts["path"] == "no_speech"
    assert first.ok
    package: Any = json.loads(json.dumps(first.to_dict()))["assets"][0]["layer4"]
    assert package["path"] == "no_speech" and package["edit"] is None
    assert package["anchor_asset_id"] == asset.asset_id
    assert set(package["plans"]) == {k.value for k in LAYER4_STAGES}
    style = package["style"]
    assert style["output"] == "1080x1920" and style["profile"] == "shorts_meme"

    # Every plan is a stored document of the clip, made for no edit, in the same style.
    plans = package["plans"]
    c = application.container
    graphics = c.resolve(GraphicsCatalog).load(plans["graphics"]["plan_asset_id"])
    captions = c.resolve(CaptionCatalog).load(plans["captions"]["plan_asset_id"])
    transitions = c.resolve(TransitionCatalog).load(plans["transitions"]["plan_asset_id"])
    vfx = c.resolve(VfxCatalog).load(plans["vfx"]["plan_asset_id"])
    sound = c.resolve(SoundDesignCatalog).load(plans["sound_design"]["plan_asset_id"])
    assert isinstance(graphics, Ok) and isinstance(captions, Ok) and isinstance(transitions, Ok)
    assert isinstance(vfx, Ok) and isinstance(sound, Ok)
    visual = (graphics.value, captions.value, transitions.value, vfx.value)
    assert {p.style.fingerprint for p in visual if p.style} == {style["fingerprint"]}
    assert all(
        p.style is not None and p.style.document_id == style["plan_asset_id"] for p in visual
    )
    assert all(p.edit is None for p in (*visual, sound.value))
    assert sound.value.anchor_asset_id == asset.asset_id
    assert transitions.value.transitions  # the start and end of the recording are bookended
    derived = {a.id for a in c.resolve(MediaLibrary).list_derived(asset.asset_id)}
    assert {p["plan_asset_id"] for p in plans.values()} <= derived

    # Music was chosen from the imported library and its stem rendered as a derived asset.
    assert sound.value.music and package["rendered"]["sound_design"]
    [stem] = package["rendered"]["sound_design"]
    assert stem in derived

    again = run(application, clip)

    statuses = {t.kind: t.status for t in again.assets[0].tasks if t.kind in LAYER4_STAGES}
    assert set(statuses.values()) == {TaskStatus.REUSED}
    assert again.assets[0].layer4_package() == first.assets[0].layer4_package()


def test_a_sponsored_short_renders_its_mandatory_label_through_hyperframes(
    tmp_path: Path,
) -> None:
    """The graphics task really renders (ProRes 4444 with alpha) when HyperFrames is set up: the
    "Werbung" label the style mandates over a sponsored timeline (100 frames at 25 fps)."""
    from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import (
        install_dir,
        locate,
    )
    from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
    from media_house.shared.filesystem import AppPaths
    from tests.integration.motion_graphics_engine.conftest import with_motion

    install = install_dir(AppPaths.for_user())
    if isinstance(locate(install), str):
        pytest.skip("no HyperFrames (uv run python scripts/setup_hyperframes.py)")
    app = Application.start(
        StartupOptions(
            env={},
            home=tmp_path / "home",
            environment=Environment.TEST,
            modules=with_motion(MotionGraphicsEngineModule(install)),
        )
    )
    try:
        clip = no_speech_clip(tmp_path / "test.mp4")
        result = app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(
                paths=[clip],
                stages=frozenset({K.GRAPHICS}),
                settings=StageSettings(output_format="9:16", sponsored=True),
            ),
            JobContext.detached(),
        )
        assert isinstance(result, Ok)
        graphics = result.value.assets[0].task(K.GRAPHICS)
        assert graphics is not None and graphics.status is TaskStatus.SUCCEEDED, graphics
        labels = graphics.facts["rendered_asset_ids"]
        assert isinstance(labels, list) and len(labels) == 1
        rendered = app.container.resolve(MediaLibrary).get(str(labels[0]))
        assert isinstance(rendered, Ok) and rendered.value.media_type.value == "video"
    finally:
        app.shutdown()
