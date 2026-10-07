"""Dereverberation by statistical late-reverberation suppression (spectral subtraction).

The late part of a room's reverberation decays exponentially with the room's RT60. For each
time-frequency cell the engine estimates the late-reverb power from the (smoothed) power
``delay`` seconds earlier, attenuated by that decay, and subtracts a share of it. Early
reflections, which carry speech character, are untouched; the attenuation is capped so the
result cannot turn into a hollow, over-processed voice.

It is a conservative engine, not a neural one: the planner only calls it for clearly
reverberant rooms and the stage guard reverts it unless the measured reverberation time falls.
A better algorithm replaces this class for the same stage.
"""

import math
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy import signal
from scipy.io import wavfile

from media_house.modules.audio_improvement.application.ports import EngineIdentity
from media_house.modules.audio_improvement.domain.values import Parameter, ProcessingStage
from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import read_wav
from media_house.shared.concurrency import CancellationToken

_REVISION = "1"
_WINDOW_SECONDS = 0.032
#: Late reverberation is assumed to start this long after the direct sound.
_LATE_DELAY_SECONDS = 0.05
#: Subtraction share = ``strength * _OVERSUBTRACTION`` (strength 0..1).
_OVERSUBTRACTION = 2.0
#: No time-frequency cell is attenuated by more than this (dB).
_MAX_ATTENUATION_DB = 12.0
_SMOOTHING_FRAMES = 3
_EPSILON = 1e-12

type Floats = NDArray[np.float64]


def suppress_late_reverb(channel: Floats, rate: int, rt60: float, strength: float) -> Floats:
    """One channel with the late reverberation of a room of reverberation time ``rt60`` reduced."""
    nperseg = 2 ** round(math.log2(_WINDOW_SECONDS * rate))
    hop = nperseg // 4
    _, _, spectrum = signal.stft(channel, fs=rate, nperseg=nperseg, noverlap=nperseg - hop)
    power = np.abs(spectrum) ** 2
    smooth = signal.convolve2d(
        power, np.ones((1, _SMOOTHING_FRAMES)) / _SMOOTHING_FRAMES, mode="same", boundary="symm"
    )
    delay = max(1, round(_LATE_DELAY_SECONDS * rate / hop))
    decay = math.exp(-2.0 * 3.0 * math.log(10.0) / rt60 * delay * hop / rate)
    late = np.zeros_like(power)
    late[:, delay:] = decay * smooth[:, :-delay]
    gain = np.sqrt(np.maximum(power - strength * _OVERSUBTRACTION * late, 0.0) / (power + _EPSILON))
    gain = np.maximum(gain, 10 ** (-_MAX_ATTENUATION_DB / 20))
    _, restored = signal.istft(spectrum * gain, fs=rate, nperseg=nperseg, noverlap=nperseg - hop)
    out = np.zeros(len(channel))
    count = min(len(channel), len(restored))
    out[:count] = restored[:count]
    return out


class SpectralDereverb:
    """Structurally implements ``application.ports.StageProcessor`` for dereverberation."""

    stage = ProcessingStage.DEREVERBERATION

    @property
    def identity(self) -> EngineIdentity:
        return EngineIdentity("spectral-late-reverb-subtraction", _REVISION)

    def process(
        self,
        source: Path,
        destination: Path,
        params: Mapping[str, Parameter],
        cancellation: CancellationToken,
    ) -> None:
        samples, rate = read_wav(source)
        strength, rt60 = float(params["strength"]), float(params["rt60"])
        cancellation.raise_if_cancelled()
        channels = [suppress_late_reverb(c, rate, rt60, strength) for c in samples.T]
        wavfile.write(destination, rate, np.stack(channels, axis=1).astype(np.float32))
