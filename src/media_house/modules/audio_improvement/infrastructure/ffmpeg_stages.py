"""Stage engines built from FFmpeg filters. Each maps engine-neutral parameters to one filter graph.

A stage engine knows HOW (filter names, units) and nothing about WHEN or HOW MUCH: planners
decide that. Replacing an engine (a neural denoiser, another de-esser) means writing a class like
these for the same stage and registering it in ``module.py``; revision strings are part of the
processing identity, so changing a mapping here retires results made with the old one.
"""

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from media_house.modules.audio_improvement.application.ports import EngineIdentity
from media_house.modules.audio_improvement.domain.values import Parameter, ProcessingStage
from media_house.modules.audio_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.shared.concurrency import CancellationToken

#: Mains harmonics notched out when hum is reported (all sit below the Nyquist of 16 kHz audio).
_HUM_HARMONICS = 3
_HUM_NOTCH_Q = 30
#: afftdn reduction range (dB): strength 0..1 maps linearly onto it.
_NR_MIN_DB, _NR_MAX_DB = 6.0, 30.0
#: Fixed processing delay of afftdn (measured at 8-96 kHz: 25 ms at every sample rate).
_AFFTDN_LATENCY_SECONDS = 0.025
#: afftdn accepts a noise floor in this range (dB).
_NF_LIMITS = (-80.0, -20.0)
#: acompressor accepts a linear threshold in this range.
_THRESHOLD_LIMITS = (0.000976563, 1.0)
#: alimiter accepts a linear ceiling in this range.
_LIMIT_LIMITS = (0.0625, 1.0)
_LIMITER_ATTACK_MS = 5.0


def _number(params: Mapping[str, Parameter], key: str) -> float:
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"parameter {key!r} must be a number, got {value!r}")
    return float(value)


def _linear(db: float, limits: tuple[float, float]) -> float:
    return min(limits[1], max(limits[0], 10 ** (db / 20)))


class FfmpegFilterStage(ABC):
    """Base of the FFmpeg engines: one stage, one filter graph, float WAV in and out."""

    stage: ClassVar[ProcessingStage]
    #: Short engine name and the revision of its parameter mapping.
    engine: ClassVar[str]
    revision: ClassVar[str] = "1"

    def __init__(self, tool: FfmpegTool) -> None:
        self._tool = tool

    @property
    def identity(self) -> EngineIdentity:
        return EngineIdentity(self.engine, f"{self._tool.version}+r{self.revision}")

    @abstractmethod
    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str: ...

    def process(
        self,
        source: Path,
        destination: Path,
        params: Mapping[str, Parameter],
        cancellation: CancellationToken,
    ) -> None:
        rate = self._tool.probe_audio(source, cancellation).sample_rate
        self._tool.filter_audio(source, destination, self.graph(params, rate), cancellation)


class FfmpegDeclipper(FfmpegFilterStage):
    stage = ProcessingStage.CLIPPING_REPAIR
    engine = "ffmpeg-adeclip"

    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str:
        return "adeclip"


class FfmpegNoiseReducer(FfmpegFilterStage):
    """Broadband spectral denoising (``afftdn``) and notch filters for mains hum."""

    stage = ProcessingStage.NOISE_REDUCTION
    engine = "ffmpeg-afftdn"

    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str:
        parts: list[str] = []
        if "hum_hz" in params:
            hum = _number(params, "hum_hz")
            parts += [
                f"bandreject=f={hum * k:g}:width_type=q:w={_HUM_NOTCH_Q}"
                for k in range(1, _HUM_HARMONICS + 1)
            ]
        if params.get("broadband"):
            reduction = _NR_MIN_DB + (_NR_MAX_DB - _NR_MIN_DB) * _number(params, "strength")
            # afftdn delays its output by a fixed time and would cut the end of the audio: feed it
            # that much silence and drop the delayed start, so every sample stays in place.
            delay = int(_AFFTDN_LATENCY_SECONDS * sample_rate)
            parts += [
                f"apad=pad_len={delay}",
                f"afftdn=nr={reduction:.1f}:{self._noise_model(params)}",
                f"atrim=start_sample={delay}",
            ]
        return ",".join(parts) or "anull"

    @staticmethod
    def _noise_model(params: Mapping[str, Parameter]) -> str:
        """The measured noise floor as a fixed model (deterministic); tracking only as a fallback.

        With the floor measured by the analyzer, ``nr`` dB of reduction yields about two thirds of
        that as SNR gain at every noise level (measured on synthetic recordings).
        """
        if "noise_floor_dbfs" in params:
            floor = min(_NF_LIMITS[1], max(_NF_LIMITS[0], _number(params, "noise_floor_dbfs")))
            return f"nf={floor:.1f}:tn=0"
        return "nf=-50:tn=1:tr=1"


class FfmpegEqualizer(FfmpegFilterStage):
    stage = ProcessingStage.EQUALIZATION
    engine = "ffmpeg-equalizer"

    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str:
        parts: list[str] = []
        if "highpass_hz" in params:
            parts.append(f"highpass=f={_number(params, 'highpass_hz'):g}")
        for band in ("mud", "harshness"):
            if f"{band}_gain_db" in params:
                centre, gain = (
                    _number(params, f"{band}_center_hz"),
                    _number(params, f"{band}_gain_db"),
                )
                parts.append(f"equalizer=f={centre:g}:t=q:w=1:g={gain:.2f}")
        return ",".join(parts) or "anull"


class FfmpegCompressor(FfmpegFilterStage):
    """Gentle feed-forward compression; make-up gain is left to mastering."""

    stage = ProcessingStage.DYNAMICS
    engine = "ffmpeg-acompressor"

    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str:
        threshold = _linear(_number(params, "threshold_dbfs"), _THRESHOLD_LIMITS)
        return (
            f"acompressor=threshold={threshold:.6f}:ratio={_number(params, 'ratio'):g}"
            f":attack={_number(params, 'attack_ms'):g}"
            f":release={_number(params, 'release_ms'):g}:makeup=1"
        )


class FfmpegDeEsser(FfmpegFilterStage):
    stage = ProcessingStage.DE_ESSING
    engine = "ffmpeg-deesser"

    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str:
        return (
            f"deesser=i={_number(params, 'intensity'):.2f}"
            f":m={_number(params, 'max_reduction'):.2f}:f=0.5:s=o"
        )


class FfmpegMastering(FfmpegFilterStage):
    """Gain to the loudness target followed by a look-ahead limiter at the peak ceiling.

    ``alimiter`` limits sample peaks; the mastering loop re-measures the true peak and lowers the
    ceiling if the limiter overshoots, so the verified result honours the true-peak target.
    ``latency=1`` keeps the output sample-aligned with the input.
    """

    stage = ProcessingStage.MASTERING
    engine = "ffmpeg-gain-limiter"

    def graph(self, params: Mapping[str, Parameter], sample_rate: int) -> str:
        gain, ceiling = _number(params, "gain_db"), _number(params, "ceiling_dbtp")
        limit = _linear(ceiling, _LIMIT_LIMITS)
        if not math.isfinite(gain):
            raise ValueError("mastering gain must be finite")
        return (
            f"volume={gain:.3f}dB,alimiter=limit={limit:.5f}:attack={_LIMITER_ATTACK_MS:g}"
            f":release={_number(params, 'release_ms'):g}:level=disabled:latency=1"
        )
