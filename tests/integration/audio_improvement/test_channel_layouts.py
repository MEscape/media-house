"""Whatever the channel layout of the source, the improved audio is a clean mono or stereo file."""

from pathlib import Path

import pytest

from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import read_wav
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.errors import Ok
from tests.integration.audio_improvement.conftest import Env
from tests.support.improvement_fakes import save, voice
from tests.support.media_factories import ffmpeg

pytestmark = pytest.mark.integration

#: name -> (output channels expected, pan filter turning the mono voice into that layout)
LAYOUTS = {
    "mono": (1, None),
    "dual_mono": (2, "pan=stereo|c0=c0|c1=c0"),
    "four_with_silent_pair": (2, "pan=4c|c0=c0|c1=c0|c2=0*c0|c3=0*c0"),
    "surround_5_1": (2, "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=0*c0|BL=c0|BR=c0"),
}


def make_source(env: Env, name: str, rate: int = 44_100) -> Path:
    mono = save(env.tmp / f"{name}_mono.wav", voice(8.0, amplitude=0.1), rate)
    layout = LAYOUTS[name][1]
    if layout is None:
        return mono
    target = env.tmp / f"{name}.wav"
    ffmpeg("-i", str(mono), "-af", layout, "-c:a", "pcm_s16le", str(target))
    return target


def delivered(env: Env, asset: MediaAssetDto) -> tuple[int, int]:
    path = env.library.local_path(asset.id)
    assert isinstance(path, Ok)
    samples, rate = read_wav(path.value)
    return samples.shape[1], rate


@pytest.mark.parametrize("name", list(LAYOUTS))
def test_the_output_is_mono_or_stereo_at_the_source_rate(env: Env, name: str) -> None:
    source = env.import_media(make_source(env, name))

    result = env.improve(source)

    channels, rate = delivered(env, result.asset)
    assert (channels, rate) == (LAYOUTS[name][0], 44_100)
    assert result.provenance.channels == channels
    assert result.provenance.sample_rate == 44_100
    assert result.analysis_after.integrated_lufs == pytest.approx(-14.0, abs=0.6)


def test_a_silent_extra_channel_pair_does_not_change_the_loudness_result(env: Env) -> None:
    plain = env.improve(env.import_media(make_source(env, "dual_mono")))
    padded = env.improve(env.import_media(make_source(env, "four_with_silent_pair")))

    assert padded.analysis_after.integrated_lufs == pytest.approx(
        plain.analysis_after.integrated_lufs or 0.0, abs=0.5
    )
