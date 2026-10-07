"""MixAudio end to end: voice + music, ducking, mastering after the mix, sources untouched."""

import numpy as np
import pytest

from media_house.modules.audio_improvement.application.mix_audio import (
    MixAudio,
    MixAudioCommand,
)
from media_house.modules.audio_improvement.domain.values import ProcessingStage as Stage
from media_house.modules.audio_improvement.domain.values import StageStatus
from media_house.modules.audio_improvement.infrastructure.ffmpeg_mixer import FfmpegMixer
from media_house.modules.audio_improvement.infrastructure.ffmpeg_stages import FfmpegMastering
from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import (
    FfmpegTranscoder,
    read_wav,
)
from media_house.modules.audio_improvement.infrastructure.quality_analyzer import (
    SignalQualityAnalyzer,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.integration.audio_improvement.conftest import Env
from tests.support.improvement_fakes import RATE, save, voice
from tests.support.media_factories import ffmpeg

pytestmark = pytest.mark.integration

MUSIC_HZ = 5_000.0  # well above the voice's partials: its energy in a band is the music's


def mixer(env: Env) -> MixAudio:
    return MixAudio(
        env.library,
        FfmpegTranscoder(env.tool),
        SignalQualityAnalyzer(env.tool),
        FfmpegMixer(env.tool),
        FfmpegMastering(env.tool),
        env.paths,
    )


def band_db(samples: np.ndarray, start: float, end: float) -> float:
    segment = samples[int(start * RATE) : int(end * RATE)]
    spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment)))) ** 2
    freqs = np.fft.rfftfreq(len(segment), 1 / RATE)
    return float(10 * np.log10(spectrum[(freqs > MUSIC_HZ - 300) & (freqs < MUSIC_HZ + 300)].sum()))


@pytest.fixture
def sources(env: Env) -> tuple[str, str]:
    speech = voice(12.0)
    speech[6 * RATE :] = 0.0  # speaks for six seconds, then pauses
    voice_id = env.import_media(save(env.tmp / "voice.wav", speech))
    t = np.arange(int(3.0 * RATE)) / RATE
    music_id = env.import_media(save(env.tmp / "music.wav", 0.3 * np.sin(2 * np.pi * MUSIC_HZ * t)))
    return voice_id, music_id


def mix(env: Env, voice_id: str, music_id: str, **overrides: float) -> object:
    result = mixer(env).execute(
        MixAudioCommand(voice_id, music_id, overrides=overrides), JobContext.detached()
    )
    assert isinstance(result, Ok), result
    return result.value


def test_voice_and_music_become_one_mastered_track(env: Env, sources: tuple[str, str]) -> None:
    voice_id, music_id = sources
    voice_before, music_before = env.library.get(voice_id), env.library.get(music_id)

    result = mix(env, voice_id, music_id)

    assert result.asset.derivation.source_asset_id == voice_id  # type: ignore[attr-defined]
    assert result.asset.metadata["music_asset_id"] == music_id  # type: ignore[attr-defined]
    assert result.analysis_after.duration == pytest.approx(12.0, abs=0.01)  # type: ignore[attr-defined]  # the voice's length
    assert [r.stage for r in result.provenance.stages] == [Stage.MIXING, Stage.MASTERING]  # type: ignore[attr-defined]
    assert result.provenance.stages[0].status is StageStatus.APPLIED  # type: ignore[attr-defined]
    assert not [c for c in result.checks if not c.passed]  # type: ignore[attr-defined]
    assert result.analysis_after.integrated_lufs == pytest.approx(-14.0, abs=0.6)  # type: ignore[attr-defined]
    assert (env.library.get(voice_id), env.library.get(music_id)) == (voice_before, music_before)


def test_the_music_sits_below_the_voice_and_ducks_while_it_speaks(
    env: Env, sources: tuple[str, str]
) -> None:
    voice_id, music_id = sources
    ducked = mix(env, voice_id, music_id)
    plain = mix(env, voice_id, music_id, **{"mix.duck_ratio": 1.0})  # ratio 1 = no ducking

    def speech_to_pause(result: object) -> float:
        path = env.library.local_path(result.asset.id)  # type: ignore[attr-defined]
        assert isinstance(path, Ok)
        samples, _ = read_wav(path.value)
        # inside the spoken words (they start every 1.6 s and last 0.5 s) vs the silent second half
        words = [band_db(samples[:, 0], 1.6 * k + 0.15, 1.6 * k + 0.45) for k in (1, 2, 3)]
        return float(np.mean(words)) - band_db(samples[:, 0], 7.0, 10.0)

    assert (
        speech_to_pause(ducked) < speech_to_pause(plain) - 6.0
    )  # the music gives way to the voice
    assert ducked.asset.id != plain.asset.id  # type: ignore[attr-defined]


def test_an_identical_mix_is_reused(env: Env, sources: tuple[str, str]) -> None:
    first = mix(env, *sources)

    again = mix(env, *sources)

    assert (first.created, again.created) == (True, False)  # type: ignore[attr-defined]
    assert again.asset.id == first.asset.id  # type: ignore[attr-defined]
    assert again.provenance == first.provenance  # type: ignore[attr-defined]


def test_only_audio_can_be_mixed(env: Env, sources: tuple[str, str]) -> None:
    picture = env.tmp / "pic.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1", str(picture))

    result = mixer(env).execute(
        MixAudioCommand(sources[0], env.import_media(picture)), JobContext.detached()
    )

    assert isinstance(result, Err)
