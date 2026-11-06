"""The Resolve adapter end to end through the real composition root:

raw clip -> Layer 4 -> specification -> built timeline -> RESOLVE (a stand-in of its documented
API) -> a person's fix in Resolve -> read back into Media House -> the next run keeps it -> a
render that is inspected.

Everything except Resolve is real: the library, the pipeline, the builder, ffmpeg, the stored
documents. Resolve is the stand-in of ``tests/support/resolve_fakes.py``: this proves the wiring
and the behaviour the documentation describes, not that a real Resolve behaves as documented.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.modules import installed_modules
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    LAYER5_STAGES,
    MediaPipeline,
    RunPipelineCommand,
    StageSettings,
)
from media_house.modules.resolve_adapter.application.contracts import (
    AudioLayouts,
    ExecuteTimelineCommand,
    ExecutionPolicy,
    ExecutionResult,
    Outcome,
    RenderCommand,
    ResolveAdapterConfig,
    ResolveCatalog,
    ResolveExecutor,
    ResolveRenderer,
    ResolveSynchronizer,
    ResolveUnavailable,
    SyncCommand,
)
from media_house.modules.resolve_adapter.application.store import ResolveStore
from media_house.modules.resolve_adapter.domain.binding import ConflictRecord, FieldConflict
from media_house.modules.resolve_adapter.domain.values import ChangeOrigin, Resolution
from media_house.modules.resolve_adapter.infrastructure.install import (
    ProcessProbe,
    ResolveInstall,
)
from media_house.modules.resolve_adapter.infrastructure.scripting import (
    ScriptingResolveBackend,
)
from media_house.modules.resolve_adapter.module import ResolveAdapterModule
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
    Provenance,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from tests.integration.media_pipeline.test_layer4_pipeline import no_speech_clip
from tests.support.media_factories import ffmpeg
from tests.support.resolve_fakes import FakeProject, FakeResolve, FakeRunner

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
    fake: FakeResolve
    anchor: str
    clip: Path

    def execute(self, config: ResolveAdapterConfig | None = None) -> ExecutionResult:
        command = ExecuteTimelineCommand(
            anchor_asset_id=self.anchor, config=config or ResolveAdapterConfig()
        )
        result = self.app.container.resolve(ResolveExecutor).execute(command, CTX)
        assert isinstance(result, Ok), result
        return result.value

    def built(self) -> BuiltTimeline:
        found = self.app.container.resolve(BuiltTimelineCatalog).latest(self.anchor)
        assert isinstance(found, Ok) and found.value is not None
        return found.value[0]

    def pipeline(self) -> None:
        result = self.app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(paths=[self.clip], stages=STAGES, settings=SETTINGS), CTX
        )
        assert isinstance(result, Ok)


def make_world(tmp_path: Path, fake: FakeResolve, *, running: bool = True) -> Iterator[World]:
    lib = tmp_path / "fusionscript.dll"
    lib.write_bytes(b"\x00")
    install = ResolveInstall(tmp_path, lib, "Resolve.exe", "win32")
    backend = ScriptingResolveBackend(
        install, ProcessProbe(FakeRunner(running=running), install), lambda _i: fake
    )
    modules = [m for m in installed_modules() if m.name != "resolve_adapter"]
    modules.append(ResolveAdapterModule(backend=backend))
    app = Application.start(
        StartupOptions(
            env={}, home=tmp_path / "home", environment=Environment.TEST, modules=modules
        )
    )
    try:
        clip = no_speech_clip(tmp_path / "meme.mp4")
        result = app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(paths=[clip], stages=STAGES, settings=SETTINGS), CTX
        )
        assert isinstance(result, Ok)
        yield World(app, fake, result.value.assets[0].asset_id, clip)
    finally:
        app.shutdown()


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    yield from make_world(tmp_path, FakeResolve())


def current(fake: FakeResolve) -> FakeProject:
    assert fake.current is not None
    return fake.current


def placed(fake: FakeResolve) -> int:
    timeline = current(fake).timeline
    return sum(len(lane) for lanes in timeline.tracks.values() for lane in lanes)


def test_a_built_timeline_becomes_a_verified_resolve_project_and_a_rerun_changes_nothing(
    world: World,
) -> None:
    built = world.built()

    first = world.execute()

    report = first.report
    assert report.outcome in (Outcome.PARTIAL, Outcome.COMPLETED_WITH_WARNINGS)
    assert report.connection.state == "connected" and report.connection.edition == "studio"
    assert report.project_name and report.timeline_name == report.project_name
    resolved_ids = {c.clip_id for c in built.video_clips() if c.state.value == "resolved"} | {
        c.clip_id for c in built.audio_clips() if c.state.value == "resolved"
    }
    assert placed(world.fake) == len(resolved_ids) >= 2
    assert report.failed_checks() == ()
    assert all(r.status in {"imported", "reused"} for r in report.media)
    assert not report.errors

    # the real files of the library are what Resolve was given
    library = world.app.container.resolve(MediaLibrary)
    anchor_path = library.local_path(world.anchor)
    assert isinstance(anchor_path, Ok)
    pool = current(world.fake).pool.root
    paths = {c.path for f in pool.folders[0].folders for c in f.clips}
    assert anchor_path.value in paths

    # the link and the report are documents of the recording in the library
    catalog = world.app.container.resolve(ResolveCatalog)
    linked = catalog.latest_binding(world.anchor, built.timeline_id)
    assert isinstance(linked, Ok) and linked.value is not None
    state = linked.value[0]
    assert state.project.project_name == report.project_name
    assert {e.element_id for e in state.elements} == resolved_ids
    assert catalog.reports(world.anchor)

    # a second run places nothing, imports nothing, makes no second timeline
    before = (world.fake.placed, len(current(world.fake).timelines))
    second = world.execute()
    assert (world.fake.placed, len(current(world.fake).timelines)) == before
    assert second.report.sync.status == "in_sync" and not second.report.sync.conflicts
    assert second.binding is not None and second.binding.sequence > state.sequence


def test_a_persons_fix_in_resolve_is_recorded_kept_and_never_overwritten(world: World) -> None:
    world.execute()
    built = world.built()
    picture = built.video_clips()[0]
    assert picture.audio_link is not None
    item = world.fake.human_item(picture.clip_id, current(world.fake))
    voice = world.fake.human_item(picture.audio_link, current(world.fake))
    item.duration -= 10  # the person trims ten frames off the end of the shot ...
    voice.duration -= 10  # ... and, because the two are linked, off its voice

    # 1. read it back: Media House learns what the person did, as the builder's own edit
    synced = world.app.container.resolve(ResolveSynchronizer).execute(
        SyncCommand(anchor_asset_id=world.anchor), CTX
    )
    assert isinstance(synced, Ok)
    assert not synced.value.report.errors, (synced.value.report.errors, synced.value.report.log)
    assert set(synced.value.recorded) == {picture.clip_id, picture.audio_link}
    assert synced.value.report.sync.status == "changes_found"
    edited = world.built()
    clip = edited.video_clip(picture.clip_id)
    assert clip is not None and clip.provenance is Provenance.HUMAN_MODIFIED
    assert (clip.start_frame, clip.end_frame) == (picture.start_frame, picture.end_frame - 10)
    assert edited.build.build_version == built.build.build_version + 1

    # 2. it is in sync now: nothing more to find
    again = world.app.container.resolve(ResolveSynchronizer).execute(
        SyncCommand(anchor_asset_id=world.anchor), CTX
    )
    assert isinstance(again, Ok) and again.value.recorded == ()

    # 3. the pipeline runs again with nothing new upstream: the person's version stays
    world.pipeline()
    kept = world.built().video_clip(picture.clip_id)
    assert kept is not None and kept.provenance is Provenance.HUMAN_MODIFIED

    # 4. and so does Resolve: not moved back
    run = world.execute()
    assert item.duration == picture.end_frame - picture.start_frame - 10
    assert run.report.sync.conflicts == ()
    assert world.fake.placed == len(world.built().video_clips()) + len(world.built().audio_clips())


def test_the_audio_structure_is_available_to_the_mix_engine_without_touching_resolve(
    world: World,
) -> None:
    world.execute()

    layout = world.app.container.resolve(AudioLayouts).layout(world.anchor)

    assert isinstance(layout, Ok)
    assert [t.role for t in layout.value.tracks][:1] == ["dialogue"]
    dialogue = layout.value.clips_of("dialogue")
    assert dialogue and all(c.resolve_item_id.startswith("item-") for c in dialogue)
    assert layout.value.project_name


def test_an_unchanged_timeline_is_rendered_once_and_the_file_is_inspected(tmp_path: Path) -> None:
    fake = FakeResolve()

    def write(path: Path, ext: str) -> None:
        timeline = current(fake).timeline
        seconds = (timeline.GetEndFrame() - timeline.GetStartFrame()) / 25
        ffmpeg(
            "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        )  # fmt: skip

    fake.render_writer = write
    for world in make_world(tmp_path, fake):
        world.execute()

        first = world.app.container.resolve(ResolveRenderer).execute(
            RenderCommand(anchor_asset_id=world.anchor), CTX
        )

        assert isinstance(first, Ok), first
        assert not first.value.reused and first.value.output is not None
        record = first.value.report.render
        assert record is not None and record.status == "complete" and record.output_asset_id
        assert all(c.state.value == "verified" for c in record.checks), record.checks
        assert {c.name for c in record.checks} >= {
            "file exists", "video codec", "resolution", "frame rate", "duration", "audio stream",
        }  # fmt: skip
        derived = world.app.container.resolve(MediaLibrary).list_derived(world.anchor)
        assert first.value.output.id in {a.id for a in derived}

        jobs = len(current(fake).jobs)
        second = world.app.container.resolve(ResolveRenderer).execute(
            RenderCommand(anchor_asset_id=world.anchor), CTX
        )
        assert isinstance(second, Ok) and second.value.reused
        assert second.value.output is not None and second.value.output.id == first.value.output.id
        assert len(current(fake).jobs) == jobs


def test_a_render_that_fails_is_a_failed_report_with_no_deliverable(tmp_path: Path) -> None:
    for world in make_world(tmp_path, FakeResolve(render_script=("Failed",))):
        world.execute()

        result = world.app.container.resolve(ResolveRenderer).execute(
            RenderCommand(anchor_asset_id=world.anchor), CTX
        )

        assert isinstance(result, Ok)
        assert result.value.output is None
        record = result.value.report.render
        assert record is not None and record.status == "failed" and record.error == "disk full"
        assert result.value.report.errors


def test_a_change_the_builder_refuses_stays_in_resolve_and_is_reported(world: World) -> None:
    world.execute()
    built = world.built()
    picture = built.video_clips()[0]
    item = world.fake.human_item(picture.clip_id, current(world.fake))
    item.duration -= 10  # only the picture: its linked voice would be out of sync

    synced = world.app.container.resolve(ResolveSynchronizer).execute(
        SyncCommand(anchor_asset_id=world.anchor), CTX
    )

    assert isinstance(synced, Ok)
    assert synced.value.recorded == () and "out of sync" in synced.value.report.errors[0]
    assert item.duration == picture.end_frame - picture.start_frame - 10  # Resolve is untouched
    untouched = world.built().video_clip(picture.clip_id)
    assert untouched is not None and untouched.provenance is Provenance.AI
    # the change is found again next time, not forgotten
    again = world.app.container.resolve(ResolveSynchronizer).execute(
        SyncCommand(anchor_asset_id=world.anchor), CTX
    )
    assert isinstance(again, Ok) and again.value.report.sync.human_modified == (picture.clip_id,)


def test_an_open_conflict_blocks_the_render(world: World) -> None:
    world.execute()
    built = world.built()
    store = world.app.container.resolve(ResolveStore)
    anchor = world.app.container.resolve(MediaLibrary).get(world.anchor)
    assert isinstance(anchor, Ok)
    found = store.latest_binding(world.anchor, built.timeline_id)
    assert isinstance(found, Ok) and found.value is not None
    state = found.value[0]
    conflict = ConflictRecord(
        built.video_clips()[0].clip_id,
        (FieldConflict("start", "0", "40", "30"),),
        ChangeOrigin.BOTH,
        Resolution.OPEN,
    )
    saved = store.save_binding(anchor.value, replace(state, conflicts=(conflict,), sequence=99))
    assert isinstance(saved, Ok)

    result = world.app.container.resolve(ResolveRenderer).execute(
        RenderCommand(anchor_asset_id=world.anchor), CTX
    )

    assert isinstance(result, Ok)
    report = result.value.report
    assert report.outcome is Outcome.FAILED and result.value.output is None
    assert any("conflict" in e for e in report.errors)
    assert not current(world.fake).jobs  # nothing was queued


def test_a_resolve_that_is_not_running_is_reported_before_anything_is_touched(
    tmp_path: Path,
) -> None:
    fake = FakeResolve()
    for world in make_world(tmp_path, fake, running=False):
        result = world.app.container.resolve(ResolveExecutor).execute(
            ExecuteTimelineCommand(anchor_asset_id=world.anchor), CTX
        )

        assert isinstance(result, Err) and isinstance(result.error, ResolveUnavailable)
        assert result.error.state == "not_running"
        assert world.app.container.resolve(ResolveCatalog).reports(world.anchor) == []
        assert fake.projects == {}


def test_create_new_builds_a_second_timeline_next_to_the_first(world: World) -> None:
    world.execute()

    again = world.execute(ResolveAdapterConfig(policy=ExecutionPolicy.CREATE_NEW))

    names = [t.name for t in current(world.fake).timelines]
    assert len(names) == 2 and names[0] != names[1]
    assert again.report.policy is ExecutionPolicy.CREATE_NEW
