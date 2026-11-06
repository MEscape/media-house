"""Layer 5 end to end through the real composition root:
raw clip -> Layer 4 plans -> timeline specification -> BUILT TIMELINE -> a person's change.

The real files are verified through the library, the specification is read from its stored
document, the built timeline is stored as a document of the clip, a second run builds nothing,
and a recorded change of a person survives the next run instead of being overwritten.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from media_house.bootstrap.application import Application, StartupOptions
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
from media_house.modules.timeline_builder.application.contracts import (
    BuildMode,
    BuiltTimelineCatalog,
    EditKind,
    HumanEdit,
    Provenance,
    RecordEditsCommand,
    Role,
    TimelineEditor,
    TrackKind,
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


def test_a_clip_becomes_a_stored_built_timeline_that_is_reused_and_keeps_a_persons_change(
    application: Application, tmp_path: Path
) -> None:
    clip = no_speech_clip(tmp_path / "meme.mp4")

    first = run(application, clip)

    asset = first.assets[0]
    build = asset.task(K.TIMELINE_BUILD)
    assert build is not None
    assert build.status in (TaskStatus.SUCCEEDED, TaskStatus.PARTIAL), build.detail
    package: Any = json.loads(json.dumps(first.to_dict()))["assets"][0]["built_timeline"]
    assert package["document_type"] == "built_timeline" and package["mode"] == "new"
    assert package["video_clips"] == 1 and package["audio_clips"] >= 1

    catalog = application.container.resolve(BuiltTimelineCatalog)
    loaded = catalog.load(package["plan_asset_id"])
    assert isinstance(loaded, Ok)
    timeline = loaded.value

    # The built timeline is the specification's, constructed: tracks hold their clips in order.
    assert timeline.anchor_asset_id == asset.asset_id and timeline.edit is None
    assert timeline.settings.aspect == (9, 16) and timeline.settings.frame_rate.numerator > 0
    v1 = timeline.track("V1")
    assert v1 is not None and v1.kind is TrackKind.VIDEO and v1.role is Role.PRIMARY
    [picture] = v1.video
    assert picture.source is not None and picture.source.source_id == asset.asset_id
    assert (picture.start_frame, picture.end_frame) == (0, 100)
    dialogue = [c for t in timeline.tracks if t.role is Role.DIALOGUE for c in t.audio]
    assert dialogue and dialogue[0].video_link == picture.clip_id
    assert picture.audio_link == dialogue[0].clip_id

    # The real file was verified and the specification it came from is named.
    source = timeline.source(asset.asset_id)
    assert source is not None and source.available and source.original_asset_id == asset.asset_id
    assert timeline.build.spec_asset_id == asset.task(K.TIMELINE).facts["plan_asset_id"]  # type: ignore[union-attr]
    assert timeline.identity.style_fingerprint
    derived = {
        a.id for a in application.container.resolve(MediaLibrary).list_derived(asset.asset_id)
    }
    assert package["plan_asset_id"] in derived

    # A second run builds nothing: the stored document is served.
    again = run(application, clip)
    repeated = again.assets[0].build_package()
    assert repeated is not None and repeated["plan_asset_id"] == package["plan_asset_id"]
    assert repeated["build_hash"] == package["build_hash"]
    assert len(catalog.history(asset.asset_id)) == 1

    # A person lowers the dialogue: recorded as the next version, marked as theirs.
    editor = application.container.resolve(TimelineEditor)
    recorded = editor.execute(
        RecordEditsCommand(
            anchor_asset_id=asset.asset_id,
            edits=(
                HumanEdit(
                    EditKind.MODIFY,
                    dialogue[0].clip_id,
                    (("gain_db", "-6"),),
                    editor="editor",
                    note="too loud",
                ),
            ),
        ),
        JobContext.detached(),
    )
    assert isinstance(recorded, Ok) and recorded.value.created
    edited = recorded.value.timeline
    mine = edited.audio_clip(dialogue[0].clip_id)
    assert mine is not None and mine.gain_db == -6.0
    assert mine.provenance is Provenance.HUMAN_MODIFIED
    assert edited.build.build_version == 2 and edited.build.supersedes == package["plan_asset_id"]

    # The next run finds that document (same inputs, same work by people): nothing is rebuilt,
    # nothing is overwritten.
    after = run(application, clip)
    last = after.assets[0].build_package()
    assert last is not None and last["plan_asset_id"] == recorded.value.asset.id
    newest = catalog.latest(asset.asset_id)
    assert isinstance(newest, Ok) and newest.value is not None
    kept = newest.value[0].audio_clip(dialogue[0].clip_id)
    assert kept is not None and kept.gain_db == -6.0 and kept.human is not None
    assert len(catalog.history(asset.asset_id)) == 2


def test_asking_for_the_build_alone_pulls_in_the_timeline_and_the_style(
    application: Application, tmp_path: Path
) -> None:
    clip = no_speech_clip(tmp_path / "solo.mp4")

    report = run(application, clip, frozenset({K.TIMELINE_BUILD}))

    asset = report.assets[0]
    for kind in (K.TIMELINE, K.VISUAL_STYLE, K.TIMELINE_BUILD):
        task = asset.task(kind)
        assert task is not None and task.status in (TaskStatus.SUCCEEDED, TaskStatus.PARTIAL)
    graphics = asset.task(K.GRAPHICS)
    assert graphics is not None and graphics.status is TaskStatus.SKIPPED_DISABLED
    loaded = application.container.resolve(BuiltTimelineCatalog).load(
        str(asset.task(K.TIMELINE_BUILD).facts["plan_asset_id"])  # type: ignore[union-attr]
    )
    assert isinstance(loaded, Ok)
    assert loaded.value.build.mode is BuildMode.NEW and not loaded.value.mix
