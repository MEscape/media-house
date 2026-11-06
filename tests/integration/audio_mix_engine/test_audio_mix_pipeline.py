"""The audio mix engine end to end through the real composition root and real FFmpeg:

raw clip -> sound library -> Layer 4 -> specification -> built timeline -> MIX (stems + final mix)
-> measured, judged, stored -> reused when nothing changed -> revised without re-rendering the
rest.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_mix_engine.application.contracts import (
    AudioMixCreator,
    AudioMixer,
    CreateSpecCommand,
    MixCatalog,
    MixConfig,
    MixOverrides,
    MixRenderResult,
    RenderMixCommand,
    Severity,
)
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

    def mix(self, config: MixConfig | None = None) -> MixRenderResult:
        result = self.app.container.resolve(AudioMixer).execute(
            RenderMixCommand(anchor_asset_id=self.anchor, config=config or MixConfig()), CTX
        )
        assert isinstance(result, Ok), result
        return result.value


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
        yield World(app, ran.value.assets[0].asset_id)
    finally:
        app.shutdown()


def stem_ids(done: MixRenderResult) -> dict[str, str]:
    return {s.bus_id: s.asset_id for s in done.result.stems}


def test_a_timeline_becomes_stems_and_a_final_mix_that_meets_its_delivery(world: World) -> None:
    done = world.mix()

    result, spec = done.result, done.spec
    assert result.ok or result.state.value == "partial", result.errors
    assert {"dialogue", "music"} <= set(stem_ids(done)), stem_ids(done)
    assert [s.stem_id for s in spec.stems] == [f"stem:{b}" for b in stem_ids(done)]
    assert result.mix is not None and done.mix_asset is not None
    m = result.mix.measurement
    # delivery: youtube -14 LUFS, true peak under -1, 24-bit 48 kHz stereo
    assert m.integrated_lufs is not None and abs(m.integrated_lufs + 14.0) <= 1.0
    assert m.true_peak_dbtp is not None and m.true_peak_dbtp <= -0.9
    assert (m.sample_rate, m.channels, m.codec) == (48000, 2, "pcm_s24le")
    assert not [i for i in result.issues if i.severity in {Severity.ERROR, Severity.CRITICAL}], [
        (i.code, i.message) for i in result.issues
    ]
    # stems are aligned with the mix and each measured
    assert all(s.measurement.duration_samples == m.duration_samples for s in result.stems)
    assert result.corrections and result.corrections[0].iteration == 0
    # every output is a derived asset of the recording, and the documents are found again
    library = world.app.container.resolve(MediaLibrary)
    derived = {a.id for a in library.list_derived(world.anchor)}
    assert {done.mix_asset.id, done.spec_asset.id, done.result_asset.id} <= derived
    assert set(stem_ids(done).values()) <= derived
    catalog = world.app.container.resolve(MixCatalog)
    latest = catalog.latest_result(world.anchor, spec.timeline_id)
    assert isinstance(latest, Ok) and latest.value is not None
    assert latest.value[0].mix_job_id == result.mix_job_id
    stored = catalog.latest_spec(world.anchor, spec.timeline_id)
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0].content_hash == spec.content_hash


def test_the_dialogue_is_levelled_gently_and_recorded(world: World) -> None:
    done = world.mix()

    levels = done.result.dialogue_levels
    assert levels and all(abs(lv.trim_db) <= 6.0 for lv in levels)
    assert any(r.stage == "dialogue levelling" for r in done.result.processing)


def test_the_music_is_ducked_under_the_voice_from_the_sound_designs_instructions(
    world: World,
) -> None:
    done = world.mix()

    assert done.spec.ducking and all(d.clip_ids for d in done.spec.ducking)
    music = [
        c
        for c in done.spec.clips
        if c.track_id in {t.track_id for t in done.spec.tracks if t.bus_id == "music"}
    ]
    assert music and music[0].origin_ref == music[0].clip_id


def test_a_second_mix_of_an_unchanged_timeline_renders_nothing(world: World) -> None:
    first = world.mix()

    second = world.mix()

    assert stem_ids(second) == stem_ids(first)
    assert second.result.reused_stems == len(second.result.stems)
    assert second.result.mix is not None and second.result.mix.reused
    assert first.mix_asset is not None and second.mix_asset is not None
    assert second.mix_asset.id == first.mix_asset.id
    assert second.spec_asset.id == first.spec_asset.id
    assert second.result.master_trim_db == first.result.master_trim_db


def test_a_dialogue_revision_re_renders_the_dialogue_and_the_mix_but_not_the_music(
    world: World,
) -> None:
    first = world.mix()
    bus_of = {t.track_id: t.bus_id for t in first.spec.tracks}
    dialogue_clip = next(c for c in first.spec.clips if bus_of[c.track_id] == "dialogue")

    revised = world.mix(
        MixConfig(overrides=MixOverrides(clip_gain_db=((dialogue_clip.clip_id, -3.0),)))
    )

    before, after = stem_ids(first), stem_ids(revised)
    assert after["dialogue"] != before["dialogue"]
    assert after["music"] == before["music"]
    assert revised.mix_asset is not None and first.mix_asset is not None
    assert revised.mix_asset.id != first.mix_asset.id
    assert revised.spec.provenance.overrides == (f"clip:{dialogue_clip.clip_id}=-3",)
    # the earlier valid mix is still there, untouched
    library = world.app.container.resolve(MediaLibrary)
    assert library.get(first.mix_asset.id) is not None


def test_a_new_loudness_target_re_renders_only_the_final_mix(world: World) -> None:
    first = world.mix()

    broadcast = world.mix(MixConfig(delivery="broadcast_r128"))

    assert stem_ids(broadcast) == stem_ids(first)
    assert broadcast.result.reused_stems == len(broadcast.result.stems)
    assert broadcast.result.mix is not None and not broadcast.result.mix.reused
    lufs = broadcast.result.mix.measurement.integrated_lufs
    assert lufs is not None and abs(lufs + 23.0) <= 1.0


def test_a_music_bus_change_leaves_the_voice_alone(world: World) -> None:
    first = world.mix()

    revised = world.mix(MixConfig(overrides=MixOverrides(bus_gain_db=(("music", -3.0),))))

    assert stem_ids(revised)["dialogue"] == stem_ids(first)["dialogue"]
    assert stem_ids(revised)["music"] != stem_ids(first)["music"]


def test_the_specification_can_be_made_without_rendering(world: World) -> None:
    made = world.app.container.resolve(AudioMixCreator).execute(
        CreateSpecCommand(anchor_asset_id=world.anchor)
    )

    assert isinstance(made, Ok) and made.value.created
    again = world.app.container.resolve(AudioMixCreator).execute(
        CreateSpecCommand(anchor_asset_id=world.anchor)
    )
    assert isinstance(again, Ok) and not again.value.created
    assert again.value.asset.id == made.value.asset.id


def test_a_recording_without_a_built_timeline_is_refused_with_the_reason(world: World) -> None:
    other = world.app.container.resolve(AudioMixer).execute(
        RenderMixCommand(anchor_asset_id="nothing-here"), CTX
    )

    assert isinstance(other, Err)
