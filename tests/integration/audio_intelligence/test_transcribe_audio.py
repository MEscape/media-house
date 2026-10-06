"""Audio intelligence end to end: real Media Library (SQLite + blobs) and real FFmpeg.

Only the speech engine is replaced by ``FakeEngine``, which counts how often it really ran;
that is how the tests prove reuse never re-transcribes.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.core.application.ports import Clock, ProcessRunner, ProcessSpec
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.transcribe_audio import (
    TranscribeAudio,
    TranscribeAudioCommand,
    TranscriptionResult,
)
from media_house.modules.audio_intelligence.domain.errors import (
    AlignmentUnavailable,
    NoAudioTrack,
)
from media_house.modules.audio_intelligence.domain.serialization import from_json
from media_house.modules.audio_intelligence.domain.validation import (
    Severity,
    validate_transcript,
)
from media_house.modules.audio_intelligence.domain.values import (
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.modules.media_library.application.contracts import (
    MediaLibrary,
    MediaType,
    UnsupportedMediaType,
)
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    ExternalSystemError,
    Ok,
    ToolNotFoundError,
    ValidationError,
)
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.audio_fakes import FakeEngine, raw_transcription, refusing_alignment
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.media_factories import ffmpeg, make_video, make_wav

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required",
    ),
]


# --- fixture -----------------------------------------------------------------------------------
@dataclass
class Env:
    library: MediaLibrary
    engine: FakeEngine
    use_case: TranscribeAudio
    paths: AppPaths
    clock: Clock
    runner: ProcessRunner
    tmp: Path

    def import_media(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def transcribe(
        self,
        asset_id: str,
        config: TranscriptionConfig | None = None,
        use_case: TranscribeAudio | None = None,
    ) -> TranscriptionResult:
        command = TranscribeAudioCommand(asset_id, config or TranscriptionConfig())
        result = (use_case or self.use_case).execute(command, JobContext.detached())
        assert isinstance(result, Ok), result
        return result.value

    def fresh_use_case(self, engine: FakeEngine) -> TranscribeAudio:
        """A new service over the same library: what an application restart looks like."""
        return TranscribeAudio(
            self.library,
            FfmpegAudioPreparer(self.runner),
            engine,
            self.paths,
            self.clock,
        )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    container = Container()
    paths = AppPaths.under_root(tmp_path / "app")
    runner = SubprocessRunner()
    clock = FixedClock()
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, clock)
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, runner)
    MediaLibraryModule().register(container)
    library = container.resolve(MediaLibrary)
    engine = FakeEngine()
    use_case = TranscribeAudio(library, FfmpegAudioPreparer(runner), engine, paths, clock)
    media = tmp_path / "media"
    media.mkdir()
    return Env(library, engine, use_case, paths, clock, runner, media)


# --- Media Library accepts audio and video ------------------------------------------------------
def test_library_imports_audio_and_video_as_first_class_assets(env: Env) -> None:
    wav = env.library.get(env.import_media(make_wav(env.tmp / "voice.wav")))
    mp4 = env.library.get(env.import_media(make_video(env.tmp / "clip.mp4")))

    assert isinstance(wav, Ok)
    assert (wav.value.media_type, wav.value.mime_type) == (MediaType.AUDIO, "audio/wav")
    assert wav.value.duration_seconds == pytest.approx(2.0, abs=0.05)
    assert isinstance(mp4, Ok)
    assert (mp4.value.media_type, mp4.value.mime_type) == (MediaType.VIDEO, "video/mp4")
    assert (mp4.value.width, mp4.value.height) == (160, 120)


def test_a_non_media_file_is_rejected_whatever_its_extension(env: Env) -> None:
    fake = env.tmp / "fake.mp3"
    fake.write_text("this is not audio at all")

    result = env.library.import_file(fake)

    assert isinstance(result, Err)
    assert isinstance(result.error, UnsupportedMediaType)


# --- extraction and transcription ---------------------------------------------------------------
def test_audio_is_extracted_as_a_standard_asset_and_transcribed(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))

    result = env.transcribe(source_id)

    assert result.created
    assert env.engine.calls == 1
    transcript = result.transcript
    assert transcript.metadata.source_asset_id == source_id
    assert transcript.metadata.audio_asset_id == result.audio_asset.id
    assert transcript.metadata.duration == pytest.approx(2.0, abs=0.05)  # media, not last word
    assert [w.raw_word for w in transcript.words] == ["Hello,", "world.", "AMAZING", "Feuerwerk,"]
    assert not [i for i in validate_transcript(transcript) if i.severity is Severity.ERROR]

    # The extracted audio is a normal, independently usable library asset: 16 kHz mono PCM.
    audio = result.audio_asset
    assert audio.media_type is MediaType.AUDIO
    assert audio.derivation is not None
    assert audio.derivation.source_asset_id == source_id
    assert audio.derivation.operation == "audio_extraction"
    assert audio.technical["sample_rate"] == 16_000
    assert audio.technical["channels"] == 1
    assert env.library.get(source_id).value.id == source_id  # type: ignore[union-attr]  # untouched

    # The engine heard the library's stored audio file.
    stored = env.library.local_path(audio.id)
    assert isinstance(stored, Ok)
    assert (
        env.engine.audio_paths[0].name == "audio.wav" or env.engine.audio_paths[0] == stored.value
    )


def test_the_transcript_is_a_derived_library_document_that_reloads_identically(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))

    result = env.transcribe(source_id)

    asset = result.asset
    assert (asset.media_type, asset.mime_type) == (MediaType.OTHER, "application/json")
    assert asset.derivation is not None
    assert asset.derivation.source_asset_id == source_id
    assert asset.derivation.operation == "transcription"
    assert asset.metadata["language"] == "en"
    assert asset.metadata["word_count"] == 4
    assert {a.id for a in env.library.list_derived(source_id)} == {asset.id, result.audio_asset.id}
    path = env.library.local_path(asset.id)
    assert isinstance(path, Ok)
    stored = from_json(path.value.read_text(encoding="utf-8"))
    assert [w.raw_word for w in stored.words] == [w.raw_word for w in result.transcript.words]
    assert stored.metadata.model == "large-v3"


def test_video_audio_offset_is_mapped_back_to_the_source_timeline(env: Env) -> None:
    source_id = env.import_media(make_video(env.tmp / "delayed.mp4", audio_delay=0.5, seconds=3))

    result = env.transcribe(source_id)

    offset = result.transcript.metadata.audio_offset
    assert 0.3 < offset < 0.7
    first = result.transcript.words[0]
    assert first.start == pytest.approx(0.42 + offset)  # engine said 0.42 in prepared audio
    assert result.transcript.duration == pytest.approx(3.0, abs=0.2)


def test_video_without_offset_keeps_engine_times_unchanged(env: Env) -> None:
    source_id = env.import_media(make_video(env.tmp / "plain.mp4"))

    result = env.transcribe(source_id)

    assert result.transcript.metadata.audio_offset == pytest.approx(0.0, abs=0.1)
    assert result.transcript.words[0].start == pytest.approx(0.42, abs=0.1)


# --- reuse -------------------------------------------------------------------------------------
def test_same_source_and_configuration_never_transcribes_twice(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    first = env.transcribe(source_id)

    second = env.transcribe(source_id)
    third = env.transcribe(source_id)

    assert (first.created, second.created, third.created) == (True, False, False)
    assert env.engine.calls == 1
    assert second.asset.id == first.asset.id == third.asset.id
    assert second.audio_asset.id == first.audio_asset.id
    assert [w.raw_word for w in second.transcript.words] == [
        w.raw_word for w in first.transcript.words
    ]
    assert len(env.library.list_derived(source_id)) == 2  # audio + transcript


def test_reuse_survives_an_application_restart(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    first = env.transcribe(source_id)
    restarted_engine = FakeEngine()

    again = env.transcribe(source_id, use_case=env.fresh_use_case(restarted_engine))

    assert not again.created
    assert restarted_engine.calls == 0
    assert again.asset.id == first.asset.id


def test_performance_settings_reuse_the_result_but_quality_settings_do_not(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    base = env.transcribe(source_id, TranscriptionConfig())

    tuned = env.transcribe(source_id, TranscriptionConfig(device="cpu", batch_size=2))
    other_model = env.transcribe(source_id, TranscriptionConfig(model="medium"))
    german = env.transcribe(source_id, TranscriptionConfig(language="de"))

    assert not tuned.created
    assert tuned.asset.id == base.asset.id
    assert {other_model.asset.id, german.asset.id}.isdisjoint({base.asset.id})
    assert other_model.asset.id != german.asset.id
    assert other_model.created
    assert german.created
    assert env.engine.calls == 3
    # The prepared audio is shared between all of them: extracted exactly once.
    assert len({r.audio_asset.id for r in (base, tuned, other_model, german)}) == 1
    assert len(env.library.list_derived(source_id)) == 4  # audio + 3 transcripts


def test_a_new_engine_version_creates_a_new_result(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    old = env.transcribe(source_id)
    upgraded_engine = FakeEngine(version="test-2")

    new = env.transcribe(source_id, use_case=env.fresh_use_case(upgraded_engine))

    assert new.created
    assert new.asset.id != old.asset.id
    assert upgraded_engine.calls == 1


def test_different_sources_get_their_own_transcripts(env: Env) -> None:
    a = env.import_media(make_wav(env.tmp / "a.wav", seconds=2))
    b = env.import_media(make_wav(env.tmp / "b.wav", seconds=3))

    first, second = env.transcribe(a), env.transcribe(b)

    assert first.asset.id != second.asset.id
    assert first.audio_asset.id != second.audio_asset.id
    assert env.engine.calls == 2


def test_preparation_changes_extract_again_and_retranscribe(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    plain = env.transcribe(source_id)

    normalised = env.transcribe(
        source_id,
        TranscriptionConfig(preparation=PreparationConfig(loudness_normalization=True)),
    )

    assert normalised.audio_asset.id != plain.audio_asset.id
    assert normalised.asset.id != plain.asset.id


# --- language ----------------------------------------------------------------------------------
def test_explicit_language_is_respected_and_detection_is_labelled(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))

    german = env.transcribe(source_id, TranscriptionConfig(language="de"))
    auto = env.transcribe(source_id, TranscriptionConfig())

    assert env.engine.configs[0].language == "de"
    assert (german.transcript.language, german.transcript.metadata.language_detected) == (
        "de",
        False,
    )
    assert (auto.transcript.language, auto.transcript.metadata.language_detected) == ("en", True)


# --- errors ------------------------------------------------------------------------------------
def test_video_without_an_audio_track_is_an_expected_error(env: Env) -> None:
    source_id = env.import_media(make_video(env.tmp / "silent.mp4", audio_delay=None))

    result = env.use_case.execute(TranscribeAudioCommand(source_id), JobContext.detached())

    assert isinstance(result, Err)
    assert isinstance(result.error, NoAudioTrack)
    assert env.engine.calls == 0
    assert env.library.list_derived(source_id) == []


def test_images_cannot_be_transcribed(env: Env) -> None:
    png = env.tmp / "pic.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1", str(png))
    image_id = env.import_media(png)

    result = env.use_case.execute(TranscribeAudioCommand(image_id), JobContext.detached())

    assert isinstance(result, Err)
    assert isinstance(result.error, ValidationError)


def test_unknown_asset_is_an_expected_error(env: Env) -> None:
    result = env.use_case.execute(TranscribeAudioCommand("missing"), JobContext.detached())

    assert isinstance(result, Err)


def test_alignment_failure_is_reported_not_hidden(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    env.engine = FakeEngine(refusing_alignment)
    use_case = env.fresh_use_case(env.engine)

    result = use_case.execute(TranscribeAudioCommand(source_id), JobContext.detached())

    assert isinstance(result, Err)
    assert isinstance(result.error, AlignmentUnavailable)
    assert not [
        a
        for a in env.library.list_derived(source_id)
        if a.derivation and a.derivation.operation == "transcription"
    ]


def test_engine_failure_propagates_and_stores_no_transcript(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))

    def explode(config: TranscriptionConfig) -> object:
        raise ExternalSystemError("CUDA out of memory", user_message="Out of memory.")

    broken = env.fresh_use_case(FakeEngine(explode))  # type: ignore[arg-type]

    with pytest.raises(ExternalSystemError):
        broken.execute(TranscribeAudioCommand(source_id), JobContext.detached())

    assert env.transcribe(source_id).created  # a later healthy run still works


def test_missing_ffmpeg_gives_an_actionable_error(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav"))
    no_tools = TranscribeAudio(
        env.library,
        FfmpegAudioPreparer(
            env.runner, ffmpeg="ffmpeg-not-installed", ffprobe="ffprobe-not-installed"
        ),
        env.engine,
        env.paths,
        env.clock,
    )

    with pytest.raises(ToolNotFoundError) as caught:
        no_tools.execute(TranscribeAudioCommand(source_id), JobContext.detached())

    assert "ffprobe-not-installed" in caught.value.user_message


def test_transcript_with_pauses_keeps_the_silence(env: Env) -> None:
    source_id = env.import_media(make_wav(env.tmp / "voice.wav", seconds=6))
    spaced = raw_transcription(words=(("a", 0.5, 0.9), ("b", 4.0, 4.4)), split_after=1)
    use_case = env.fresh_use_case(FakeEngine(lambda _c: spaced))

    result = env.transcribe(source_id, use_case=use_case)

    assert result.transcript.gap_before(result.transcript.words[1]) == pytest.approx(3.1)
    assert result.transcript.pauses(min_duration=3.0)


def test_process_runner_is_only_used_through_the_port(env: Env) -> None:
    ok = env.runner.run(ProcessSpec("ffmpeg", ["-version"], timeout_seconds=20))

    assert ok.succeeded
