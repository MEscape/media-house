"""The speaker stage on a real library: opt-in, separate from transcription, cached on its own.

The diarizer is a fake (no model is needed or downloaded); the speech engine counts its runs, which
is how these tests prove that asking for speakers never transcribes or measures again.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from media_house.modules.audio_intelligence.application.analyze_audio import (
    AnalyzeAudio,
    AnalyzeAudioCommand,
)
from media_house.modules.audio_intelligence.application.transcribe_audio import TranscribeAudio
from media_house.modules.audio_intelligence.domain.analysis.config import AudioIntelligenceConfig
from media_house.modules.audio_intelligence.domain.diarization import (
    DiarizationConfig,
    SpeakerTurn,
)
from media_house.modules.audio_intelligence.domain.errors import DiarizationUnavailable
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok
from tests.integration.audio_intelligence.test_analyze_audio import (  # noqa: F401
    Env,
    env,
    operations,
)

pytestmark = pytest.mark.integration

SPEAKERS = (SpeakerTurn(0.0, 1.5, "S1"), SpeakerTurn(1.8, 4.0, "S2"))


@dataclass
class FakeDiarizer:
    why: str | None = None
    version: str = "1"
    calls: int = 0
    turns: tuple[SpeakerTurn, ...] = SPEAKERS
    seen: list[DiarizationConfig] = field(default_factory=list)

    def identity(self, config: DiarizationConfig) -> dict[str, str]:
        return {"engine": "fake", "version": self.version, "model": config.model}

    def unavailable(self, config: DiarizationConfig) -> str | None:
        return self.why

    def diarize(
        self, audio: Path, config: DiarizationConfig, cancellation: CancellationToken
    ) -> tuple[SpeakerTurn, ...]:
        self.calls += 1
        self.seen.append(config)
        return self.turns


def with_speakers(host: Env, diarizer: FakeDiarizer | None) -> AnalyzeAudio:
    transcriber = TranscribeAudio(
        host.library, FfmpegAudioPreparer(host.runner), host.engine, host.paths, host.clock
    )
    return AnalyzeAudio(
        host.library, transcriber, host.extractor, host.paths, host.clock, diarizer=diarizer
    )


def config(**changes: object) -> AudioIntelligenceConfig:
    return AudioIntelligenceConfig(diarization=DiarizationConfig(**changes))  # type: ignore[arg-type]


def test_without_the_option_nothing_changes_and_nothing_is_asked(env: Env) -> None:  # noqa: F811
    diarizer = FakeDiarizer()
    source = env.voice_asset()

    result = env.analyze(source, use_case=with_speakers(env, diarizer))

    assert diarizer.calls == 0
    assert all(w.word.speaker is None for w in result.timeline.words)
    assert "diarization" not in operations(env, source)


def test_asking_for_speakers_labels_the_words_without_repeating_any_other_work(env: Env) -> None:  # noqa: F811
    diarizer = FakeDiarizer()
    use_case = with_speakers(env, diarizer)
    source = env.voice_asset()
    plain = env.analyze(source, use_case=use_case)
    assert (env.engine.calls, env.extractor.calls) == (1, 1)

    labelled = env.analyze(source, config(), use_case)

    assert (env.engine.calls, env.extractor.calls, diarizer.calls) == (1, 1, 1)
    assert labelled.transcript_asset.id == plain.transcript_asset.id  # same transcript
    assert labelled.measurements_asset.id == plain.measurements_asset.id
    assert labelled.asset.id != plain.asset.id  # a different timeline
    assert [w.word.speaker for w in labelled.timeline.words] == ["S1", "S1", "S2", "S2", "S2"]
    assert [w.word.raw_word for w in labelled.timeline.words] == [
        w.word.raw_word for w in plain.timeline.words
    ]
    assert "diarization" in operations(env, source)
    assert labelled.asset.metadata["diarization_asset_id"]


def test_the_same_request_again_computes_nothing(env: Env) -> None:  # noqa: F811
    diarizer = FakeDiarizer()
    use_case = with_speakers(env, diarizer)
    source = env.voice_asset()
    first = env.analyze(source, config(), use_case)

    again = env.analyze(source, config(), use_case)

    assert not again.created and again.asset.id == first.asset.id
    assert (env.engine.calls, env.extractor.calls, diarizer.calls) == (1, 1, 1)


def test_another_speaker_count_recomputes_only_the_turns_and_the_timeline(env: Env) -> None:  # noqa: F811
    diarizer = FakeDiarizer()
    use_case = with_speakers(env, diarizer)
    source = env.voice_asset()
    env.analyze(source, config(), use_case)

    env.analyze(source, config(num_speakers=2), use_case)

    assert (env.engine.calls, env.extractor.calls, diarizer.calls) == (1, 1, 2)


def test_speed_and_access_settings_do_not_cause_a_new_run(env: Env) -> None:  # noqa: F811
    diarizer = FakeDiarizer()
    use_case = with_speakers(env, diarizer)
    source = env.voice_asset()
    env.analyze(source, config(), use_case)

    again = env.analyze(source, config(device="cpu", allow_model_download=True), use_case)

    assert not again.created and diarizer.calls == 1


def test_a_new_engine_version_recomputes_the_turns_but_not_the_speech(env: Env) -> None:  # noqa: F811
    source = env.voice_asset()
    env.analyze(source, config(), with_speakers(env, FakeDiarizer(version="1")))
    newer = FakeDiarizer(version="2")

    env.analyze(source, config(), with_speakers(env, newer))

    assert newer.calls == 1 and env.engine.calls == 1


def test_an_unavailable_engine_fails_before_any_expensive_work(env: Env) -> None:  # noqa: F811
    source = env.voice_asset()
    broken = FakeDiarizer(why="the model is not on disk. Run: fetch --diarization")

    result = with_speakers(env, broken).execute(
        AnalyzeAudioCommand(source, config()), JobContext.detached()
    )

    assert isinstance(result, Err) and isinstance(result.error, DiarizationUnavailable)
    assert "fetch --diarization" in result.error.user_message
    assert env.engine.calls == 0 and env.extractor.calls == 0
    assert operations(env, source) == []  # not even the audio extraction ran


def test_stored_turns_need_no_engine(env: Env) -> None:  # noqa: F811
    source = env.voice_asset()
    env.analyze(source, config(), with_speakers(env, FakeDiarizer()))
    # a fresh timeline identity (scoring tweaked) but the same turns, engine now unavailable
    offline = FakeDiarizer(why="offline")
    cfg = config()
    from dataclasses import replace

    cfg = replace(cfg, analysis=replace(cfg.analysis, min_pause=cfg.analysis.min_pause + 0.01))

    result = with_speakers(env, offline).execute(
        AnalyzeAudioCommand(source, cfg), JobContext.detached()
    )

    assert isinstance(result, Ok)
    assert offline.calls == 0
    assert all(w.word.speaker for w in result.value.timeline.words[:4])


def test_no_engine_at_all_is_reported_not_ignored(env: Env) -> None:  # noqa: F811
    source = env.voice_asset()

    result = with_speakers(env, None).execute(
        AnalyzeAudioCommand(source, config()), JobContext.detached()
    )

    assert isinstance(result, Err) and isinstance(result.error, DiarizationUnavailable)
