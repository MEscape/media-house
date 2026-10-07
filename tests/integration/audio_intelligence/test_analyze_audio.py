"""AnalyzeAudio end to end: real library, FFmpeg and acoustic extractor; scripted speech engine.

The speech engine is a ``FakeEngine`` (counts runs); the extractor is the REAL one wrapped in a
counter, so acoustic values come from real measurements of synthetic voices.
"""

import shutil
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.analyze_audio import (
    AnalysisResult,
    AnalyzeAudio,
    AnalyzeAudioCommand,
)
from media_house.modules.audio_intelligence.application.transcribe_audio import TranscribeAudio
from media_house.modules.audio_intelligence.domain.analysis import config as config_module
from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticMeasurements
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AcousticConfig,
    AudioIntelligenceConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.serialization import timeline_from_json
from media_house.modules.audio_intelligence.domain.errors import NoAudioTrack
from media_house.modules.audio_intelligence.domain.raw import RawTranscription
from media_house.modules.audio_intelligence.domain.validation import Severity
from media_house.modules.audio_intelligence.domain.values import TranscriptionConfig
from media_house.modules.audio_intelligence.infrastructure.acoustic_extractor import (
    AcousticExtractor,
)
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.modules.media_library.application.contracts import MediaLibrary, MediaType
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, ValidationError
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.analysis_fakes import harmonic, raw_segments, write_wav
from tests.support.audio_fakes import FakeEngine
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.media_factories import ffmpeg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required",
    ),
]

#: Words and where they are spoken in the synthetic voiceover (prepared-audio time).
SPOKEN = [
    [("Hello,", 0.4, 0.8), ("world.", 0.9, 1.3)],
    [("AMAZING", 2.0, 2.6), ("results", 2.7, 3.2), ("today.", 3.3, 3.7)],
]


def voice(seconds: float = 5.0) -> list[float]:
    """150 Hz voice on every word; 'AMAZING' is louder and rises in pitch."""
    spans = [(s, e) for group in SPOKEN for _, s, e in group]

    def speaking(t: float) -> bool:
        return any(s <= t < e for s, e in spans)

    def f0(t: float) -> float:
        return 150.0 + 90.0 * min(1.0, (t - 2.0) / 0.4) if 2.0 <= t < 2.6 else 150.0

    return harmonic(
        seconds,
        f0,
        lambda t: (0.7 if 2.0 <= t < 2.6 else 0.25) if speaking(t) else 0.0,
    )


def script(config: TranscriptionConfig) -> RawTranscription:
    return raw_segments(SPOKEN, language=config.language or "en")


class CountingExtractor:
    """The real extractor plus call counting, an optional version tag and failure injection."""

    def __init__(self, tag: str = "", failure: Exception | None = None) -> None:
        self.calls = 0
        self.started = threading.Event()
        self._inner = AcousticExtractor()
        self._tag = tag
        self._failure = failure

    def identity(self, config: AcousticConfig) -> dict[str, str]:
        found = self._inner.identity(config)
        return {**found, "pitch": found["pitch"] + self._tag} if self._tag else found

    def measure(
        self,
        audio: Path,
        config: AcousticConfig,
        cancellation: CancellationToken,
    ) -> AcousticMeasurements:
        self.calls += 1
        self.started.set()
        if self._failure is not None:
            raise self._failure
        measured = self._inner.measure(audio, config, cancellation)
        return replace(measured, identity=self.identity(config))


@dataclass
class Env:
    library: MediaLibrary
    engine: FakeEngine
    extractor: CountingExtractor
    use_case: AnalyzeAudio
    paths: AppPaths
    clock: Clock
    runner: ProcessRunner
    tmp: Path

    def import_media(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def voice_asset(self, seconds: float = 5.0) -> str:
        return self.import_media(write_wav(self.tmp / "voice.wav", voice(seconds)))

    def analyze(
        self,
        asset_id: str,
        config: AudioIntelligenceConfig | None = None,
        use_case: AnalyzeAudio | None = None,
    ) -> AnalysisResult:
        command = AnalyzeAudioCommand(asset_id, config or AudioIntelligenceConfig())
        result = (use_case or self.use_case).execute(command, JobContext.detached())
        assert isinstance(result, Ok), result
        return result.value

    def build(self, engine: FakeEngine, extractor: CountingExtractor) -> AnalyzeAudio:
        """A new service over the same library: what an application restart looks like."""
        transcriber = TranscribeAudio(
            self.library,
            FfmpegAudioPreparer(self.runner),
            engine,
            self.paths,
            self.clock,
        )
        return AnalyzeAudio(self.library, transcriber, extractor, self.paths, self.clock)


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
    media = tmp_path / "media"
    media.mkdir()
    holder = Env(
        container.resolve(MediaLibrary),
        FakeEngine(script),
        CountingExtractor(),
        None,  # type: ignore[arg-type]
        paths,
        clock,
        runner,
        media,
    )
    holder.use_case = holder.build(holder.engine, holder.extractor)
    return holder


def operations(env: Env, source_id: str) -> list[str]:
    return sorted(
        a.derivation.operation for a in env.library.list_derived(source_id) if a.derivation
    )


# --- the timeline ------------------------------------------------------------------------------
def test_one_audio_asset_becomes_one_synchronised_timeline(env: Env) -> None:
    source_id = env.voice_asset()

    result = env.analyze(source_id)

    timeline = result.timeline
    assert result.created
    assert [w.raw_word for w in timeline.words] == [
        "Hello,",
        "world.",
        "AMAZING",
        "results",
        "today.",
    ]
    hello = timeline.word_at(0.6)
    assert hello is not None
    assert hello.pitch is not None
    assert hello.pitch.median_hz == pytest.approx(150.0, abs=4.0)
    sample = timeline.acoustic_at(0.6)
    assert sample is not None
    assert sample.voiced
    assert sample.f0 == pytest.approx(150.0, abs=4.0)
    assert timeline.acoustic_at(1.7) is not None
    assert not timeline.acoustic_at(1.7).voiced  # type: ignore[union-attr]  # between sentences
    pause = timeline.pause_after(timeline.words[1])
    assert pause is not None
    assert pause.duration == pytest.approx(0.7)
    assert pause.silence_ratio is not None
    assert pause.silence_ratio > 0.8
    assert timeline.duration == pytest.approx(5.0, abs=0.05)
    assert [i for i in timeline.metadata.warnings if "error" in i] == []
    assert timeline.metadata.analyzers["pitch"].startswith("parselmouth-")


def test_the_loud_rising_word_stands_out_with_its_evidence(env: Env) -> None:
    timeline = env.analyze(env.voice_asset()).timeline

    amazing = timeline.words[2]
    others = [w for w in timeline.words if w.raw_word != "AMAZING"]

    assert amazing.energy is not None
    assert amazing.energy.relative_db is not None
    assert amazing.energy.relative_db > 3.0
    assert amazing.pitch is not None
    assert (amazing.pitch.range_st or 0) > 3.0
    assert (amazing.emphasis_score or 0) > max(w.emphasis_score or 0 for w in others)
    assert (amazing.moment_score or 0) > max(w.moment_score or 0 for w in others)
    assert timeline.moment_at(2.3) is not None
    explanation = timeline.explain_word(amazing)
    assert {"energy_level", "pitch_level"} <= set(explanation["emphasis"])
    rises = timeline.events_between(1.9, 2.8, kinds=["pitch_rise"])
    assert rises
    assert rises[0].strength > 0.5


def test_every_building_block_is_a_normal_library_asset(env: Env) -> None:
    source_id = env.voice_asset()

    result = env.analyze(source_id)

    assert operations(env, source_id) == [
        "acoustic_measurements",
        "audio_extraction",
        "audio_intelligence",
        "transcription",
    ]
    assert (result.asset.media_type, result.asset.mime_type) == (
        MediaType.OTHER,
        "application/json",
    )
    assert result.asset.metadata["transcript_asset_id"] == result.transcript_asset.id
    assert result.asset.metadata["measurements_asset_id"] == result.measurements_asset.id
    assert result.audio_asset.media_type is MediaType.AUDIO  # independently usable audio
    path = env.library.local_path(result.asset.id)
    assert isinstance(path, Ok)
    stored = timeline_from_json(path.value.read_text(encoding="utf-8"))
    assert [w.raw_word for w in stored.words] == [w.raw_word for w in result.timeline.words]
    assert [s.kind for s in stored.editing_signals] == [
        s.kind for s in result.timeline.editing_signals
    ]
    assert not [i for i in result.timeline.metadata.warnings if i.startswith("negative")]


# --- reuse -------------------------------------------------------------------------------------
def test_identical_requests_reuse_everything_also_after_a_restart(env: Env) -> None:
    source_id = env.voice_asset()
    first = env.analyze(source_id)

    again = env.analyze(source_id)
    restarted_engine, restarted_extractor = FakeEngine(script), CountingExtractor()
    after_restart = env.analyze(
        source_id,
        use_case=env.build(restarted_engine, restarted_extractor),
    )

    assert (first.created, again.created, after_restart.created) == (True, False, False)
    assert again.asset.id == after_restart.asset.id == first.asset.id
    assert (env.engine.calls, env.extractor.calls) == (1, 1)
    assert (restarted_engine.calls, restarted_extractor.calls) == (0, 0)
    assert [w.raw_word for w in after_restart.timeline.words] == [
        w.raw_word for w in first.timeline.words
    ]
    assert len(env.library.list_derived(source_id)) == 4


def test_a_damaged_stored_timeline_is_recomputed_from_its_reusable_parts(env: Env) -> None:
    source_id = env.voice_asset()
    first = env.analyze(source_id)
    stored = env.library.local_path(first.asset.id)
    assert isinstance(stored, Ok)
    stored.value.write_text("{not json", encoding="utf-8")

    again = env.analyze(source_id)

    assert again.created
    assert [w.raw_word for w in again.timeline.words] == [w.raw_word for w in first.timeline.words]
    assert (env.engine.calls, env.extractor.calls) == (1, 1)  # only fusion ran again


def test_new_scoring_reuses_transcript_and_measurements(env: Env) -> None:
    source_id = env.voice_asset()
    base = env.analyze(source_id)
    rescored = AudioIntelligenceConfig(
        scoring=ScoringConfig(emphasis_weights={"energy_level": 1.0}),
    )

    new = env.analyze(source_id, rescored)

    assert new.created
    assert new.asset.id != base.asset.id
    assert new.transcript_asset.id == base.transcript_asset.id
    assert new.measurements_asset.id == base.measurements_asset.id
    assert (env.engine.calls, env.extractor.calls) == (1, 1)  # nothing re-run
    emphasis = new.timeline.words[2].signals.emphasis
    assert emphasis is not None
    assert set(emphasis.contributors) == {"energy_level"}


@pytest.mark.parametrize("version", ["MEASUREMENTS_VERSION", "EXTRACTION_VERSION"])
def test_a_bumped_pipeline_version_never_serves_the_stale_timeline(
    env: Env, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    source_id = env.voice_asset()
    base = env.analyze(source_id)

    monkeypatch.setattr(config_module, version, getattr(config_module, version) + 1)
    new = env.analyze(source_id)

    assert new.created
    assert new.asset.id != base.asset.id
    assert new.transcript_asset.id == base.transcript_asset.id  # speech is untouched


def test_a_new_analyzer_version_remeasures_but_keeps_the_transcript(env: Env) -> None:
    source_id = env.voice_asset()
    base = env.analyze(source_id)
    upgraded = CountingExtractor(tag="+v2")

    new = env.analyze(source_id, use_case=env.build(env.engine, upgraded))

    assert new.created
    assert upgraded.calls == 1
    assert env.engine.calls == 1  # no retranscription
    assert new.measurements_asset.id != base.measurements_asset.id
    assert new.transcript_asset.id == base.transcript_asset.id


def test_a_new_speech_model_retranscribes_but_keeps_the_measurements(env: Env) -> None:
    source_id = env.voice_asset()
    base = env.analyze(source_id)

    new = env.analyze(
        source_id,
        AudioIntelligenceConfig(transcription=TranscriptionConfig(model="medium")),
    )

    assert new.created
    assert env.engine.calls == 2
    assert env.extractor.calls == 1  # frames do not depend on the speech model
    assert new.measurements_asset.id == base.measurements_asset.id
    assert new.audio_asset.id == base.audio_asset.id


# --- parallelism, offsets, edge cases ----------------------------------------------------------
def test_speech_recognition_and_acoustic_measurement_run_concurrently(env: Env) -> None:
    source_id = env.voice_asset()
    observed: list[bool] = []

    def waits_for_the_extractor(config: TranscriptionConfig) -> RawTranscription:
        observed.append(env.extractor.started.wait(timeout=20))  # sequential code would time out
        return script(config)

    env.engine = FakeEngine(waits_for_the_extractor)
    env.use_case = env.build(env.engine, env.extractor)

    env.analyze(source_id)

    assert observed == [True]


def test_video_audio_offset_keeps_acoustics_and_words_in_sync(env: Env) -> None:
    video = env.tmp / "delayed.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=160x120:rate=25",
        "-itsoffset",
        "0.5",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=150:duration=3",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        str(video),
    )
    source_id = env.import_media(video)
    env.engine = FakeEngine(
        lambda c: raw_segments([[("tone", 0.5, 1.5)]], language=c.language or "en")
    )
    env.use_case = env.build(env.engine, env.extractor)

    timeline = env.analyze(source_id).timeline

    offset = timeline.metadata.audio_offset
    assert 0.3 < offset < 0.7
    assert timeline.track.origin == pytest.approx(offset)
    word = timeline.words[0]
    assert word.start == pytest.approx(0.5 + offset)
    middle = timeline.acoustic_at((word.start + word.end) / 2)
    assert middle is not None
    assert middle.voiced  # the tone IS there at the word's SOURCE time
    assert middle.f0 == pytest.approx(150.0, abs=5.0)
    assert timeline.acoustic_at(offset * 0.4) is None  # before the audio starts: no frame


def test_audio_without_speech_gives_a_valid_empty_timeline(env: Env) -> None:
    source_id = env.import_media(write_wav(env.tmp / "silence.wav", [0.0] * 48_000))
    env.engine = FakeEngine(lambda c: raw_segments([], language=c.language or "en"))
    env.use_case = env.build(env.engine, env.extractor)

    timeline = env.analyze(source_id).timeline

    assert timeline.words == ()
    assert timeline.baseline.pitch_median_hz is None
    assert timeline.editing_signals == ()
    assert any("no_words" in w for w in timeline.metadata.warnings)
    assert not [w for w in timeline.metadata.warnings if "error" in w.lower()]
    assert timeline.events_between(0.0, 3.0)  # the silence itself is an event


def test_a_failing_analyzer_stores_no_timeline_and_progress_is_kept(env: Env) -> None:
    source_id = env.voice_asset()
    broken = CountingExtractor(failure=ExternalSystemError("praat crashed", user_message="x"))

    env.use_case = env.build(env.engine, broken)
    with pytest.raises(ExternalSystemError, match="praat"):
        env.analyze(source_id)

    assert "audio_intelligence" not in operations(env, source_id)
    healthy = env.analyze(source_id, use_case=env.build(env.engine, env.extractor))
    assert healthy.created
    assert env.engine.calls == 1  # the finished transcript was reused, not redone


def test_expected_failures_are_results(env: Env) -> None:
    silent_video = env.tmp / "silent.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=64x64:rate=10",
        "-c:v",
        "mpeg4",
        str(silent_video),
    )
    png = env.tmp / "pic.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1", str(png))

    no_audio = env.use_case.execute(
        AnalyzeAudioCommand(env.import_media(silent_video)),
        JobContext.detached(),
    )
    image = env.use_case.execute(AnalyzeAudioCommand(env.import_media(png)), JobContext.detached())
    missing = env.use_case.execute(AnalyzeAudioCommand("missing"), JobContext.detached())

    assert isinstance(no_audio, Err)
    assert isinstance(no_audio.error, NoAudioTrack)
    assert isinstance(image, Err)
    assert isinstance(image.error, ValidationError)
    assert isinstance(missing, Err)
    assert env.extractor.calls == 0


def test_validation_findings_are_kept_but_never_hidden(env: Env) -> None:
    timeline = env.analyze(env.voice_asset()).timeline

    from media_house.modules.audio_intelligence.domain.analysis.validation import validate_timeline

    errors = [i for i in validate_timeline(timeline) if i.severity is Severity.ERROR]
    assert errors == []
