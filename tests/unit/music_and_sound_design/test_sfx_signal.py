import numpy as np

from media_house.modules.music_and_sound_design.domain.analysis import Loudness
from media_house.modules.music_and_sound_design.infrastructure.ffmpeg_audio import parse_loudness
from media_house.modules.music_and_sound_design.infrastructure.sfx_signal import (
    SAMPLE_RATE,
    measure_sfx_signal,
)

SR = SAMPLE_RATE
_GATED = Loudness(integrated_lufs=None, loudness_range_lu=None, true_peak_dbtp=-1.0)


def test_an_impact_has_a_sharp_attack_a_measured_tail_and_skips_leading_silence() -> None:
    rng = np.random.default_rng(3)
    t = np.arange(int(0.5 * SR)) / SR
    burst = rng.standard_normal(len(t)) * np.exp(-t * 15) * 0.5
    x = np.concatenate([np.zeros(int(0.1 * SR)), burst, np.zeros(int(0.2 * SR))])

    m = measure_sfx_signal(x, loudness=_GATED)

    assert abs(m.onset_sample / SR - 0.1) < 0.006
    assert m.useful_start_sample < m.onset_sample
    assert m.attack_seconds <= 0.01
    assert 0.2 < m.tail_seconds < 0.5
    assert m.useful_end_sample <= int(0.62 * SR)
    assert m.brightness > 0.3  # white noise: most energy above 4 kHz
    assert m.rms_max_dbfs < m.peak_dbfs
    assert "integrated_loudness" in {u.what for u in m.not_available}


def test_a_riser_has_a_slow_attack_and_low_centroid() -> None:
    t = np.arange(int(1.5 * SR)) / SR
    x = np.sin(2 * np.pi * 300 * t) * (t / t[-1]) ** 2 * 0.5

    m = measure_sfx_signal(x, loudness=_GATED)

    assert m.attack_seconds > 0.5
    assert m.peak_sample / SR > 1.3
    assert m.spectral_centroid_hz < 1000


def test_a_silent_file_is_reported_not_profiled() -> None:
    m = measure_sfx_signal(np.zeros(SR // 2), loudness=_GATED)

    assert m.warnings == ("the file is silent",)
    assert "timing" in {u.what for u in m.not_available}


def test_ebur128_summary_is_parsed_and_the_gate_floor_is_not_a_value() -> None:
    summary = """Summary:
  Integrated loudness:
    I:         -14.2 LUFS
    Threshold: -24.5 LUFS
  Loudness range:
    LRA:         5.1 LU
  True peak:
    Peak:        0.4 dBFS"""
    gated = summary.replace("-14.2", "-70.0")

    assert parse_loudness(summary) == Loudness(
        integrated_lufs=-14.2, loudness_range_lu=5.1, true_peak_dbtp=0.4
    )
    assert parse_loudness(gated).integrated_lufs is None
    assert parse_loudness(gated).true_peak_dbtp == 0.4
