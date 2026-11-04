"""Sound-effect profile on mono float PCM: timing (onset, attack, peak, tail), level and colour.

Envelope = RMS over 5 ms frames. Onset = first frame within 20 dB of the envelope peak; the
useful start sits 5 ms before it (leading silence is skipped). Tail = time from the peak until
the envelope stays 40 dB below it; the useful end is that point. Colour = spectral centroid and
the share of energy above 4 kHz over the useful span (Welch spectrum).
"""

import numpy as np
from numpy.typing import NDArray
from scipy import signal as sps

from media_house.modules.music_and_sound_design.domain.analysis import (
    Loudness,
    SfxMeasurement,
    Unavailable,
)

SAMPLE_RATE = 48_000
_FRAME_S = 0.005
_RMS_WINDOW_S = 0.05
_ONSET_BELOW_PEAK_DB = 20.0
_TAIL_BELOW_PEAK_DB = 40.0
_PRE_ROLL_S = 0.005
_BRIGHT_HZ = 4000.0


def measure_sfx_signal(
    samples: NDArray[np.float64],
    *,
    loudness: Loudness,
    engines: tuple[tuple[str, str], ...] = (),
) -> SfxMeasurement:
    x = np.asarray(samples, dtype=np.float64)
    duration = len(x) / SAMPLE_RATE
    frame = max(1, int(_FRAME_S * SAMPLE_RATE))
    count = max(1, int(np.ceil(len(x) / frame)))
    env = np.sqrt(
        np.mean(np.pad(x, (0, count * frame - len(x))).reshape(count, frame) ** 2, axis=1)
    )
    unavailable: list[Unavailable] = []
    if loudness.integrated_lufs is None:
        unavailable.append(
            Unavailable(
                what="integrated_loudness",
                reason="too short or too quiet for the 400 ms EBU R128 gate; use rms_max_dbfs",
            )
        )
    peak_env = float(env.max(initial=0.0))
    peak_sample = int(np.argmax(np.abs(x))) if len(x) else 0
    peak_dbfs = _db(float(np.abs(x).max(initial=0.0)))
    if peak_env <= 1e-7:
        return SfxMeasurement(
            duration_seconds=round(duration, 4),
            sample_rate=SAMPLE_RATE,
            loudness=loudness,
            peak_dbfs=peak_dbfs,
            rms_max_dbfs=peak_dbfs,
            onset_sample=0,
            peak_sample=0,
            useful_start_sample=0,
            useful_end_sample=len(x),
            attack_seconds=0.0,
            tail_seconds=0.0,
            spectral_centroid_hz=0.0,
            brightness=0.0,
            not_available=(*unavailable, Unavailable(what="timing", reason="the file is silent")),
            warnings=("the file is silent",),
            engines=engines,
        )
    peak_frame = int(np.argmax(env))
    onset_frame = int(np.flatnonzero(env >= peak_env * 10 ** (-_ONSET_BELOW_PEAK_DB / 20))[0])
    above_tail = np.flatnonzero(env >= peak_env * 10 ** (-_TAIL_BELOW_PEAK_DB / 20))
    end_frame = int(above_tail[-1]) + 1
    onset_sample = onset_frame * frame
    useful_start = max(0, onset_sample - int(_PRE_ROLL_S * SAMPLE_RATE))
    useful_end = min(len(x), end_frame * frame)
    window = max(1, int(_RMS_WINDOW_S * SAMPLE_RATE))
    power = (
        np.convolve(x**2, np.ones(window) / window, mode="valid")
        if len(x) >= window
        else [float(np.mean(x**2))]
    )
    rms_max = _db(float(np.sqrt(np.max(power))))
    span = x[useful_start:useful_end]
    centroid, brightness = _colour(span if len(span) >= 64 else x)
    warnings = []
    if peak_dbfs > -0.1:
        warnings.append(f"sample peak {peak_dbfs:.1f} dBFS: the file may be clipped")
    return SfxMeasurement(
        duration_seconds=round(duration, 4),
        sample_rate=SAMPLE_RATE,
        loudness=loudness,
        peak_dbfs=round(peak_dbfs, 2),
        rms_max_dbfs=round(rms_max, 2),
        onset_sample=onset_sample,
        peak_sample=peak_sample,
        useful_start_sample=useful_start,
        useful_end_sample=useful_end,
        attack_seconds=round(max(0.0, (peak_frame - onset_frame) * _FRAME_S), 4),
        tail_seconds=round(max(0.0, (end_frame - peak_frame) * _FRAME_S), 4),
        spectral_centroid_hz=round(centroid, 1),
        brightness=round(brightness, 3),
        not_available=tuple(unavailable),
        warnings=tuple(warnings),
        engines=engines,
    )


def _db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-6)))


def _colour(x: NDArray[np.float64]) -> tuple[float, float]:
    if len(x) < 16:
        return 0.0, 0.0
    freqs, power = sps.welch(x, fs=SAMPLE_RATE, nperseg=min(2048, len(x)))
    total = float(power.sum())
    if total <= 0:
        return 0.0, 0.0
    centroid = float((freqs * power).sum() / total)
    return centroid, float(power[freqs >= _BRIGHT_HZ].sum() / total)
