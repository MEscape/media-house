"""Synthetic recordings, measurements and scriptable engines for audio improvement tests."""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy import signal

from media_house.modules.audio_improvement.application.ports import EngineIdentity
from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.values import Parameter, ProcessingStage
from media_house.shared.concurrency import CancellationToken
from tests.support.analysis_fakes import harmonic, write_wav

RATE = 48_000
#: One short word every 1.6 s: speech with real pauses (the analyzer needs them to see noise).
_PERIOD, _WORD = 1.6, 0.5

type Floats = NDArray[np.float64]


def voice(seconds: float = 12.0, *, amplitude: float = 0.25, f0: float = 210.0) -> Floats:
    """Voice-like bursts with a gliding pitch and silent pauses between them."""
    return np.array(
        harmonic(
            seconds,
            lambda t: f0 + 25.0 * np.sin(2 * np.pi * 0.7 * t),
            lambda t: amplitude if (t % _PERIOD) < _WORD else 0.0,
            rate=RATE,
        )
    )


def rng() -> np.random.Generator:
    return np.random.default_rng(7)


def with_noise(x: Floats, level_dbfs: float) -> Floats:
    return x + rng().standard_normal(len(x)) * 10 ** (level_dbfs / 20)


def with_hum(x: Floats, amplitude: float = 0.01, hz: float = 50.0) -> Floats:
    return x + amplitude * np.sin(2 * np.pi * hz * np.arange(len(x)) / RATE)


def reverberate(x: Floats, rt60: float) -> Floats:
    """The voice in a room whose tail is as strong as the direct sound (a clearly wet room)."""
    n = int(rt60 * RATE)
    t = np.arange(n) / RATE
    impulse = rng().standard_normal(n) * 10 ** (-3 * t / rt60) * 1.2
    impulse[0] = 1.0
    wet = signal.fftconvolve(x, impulse)[: len(x)]
    return np.asarray(wet / np.abs(wet).max() * np.abs(x).max(), dtype=np.float64)


def with_sibilance(x: Floats, level_db: float = -18.0) -> Floats:
    sos = signal.butter(4, [5_000, 9_000], "bp", fs=RATE, output="sos")
    hiss = signal.sosfilt(sos, rng().standard_normal(len(x)))
    hiss /= hiss.std()
    t = np.arange(len(x)) / RATE
    burst = ((t % _PERIOD) > 0.35) & ((t % _PERIOD) < _WORD)
    return np.asarray(x + hiss * burst * 10 ** (level_db / 20), dtype=np.float64)


def clipped(x: Floats, drive: float = 5.0) -> Floats:
    return np.clip(x * drive, -1.0, 1.0)


def save(path: Path, x: Floats, rate: int = RATE) -> Path:
    return write_wav(path, np.clip(x, -1.0, 1.0).tolist(), rate)


def measurements(**changes: float | None) -> QualityMeasurements:
    """Measurements of clean, well-mastered speech; override what a test is about."""
    base = QualityMeasurements(
        duration=10.0,
        sample_rate=48_000,
        channels=1,
        integrated_lufs=-14.0,
        loudness_range_lu=6.0,
        true_peak_dbtp=-2.0,
        sample_peak_dbfs=-2.0,
        rms_dbfs=-20.0,
        speech_ratio=0.5,
        speech_level_dbfs=-18.0,
        noise_floor_dbfs=-70.0,
        snr_db=52.0,
        hum_hz=None,
        hum_prominence_db=None,
        noise_hf_share=0.1,
        clipping_ratio=0.0,
        crest_factor_db=10.0,
        speech_dynamics_db=28.0,
        sibilance_peak_db=-16.0,
        rumble_db=-34.0,
        mud_db=3.0,
        harshness_db=-18.0,
        reverb_rt60=0.2,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


class ScriptedAnalyzer:
    """Returns prepared measurements by file name (stem), else the default."""

    identity = EngineIdentity("scripted-analyzer", "1")

    def __init__(
        self,
        default: QualityMeasurements,
        by_stem: Mapping[str, QualityMeasurements] | None = None,
    ) -> None:
        self.default = default
        self.by_stem = dict(by_stem or {})
        self.analyzed: list[str] = []

    def analyze(self, audio: Path, cancellation: CancellationToken) -> QualityMeasurements:
        self.analyzed.append(audio.stem)
        return self.by_stem.get(audio.stem, self.default)


class ScriptedEngine:
    """A stage engine that copies its input (or applies ``transform``) and counts its calls."""

    def __init__(
        self,
        stage: ProcessingStage,
        transform: Callable[[Path, Path, Mapping[str, Parameter]], None] | None = None,
    ) -> None:
        self.stage = stage
        self.identity = EngineIdentity(f"scripted-{stage.value}", "1")
        self.calls: list[Mapping[str, Parameter]] = []
        self._transform = transform

    def process(
        self,
        source: Path,
        destination: Path,
        params: Mapping[str, Parameter],
        cancellation: CancellationToken,
    ) -> None:
        self.calls.append(dict(params))
        if self._transform is None:
            shutil.copyfile(source, destination)
        else:
            self._transform(source, destination, params)


def rms_db(x: Floats) -> float:
    return float(10 * np.log10(np.mean(x**2) + 1e-12))
