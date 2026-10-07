"""Acoustic measurement: pitch (Praat), energy, loudness, speech activity, pluggable events.

The prepared audio is decoded ONCE into a shared float array; independent analyzers then run
concurrently on it (NumPy/SciPy/Praat release the GIL for the heavy work). Everything is
measured on a common frame grid (``hop``), in prepared-audio time; the use case maps it onto the
source timeline. Missing measurements stay NaN (``null`` in JSON): an unvoiced frame has no pitch.
"""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from scipy import signal as dsp

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    SILENCE_FLOOR_DB,
    AcousticMeasurements,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.config import AcousticConfig
from media_house.modules.audio_intelligence.domain.analysis.serialization import FRAME_PRECISION
from media_house.modules.audio_intelligence.infrastructure.wav_io import read_mono_pcm16
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type Floats = NDArray[np.float64]

# Algorithm constants of the analyzers below: not user settings. Changing one changes the measured
# values, so it needs a bump of the analyzer's version string (pinned by a unit test).
ENERGY_VERSION = "rms-1"
LOUDNESS_VERSION = "bs1770-momentary-1"
ACTIVITY_VERSION = "adaptive-1"
_EPSILON = 1e-12
_MIN_PITCH_SECONDS = 0.2
_FIRST_PASS_RANGE = (60.0, 700.0)
_MIN_RANGE_FRAMES = 20
_MIN_DYNAMIC_DB = 6.0
_ABSOLUTE_VOICED_FLOOR_DB = -60.0
_MAX_GAP_FRAMES = 3
_MIN_RUN_FRAMES = 3
#: Voiced frames count as speech this far above the noise floor (a lower bar than pure energy).
_VOICED_ACTIVITY_MARGIN_DB = 3.0


@dataclass(frozen=True, slots=True)
class AudioSignal:
    """The shared decoded audio every analyzer reads (never modified)."""

    samples: NDArray[np.float32]
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


@dataclass(frozen=True, slots=True)
class PitchTrack:
    f0: Floats
    confidence: Floats
    parameters: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class PitchAnalyzer(Protocol):
    """Replaceable F0 tracker. Output is on the frame grid: ``frames`` values, NaN = unvoiced."""

    @property
    def identity(self) -> str: ...

    def analyze(self, signal: AudioSignal, config: AcousticConfig, frames: int) -> PitchTrack: ...


class AudioEventDetector(Protocol):
    """Pluggable non-verbal event detector (laughter, breath, music, ...). None ship by default."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def detect(self, signal: AudioSignal) -> list[AudioEvent]:
        """Events in seconds from the start of ``signal``. Never invent confidence."""
        ...


class ParselmouthPitchAnalyzer:
    """Praat's autocorrelation pitch tracker with Hirst-style two-pass range estimation.

    Pass one tracks a wide range; the speaker's 25th/75th percentile then set the real range
    (``0.75*q1`` .. ``2.5*q3``) for the final pass, which removes most octave errors. Explicit
    ``pitch_floor``/``pitch_ceiling`` in the config skip pass one.
    """

    @property
    def identity(self) -> str:
        import parselmouth

        return f"parselmouth-{parselmouth.__version__}/praat-{parselmouth.PRAAT_VERSION}"

    def analyze(self, signal: AudioSignal, config: AcousticConfig, frames: int) -> PitchTrack:
        nothing = PitchTrack(np.full(frames, math.nan), np.full(frames, math.nan))
        if signal.duration < _MIN_PITCH_SECONDS or frames == 0:
            return PitchTrack(
                nothing.f0,
                nothing.confidence,
                warnings=("audio too short for pitch analysis",),
            )
        warnings: list[str] = []
        if config.pitch_floor is not None and config.pitch_ceiling is not None:
            floor, ceiling = config.pitch_floor, config.pitch_ceiling
        else:
            first, _ = self._track(signal, config, *_FIRST_PASS_RANGE, frames)
            voiced = first[np.isfinite(first)]
            if len(voiced) >= _MIN_RANGE_FRAMES:
                q1, q3 = np.percentile(voiced, [25, 75])
                floor = config.pitch_floor or max(30.0, 0.75 * float(q1))
                ceiling = config.pitch_ceiling or min(1200.0, 2.5 * float(q3))
            else:
                floor, ceiling = (
                    config.pitch_floor or _FIRST_PASS_RANGE[0],
                    (config.pitch_ceiling or _FIRST_PASS_RANGE[1]),
                )
                warnings.append("too little voiced speech to estimate the speaker's pitch range")
        f0, confidence = self._track(signal, config, floor, ceiling, frames)
        return PitchTrack(
            f0,
            confidence,
            parameters={"pitch_floor": float(floor), "pitch_ceiling": float(ceiling)},
            warnings=tuple(warnings),
        )

    def _track(
        self,
        signal: AudioSignal,
        config: AcousticConfig,
        floor: float,
        ceiling: float,
        frames: int,
    ) -> tuple[Floats, Floats]:
        import parselmouth

        try:
            sound = parselmouth.Sound(signal.samples.astype(np.float64), signal.sample_rate)
            pitch = sound.to_pitch_ac(
                time_step=config.hop,
                pitch_floor=floor,
                pitch_ceiling=ceiling,
                voicing_threshold=config.voicing_threshold,
            )
        except parselmouth.PraatError as exc:
            raise ExternalSystemError(
                f"Praat pitch analysis failed: {exc}",
                user_message="Pitch analysis failed for this audio.",
            ) from exc
        selected = pitch.selected_array
        frequency = np.asarray(selected["frequency"], dtype=np.float64)
        strength = np.clip(np.asarray(selected["strength"], dtype=np.float64), 0.0, 1.0)
        # Praat's frame times are not on our grid (and may sit exactly between two cells), so
        # take for every grid centre the NEAREST pitch frame, if one is within 3/4 of a hop.
        xs = np.asarray(pitch.xs(), dtype=np.float64)
        centres = (np.arange(frames) + 0.5) * config.hop
        upper = np.clip(np.searchsorted(xs, centres), 1, max(1, len(xs) - 1))
        lower = upper - 1
        nearest = np.where(np.abs(xs[lower] - centres) <= np.abs(xs[upper] - centres), lower, upper)
        close = np.abs(xs[nearest] - centres) <= 0.75 * config.hop
        f0 = np.where(close & (frequency[nearest] > 0), frequency[nearest], math.nan)
        confidence = np.where(close, strength[nearest], math.nan)
        return f0, confidence


# --- energy and loudness -------------------------------------------------------------------------
def _k_weighting(rate: int) -> list[tuple[Floats, Floats]]:
    """ITU-R BS.1770 K-weighting as two biquads (shelf + high-pass) designed for ``rate``."""

    def shelf(f0: float, gain_db: float, q: float) -> tuple[Floats, Floats]:
        a = 10 ** (gain_db / 40)
        w0 = 2 * math.pi * f0 / rate
        alpha = math.sin(w0) / (2 * q)
        cos = math.cos(w0)
        root = 2 * math.sqrt(a) * alpha
        b = np.array(
            [
                a * ((a + 1) + (a - 1) * cos + root),
                -2 * a * ((a - 1) + (a + 1) * cos),
                a * ((a + 1) + (a - 1) * cos - root),
            ],
        )
        denominator = np.array(
            [
                (a + 1) - (a - 1) * cos + root,
                2 * ((a - 1) - (a + 1) * cos),
                (a + 1) - (a - 1) * cos - root,
            ],
        )
        return b / denominator[0], denominator / denominator[0]

    def high_pass(f0: float, q: float) -> tuple[Floats, Floats]:
        w0 = 2 * math.pi * f0 / rate
        alpha = math.sin(w0) / (2 * q)
        cos = math.cos(w0)
        b = np.array([(1 + cos) / 2, -(1 + cos), (1 + cos) / 2])
        denominator = np.array([1 + alpha, -2 * cos, 1 - alpha])
        return b / denominator[0], denominator / denominator[0]

    return [
        shelf(1681.974450955533, 3.999843853973347, 0.7071752369554196),
        high_pass(38.13547087602444, 0.5003270373238773),
    ]


def _windowed_mean_square(power: Floats, centres: Floats, half_window: int) -> Floats:
    """Mean of ``power`` over ``centre +/- half_window`` samples (clipped at the edges)."""
    cumulative = np.concatenate(([0.0], np.cumsum(power)))
    lo = np.clip(centres - half_window, 0, len(power)).astype(int)
    hi = np.clip(centres + half_window, 0, len(power)).astype(int)
    return np.asarray((cumulative[hi] - cumulative[lo]) / np.maximum(hi - lo, 1), dtype=np.float64)


def measure_energy(
    signal: AudioSignal, config: AcousticConfig, frames: int
) -> tuple[Floats, Floats]:
    """Per-frame RMS in dBFS and momentary K-weighted loudness (LUFS-style, ungated)."""
    samples = signal.samples.astype(np.float64)
    hop = max(1, round(config.hop * signal.sample_rate))
    centres = ((np.arange(frames) + 0.5) * hop).astype(int)
    rms_ms = _windowed_mean_square(samples**2, centres, hop)
    rms_db = np.maximum(10 * np.log10(rms_ms + _EPSILON), SILENCE_FLOOR_DB)

    weighted = samples
    for b, a in _k_weighting(signal.sample_rate):
        weighted = dsp.lfilter(b, a, weighted)
    half = max(1, round(config.loudness_window * signal.sample_rate / 2))
    loudness_ms = _windowed_mean_square(weighted**2, centres, half)
    loudness = np.maximum(-0.691 + 10 * np.log10(loudness_ms + _EPSILON), SILENCE_FLOOR_DB)
    return rms_db, loudness


def measure_activity(
    rms_db: Floats,
    f0: Floats,
    confidence: Floats,
    config: AcousticConfig,
) -> Floats:
    """Speech activity: energy well above the noise floor, or clearly voiced; gaps/specks smoothed.

    Audio without enough dynamic range (silence, constant hum) has no activity at all.
    """
    if len(rms_db) == 0:
        return np.zeros(0)
    floor, peak = np.percentile(rms_db, [10, 95])
    voiced = np.isfinite(f0) & (np.nan_to_num(confidence) >= config.voicing_threshold)
    if peak - floor < _MIN_DYNAMIC_DB:
        # No level contrast to separate speech from background: only clear voicing counts.
        active = voiced & (rms_db > _ABSOLUTE_VOICED_FLOOR_DB)
    else:
        active = (rms_db > floor + config.activity_margin_db) | (
            voiced & (rms_db > floor + _VOICED_ACTIVITY_MARGIN_DB)
        )
    return _smooth_runs(active).astype(np.float64)


def _smooth_runs(active: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """Fill silent gaps up to 3 frames, then drop active runs of 2 frames or fewer.

    Only runs enclosed by the other state are changed; the start and end of the audio stay as
    measured.
    """
    result = active.copy()
    for value, max_length in ((False, _MAX_GAP_FRAMES), (True, _MIN_RUN_FRAMES - 1)):
        boundaries = np.concatenate(
            ([0], np.flatnonzero(np.diff(result.astype(np.int8))) + 1, [len(result)]),
        )
        for start, end in pairwise(boundaries.tolist()):
            if (
                bool(result[start]) is value
                and end - start <= max_length
                and 0 < start < len(result)
                and end < len(result)
            ):
                result[start:end] = not value
    return result


def _round_columns(columns: dict[str, Floats]) -> dict[str, Floats]:
    return {name: np.round(values, FRAME_PRECISION[name]) for name, values in columns.items()}


class AcousticExtractor:
    """Structurally implements ``application.ports.AcousticExtractor``."""

    def __init__(
        self,
        pitch: PitchAnalyzer | None = None,
        detectors: tuple[AudioEventDetector, ...] = (),
        *,
        max_workers: int = 3,
    ) -> None:
        self._pitch = pitch or ParselmouthPitchAnalyzer()
        self._detectors = detectors
        self._max_workers = max_workers

    def identity(self, config: AcousticConfig) -> dict[str, str]:
        _ = config
        found = {
            "pitch": self._pitch.identity,
            "energy": ENERGY_VERSION,
            "loudness": LOUDNESS_VERSION,
            "activity": ACTIVITY_VERSION,
        }
        found.update({f"detector:{d.name}": d.version for d in self._detectors})
        return found

    def measure(
        self,
        audio: Path,
        config: AcousticConfig,
        cancellation: CancellationToken,
    ) -> AcousticMeasurements:
        samples, rate = read_mono_pcm16(audio)
        signal = AudioSignal(samples, rate)
        frames = math.ceil(signal.duration / config.hop)
        cancellation.raise_if_cancelled()

        with ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="acoustic"
        ) as pool:
            pitch_job = pool.submit(self._pitch.analyze, signal, config, frames)
            energy_job = pool.submit(measure_energy, signal, config, frames)
            detector_jobs = [pool.submit(_run_detector, d, signal) for d in self._detectors]
            pitch = pitch_job.result()
            rms_db, loudness = energy_job.result()
            detected = [event for job in detector_jobs for event in job.result()]
        cancellation.raise_if_cancelled()

        speech = measure_activity(rms_db, pitch.f0, pitch.confidence, config)
        columns = _round_columns(
            {
                "f0": pitch.f0,
                "pitch_confidence": pitch.confidence,
                "rms_db": rms_db,
                "loudness": loudness,
                "speech": speech,
            },
        )
        track = AcousticTrack.of(
            origin=0.0,
            hop=config.hop,
            f0=_values(columns["f0"]),
            pitch_confidence=_values(columns["pitch_confidence"]),
            rms_db=_values(columns["rms_db"]),
            loudness=_values(columns["loudness"]),
            speech=_values(columns["speech"]),
        )
        _log.info(
            "Acoustic measurements done",
            seconds=round(signal.duration, 1),
            frames=frames,
            voiced=int(np.isfinite(pitch.f0).sum()),
            speech=int(speech.sum()),
            pitch=self._pitch.identity,
        )
        return AcousticMeasurements(
            track=track,
            identity=self.identity(config),
            parameters=pitch.parameters,
            events=tuple(sorted(detected, key=lambda e: (e.start, e.kind))),
            warnings=pitch.warnings,
        )


def _values(values: Floats) -> list[float]:
    return [float(v) for v in values]


def _run_detector(detector: AudioEventDetector, signal: AudioSignal) -> list[AudioEvent]:
    try:
        return detector.detect(signal)
    except Exception as exc:
        raise ExternalSystemError(
            f"Event detector {detector.name!r} failed: {exc}",
            user_message=f"The '{detector.name}' event detector failed.",
        ) from exc
