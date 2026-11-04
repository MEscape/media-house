"""End to end with the real application: import -> measure -> plan -> render, all cached."""

from pathlib import Path

import numpy as np

from media_house.bootstrap.application import Application
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.music_and_sound_design.application.contracts import (
    ClipAudio,
    EventSource,
    ImportSoundLibraryCommand,
    LicencePolicy,
    MusicAnalysisCatalog,
    MusicMode,
    OriginalAudioMode,
    PlanSoundDesignCommand,
    RenderSoundStemCommand,
    SfxProfileCatalog,
    SoundDesignCatalog,
    SoundDesignConfig,
    SoundDesignPlanner,
    SoundKind,
    SoundLibraryCatalog,
    SoundLibraryImporter,
    SoundQuery,
    SoundStemRenderer,
    StemOptions,
)
from media_house.modules.music_and_sound_design.infrastructure.ffmpeg_audio import FfmpegAudio
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok
from tests.integration.music_and_sound_design.conftest import SynthLibrary, needs_ffmpeg
from tests.support.sound_fakes import clip, event, section, speech, timeline, write_tone_wav

pytestmark = needs_ffmpeg


def _import(app: Application, library: SynthLibrary) -> object:
    importer = app.container.resolve(SoundLibraryImporter)
    result = importer.execute(ImportSoundLibraryCommand(library.manifest), JobContext.detached())
    assert isinstance(result, Ok), result
    return result.value


def _recording(app: Application, tmp_path: Path) -> str:
    path = write_tone_wav(tmp_path / "recording.wav", seconds=30.0, frequency=220.0)
    imported = app.container.resolve(MediaLibrary).import_file(path)
    assert isinstance(imported, Ok), imported
    return imported.value.asset.id


def test_import_is_idempotent_and_every_sound_is_measured_once(
    app: Application, synth_library: SynthLibrary
) -> None:
    first = _import(app, synth_library)
    second = _import(app, synth_library)

    assert [s.created for s in first.sounds] == [True] * 4  # type: ignore[attr-defined]
    assert len(first.analysis.analysed) == 4  # type: ignore[attr-defined]
    assert not any(s.created for s in second.sounds)  # type: ignore[attr-defined]
    assert second.analysis.analysed == ()  # type: ignore[attr-defined]
    assert len(second.analysis.reused) == 4  # type: ignore[attr-defined]
    assert first.not_cleared == ("music/clicks100.wav",)  # type: ignore[attr-defined]
    assert first.no_training == 1  # type: ignore[attr-defined]

    catalog = app.container.resolve(SoundLibraryCatalog)
    sounds = {s.title: s for s in catalog.sounds()}
    assert set(sounds) == {"clicks120", "clicks100", "impact", "whoosh"}
    music = sounds["clicks120"]
    assert music.licence.attribution_text == '"Clicks 120" test (CC BY)'
    assert music.stated is not None and music.stated.bpm == 120.0
    assert music.music is not None and music.music.measurement.tempo is not None
    assert abs(music.music.measurement.tempo.bpm - 120.0) / 120.0 < 0.02
    assert music.music.measurement.vocal_presence == "not_available"
    assert sounds["impact"].sfx is not None
    assert sounds["impact"].sfx.measurement.attack_seconds < 0.02

    stored = app.container.resolve(MusicAnalysisCatalog).latest(music.asset_id)
    assert isinstance(stored, Ok) and stored.value is not None
    sfx = app.container.resolve(SfxProfileCatalog).latest(sounds["impact"].asset_id)
    assert isinstance(sfx, Ok) and sfx.value is not None

    cleared = catalog.search(SoundQuery(kind=SoundKind.MUSIC))
    assert [m.asset.title for m in cleared] == ["clicks120"]  # null licence excluded by default
    everything = catalog.search(
        SoundQuery(kind=SoundKind.MUSIC, policy=LicencePolicy(allow_unconfirmed=True))
    )
    assert {m.asset.title for m in everything} == {"clicks120", "clicks100"}


def test_plan_is_stored_reused_and_rendered_as_a_float_stem(
    app: Application, synth_library: SynthLibrary, tmp_path: Path
) -> None:
    _import(app, synth_library)
    anchor = _recording(app, tmp_path)
    soundtrack = timeline(
        duration=30.0,
        clips=(clip("c1", 0.0, 30.0, asset_id=anchor),),
        sections=(section("s1", 0.0, 12.0, "hook"), section("s2", 12.0, 30.0, "explanation")),
        speech_spans=(speech(13.0, 20.0),),
        events=(
            event("zoom-1", 12.0, "zoom", source=EventSource.TRANSITION),
            event("cut-1", 20.0, "cut", source=EventSource.TRANSITION),
        ),
    )
    config = SoundDesignConfig(overrides={"music.categories.explanation": ["hype_energetic"]})
    planner = app.container.resolve(SoundDesignPlanner)

    first = planner.execute(PlanSoundDesignCommand(soundtrack, config), JobContext.detached())
    again = planner.execute(PlanSoundDesignCommand(soundtrack, config), JobContext.detached())

    assert isinstance(first, Ok), first
    assert isinstance(again, Ok), again
    plan = first.value.plan
    assert first.value.created and not again.value.created
    assert again.value.plan.plan_hash == plan.plan_hash
    # hook and explanation differ in energy: two cues; only one cleared track exists
    assert [c.mode for c in plan.music] == [MusicMode.MUSIC_ENABLED] * 2
    assert {c.asset.title for c in plan.music if c.asset} == {"clicks120"}
    assert next(x.kind.value for x in plan.crossfades) == "tail"
    assert plan.credit_lines == '"Clicks 120" test (CC BY)'
    assert {d.key.value for d in plan.ducking} == {"speech"}
    assert [e.sync_event_id for e in plan.sfx] == ["zoom-1"]
    assert [n.sync_event_id for n in plan.no_sfx] == ["cut-1"]
    stored = app.container.resolve(SoundDesignCatalog).latest(anchor)
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0] == plan

    renderer = app.container.resolve(SoundStemRenderer)
    stem = renderer.execute(RenderSoundStemCommand(first.value.asset.id), JobContext.detached())
    cached = renderer.execute(RenderSoundStemCommand(first.value.asset.id), JobContext.detached())
    plain = renderer.execute(
        RenderSoundStemCommand(first.value.asset.id, StemOptions(include_ducking=False)),
        JobContext.detached(),
    )

    assert isinstance(stem, Ok), stem
    assert isinstance(cached, Ok) and not cached.value.created
    assert isinstance(plain, Ok) and plain.value.created
    asset = stem.value.asset
    assert asset.technical.get("audio_codec") == "pcm_f32le"
    assert asset.technical.get("sample_rate") == 48000
    assert asset.duration_seconds is not None and abs(asset.duration_seconds - 30.0) < 0.05
    assert asset.metadata["mastered"] is False
    assert asset.metadata["plan_hash"] == plan.plan_hash

    # The rendered audio does what the plan says: music plays, and is ducked under speech.
    ducked = _levels(app, stem.value.asset.id, tmp_path / "a.f32")
    flat = _levels(app, plain.value.asset.id, tmp_path / "b.f32")
    depth = plan.ducking[0].depth_db
    assert ducked["before"] > -60.0
    assert abs((flat["speech"] - ducked["speech"]) - depth) < 1.5
    assert abs(flat["before"] - ducked["before"]) < 0.5


def _levels(app: Application, asset_id: str, scratch: Path) -> dict[str, float]:
    """RMS (dBFS) of the stem before the speech and in the middle of the ducked speech."""
    path = app.container.resolve(MediaLibrary).local_path(asset_id)
    assert isinstance(path, Ok)
    samples = app.container.resolve(FfmpegAudio).decode_mono(
        path.value, scratch, 48_000, CancellationToken()
    )

    def rms(a: float, b: float) -> float:
        part = samples[int(a * 48_000) : int(b * 48_000)]
        return float(20 * np.log10(max(float(np.sqrt(np.mean(part**2))), 1e-9)))

    return {"before": rms(2.0, 10.0), "speech": rms(14.0, 19.0)}


def test_no_speech_recording_gets_a_plan_from_the_format_profile(
    app: Application, synth_library: SynthLibrary, tmp_path: Path
) -> None:
    _import(app, synth_library)
    anchor = _recording(app, tmp_path)
    bare = timeline(
        duration=30.0,
        edit=None,
        clips=(clip("rec", 0.0, 30.0, asset_id=anchor, audio=ClipAudio.SOURCE_SYNC),),
    )
    planner = app.container.resolve(SoundDesignPlanner)

    compilation = planner.execute(
        PlanSoundDesignCommand(bare, SoundDesignConfig(profile="compilation")),
        JobContext.detached(),
    )
    meme = planner.execute(
        PlanSoundDesignCommand(
            bare,
            SoundDesignConfig(
                profile="shorts_meme",
                overrides={"music.categories.hook": ["hype_energetic"]},
            ),
        ),
        JobContext.detached(),
    )

    assert isinstance(compilation, Ok), compilation
    assert isinstance(meme, Ok), meme
    plan = compilation.value.plan
    assert plan.edit is None and plan.project_id is None
    assert all(c.segments == () for c in plan.music)
    assert [o.mode for o in plan.original_audio] == [OriginalAudioMode.DOMINANT]
    assert plan.ducking == ()
    assert [c.mode for c in meme.value.plan.music] == [MusicMode.MUSIC_ENABLED]
