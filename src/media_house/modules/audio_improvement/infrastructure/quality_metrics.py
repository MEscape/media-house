"""Signal measurements (NumPy/SciPy): noise, speech, hum, clipping, balance, sibilance, reverb.

Pure functions on decoded samples; loudness (LUFS, true peak) is measured by FFmpeg's EBU R128
filter in ``quality_analyzer.py``. Everything here is a MEASUREMENT: thresholds and decisions
live in the profile settings, never in this file. ``None`` = not enough evidence.

The estimators are deliberately simple and conservative (percentile-based, evidence-gated):
the stage guards re-measure the output, so a wrong decision costs processing time, not quality.
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import signal

type Floats = NDArray[np.float64]

_EPSILON = 1e-12
_FRAME_SECONDS = 0.05
#: Level frames are "speech" this far (dB) above the quiet floor.
_SPEECH_MARGIN_DB = 10.0
#: Without this level contrast (dB) between loud and quiet frames there is no speech/noise split.
_MIN_SEPARATION_DB = 6.0
#: Frames within this many dB of the quietest ones are the noise (room tone between speech).
_NOISE_MARGIN_DB = 3.0
#: Noise spectra need contiguous quiet stretches at least this long (seconds).
_NOISE_SEGMENT_SECONDS = 0.33
_MIN_SPECTRUM_SECONDS = 0.5
_MIN_DYNAMICS_FRAMES = 20
_MAINS_HZ = (50.0, 60.0)
_HUM_MIN_PROMINENCE_DB = 6.0
_HIGH_FREQUENCY_HZ = 4_000.0
#: Clipping: a flat run of >= 3 samples within 1% of the peak and within 0.1% of each other.
_CLIP_PEAK_SHARE = 0.99
_CLIP_FLATNESS = 1e-3
_CLIP_MIN_RUN = 3
_CLIP_MIN_PEAK = 0.5
_SIBILANCE_BAND = (5_000.0, 9_000.0)
_SIBILANCE_PERCENTILE = 95.0
# reverberation: envelope at 10 ms; decays measured from 5 dB to 25 dB below each peak
_ENVELOPE_SECONDS = 0.01
_DECAY_START_DB, _DECAY_END_DB = 5.0, 25.0
_DECAY_PEAK_ABOVE_FLOOR_DB = 25.0
#: A free decay starts where a steady sound (within `_DECAY_STEADY_DB`) drops by more than this.
_DECAY_ONSET_DROP_DB = 2.0
_DECAY_STEADY_DB = 1.5
#: A decay is followed until it falls this far below its peak (dB) ...
_DECAY_TRACE_DB = 40.0
#: ... or comes this close to the floor, or rebounds by this much (a new burst of sound).
_DECAY_FLOOR_MARGIN_DB = 3.0
_DECAY_REBOUND_DB = 6.0
_DECAY_MIN_POINTS = 4
_DECAY_MIN_R2 = 0.85
_MIN_DECAYS = 3
_RT60_RANGE = (0.1, 3.0)


@dataclass(frozen=True, slots=True)
class SignalMetrics:
    sample_peak_dbfs: float | None
    rms_dbfs: float | None
    speech_ratio: float
    speech_level_dbfs: float | None
    noise_floor_dbfs: float | None
    snr_db: float | None
    hum_hz: float | None
    hum_prominence_db: float | None
    noise_hf_share: float | None
    clipping_ratio: float
    crest_factor_db: float | None
    speech_dynamics_db: float | None
    sibilance_peak_db: float | None
    rumble_db: float | None
    mud_db: float | None
    harshness_db: float | None
    reverb_rt60: float | None


def _db(power: float) -> float:
    return 10.0 * math.log10(max(power, _EPSILON))


def _frame_power(mono: Floats, size: int) -> NDArray[np.float64]:
    count = len(mono) // size
    return np.asarray(
        (mono[: count * size].reshape(count, size) ** 2).mean(axis=1), dtype=np.float64
    )


def measure_signal(samples: Floats, rate: int) -> SignalMetrics:
    """``samples`` shaped (frames, channels) in -1..1."""
    mono = samples.mean(axis=1)
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    size = max(1, round(_FRAME_SECONDS * rate))
    count = len(mono) // size
    frames = mono[: count * size].reshape(count, size) if count else np.empty((0, size))
    power = (frames**2).mean(axis=1) if count else np.empty(0)
    rms = _db(float((mono**2).mean())) if mono.size and peak > 0 else None

    sample_peak = _db(peak**2) if peak > 0 else None
    clipping = _clipping_ratio(samples, peak)
    reverb = _reverb_time(mono, rate)
    if count < 4 or peak == 0:
        return SignalMetrics(
            speech_ratio=0.0,
            speech_level_dbfs=None,
            noise_floor_dbfs=None,
            snr_db=None,
            hum_hz=None,
            hum_prominence_db=None,
            noise_hf_share=None,
            crest_factor_db=None,
            speech_dynamics_db=None,
            sibilance_peak_db=None,
            rumble_db=None,
            mud_db=None,
            harshness_db=None,
            sample_peak_dbfs=sample_peak,
            rms_dbfs=rms,
            clipping_ratio=clipping,
            reverb_rt60=reverb,
        )

    level = 10.0 * np.log10(power + _EPSILON)
    quiet, loud = np.percentile(level, [10, 95])
    separated = loud - quiet >= _MIN_SEPARATION_DB
    active = (level > quiet + _SPEECH_MARGIN_DB) if separated else np.zeros(count, dtype=bool)
    quiet_frames = level <= quiet + _NOISE_MARGIN_DB

    noise_floor = _db(float(power[quiet_frames].mean()))
    speech_level = _db(float(power[active].mean())) if active.any() else None
    snr = (
        speech_level - noise_floor
        if speech_level is not None and speech_level - noise_floor >= _MIN_SEPARATION_DB
        else None
    )
    speech_samples = frames[active].ravel() if active.any() else np.empty(0)
    noise = _noise_spectrum(frames, quiet_frames, rate)
    hum_hz, hum_prominence = _hum(noise)
    balance = _balance(speech_samples, rate)
    return SignalMetrics(
        speech_ratio=float(active.mean()),
        speech_level_dbfs=speech_level,
        noise_floor_dbfs=noise_floor,
        snr_db=snr,
        hum_hz=hum_hz,
        hum_prominence_db=hum_prominence,
        noise_hf_share=_high_frequency_share(noise, rate),
        crest_factor_db=(_db(peak**2) - speech_level if speech_level is not None else None),
        speech_dynamics_db=_dynamics(level[active]),
        sibilance_peak_db=_sibilance(speech_samples, rate),
        rumble_db=balance[0],
        mud_db=balance[1],
        harshness_db=balance[2],
        sample_peak_dbfs=sample_peak,
        rms_dbfs=rms,
        clipping_ratio=clipping,
        reverb_rt60=reverb,
    )


def _dynamics(speech_levels: Floats) -> float | None:
    """Spread (dB) between the quiet tenth and the loudest 5% of the speech frames."""
    if len(speech_levels) < _MIN_DYNAMICS_FRAMES:
        return None
    low, high = np.percentile(speech_levels, [10, 95])
    return float(high - low)


# --- clipping ------------------------------------------------------------------------------------
def _clipping_ratio(samples: Floats, peak: float) -> float:
    """Share of samples inside digitally clipped runs (flat tops at the signal's peak)."""
    if peak < _CLIP_MIN_PEAK or samples.size == 0:
        return 0.0
    clipped = 0
    for channel in samples.T:
        near = np.abs(channel) >= _CLIP_PEAK_SHARE * peak
        flat = near[:-1] & near[1:] & (np.abs(np.diff(channel)) <= _CLIP_FLATNESS * peak)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], flat.astype(np.int8), [0]))))
        for start, end in zip(edges[::2], edges[1::2], strict=True):
            run = end - start + 1  # pairs -> samples
            if run >= _CLIP_MIN_RUN:
                clipped += int(run)
    return min(1.0, clipped / samples.size)


# --- spectrum ------------------------------------------------------------------------------------
def _welch(samples: Floats, rate: int, nperseg: int) -> tuple[Floats, Floats]:
    freqs, psd = signal.welch(samples, fs=rate, nperseg=nperseg)
    return np.asarray(freqs, dtype=np.float64), np.asarray(psd, dtype=np.float64)


def _band(freqs: Floats, psd: Floats, low: float, high: float) -> float:
    return float(psd[(freqs >= low) & (freqs < high)].sum())


def _ratio_db(numerator: float, denominator: float) -> float | None:
    return (
        None if denominator <= 0 or numerator <= 0 else 10.0 * math.log10(numerator / denominator)
    )


def _balance(speech: Floats, rate: int) -> tuple[float | None, float | None, float | None]:
    """(rumble, mud, harshness) in dB relative to the speech body, from the speech frames."""
    if len(speech) < _MIN_SPECTRUM_SECONDS * rate:
        return None, None, None
    nperseg = min(4096, 2 ** int(math.log2(len(speech))))
    freqs, psd = _welch(speech, rate, nperseg)
    body = _band(freqs, psd, 200, 3_000)
    mids = _band(freqs, psd, 500, 2_000)
    nyquist = rate / 2
    harsh = _ratio_db(_band(freqs, psd, 2_000, 5_000), mids) if nyquist >= 5_000 else None
    return (
        _ratio_db(_band(freqs, psd, 20, 70), body),
        _ratio_db(_band(freqs, psd, 200, 500), mids),
        harsh,
    )


def _noise_spectrum(
    frames: Floats, quiet: NDArray[np.bool_], rate: int
) -> tuple[Floats, Floats] | None:
    """Mean power spectrum of the contiguous quiet stretches (``None`` without a long enough one).

    Stretches are analysed separately: joining separate pauses would break the phase of a steady
    line such as mains hum and smear it across the spectrum.
    """
    size = frames.shape[1]
    nperseg = round(_NOISE_SEGMENT_SECONDS * rate)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], quiet.astype(np.int8), [0]))))
    spectra: list[Floats] = []
    freqs: Floats | None = None
    for start, end in zip(edges[::2], edges[1::2], strict=True):
        if (end - start) * size >= nperseg:
            freqs, psd = _welch(frames[start:end].ravel(), rate, nperseg)
            spectra.append(psd)
    if freqs is None:
        return None
    return freqs, np.mean(spectra, axis=0)


def _hum(noise: tuple[Floats, Floats] | None) -> tuple[float | None, float | None]:
    """The mains line (50/60 Hz) standing out of the noise spectrum, if any."""
    if noise is None:
        return None, None
    freqs, psd = noise
    resolution = float(freqs[1] - freqs[0])
    best: tuple[float, float] | None = None
    for line in _MAINS_HZ:
        centre = np.abs(freqs - line) <= resolution
        around = (np.abs(freqs - line) >= 5.0) & (np.abs(freqs - line) <= 15.0)
        prominence = _ratio_db(float(psd[centre].max()), float(np.median(psd[around])))
        if prominence is not None and (best is None or prominence > best[1]):
            best = (line, prominence)
    if best is None or best[1] < _HUM_MIN_PROMINENCE_DB:
        return None, None
    return best


def _high_frequency_share(noise: tuple[Floats, Floats] | None, rate: int) -> float | None:
    if noise is None or rate / 2 <= _HIGH_FREQUENCY_HZ:
        return None
    freqs, psd = noise
    total = float(psd.sum())
    return None if total <= 0 else float(psd[freqs >= _HIGH_FREQUENCY_HZ].sum() / total)


def _sibilance(speech: Floats, rate: int) -> float | None:
    """Peaks (95th percentile) of the 5-9 kHz band relative to the mean speech power, in dB."""
    low, high = _SIBILANCE_BAND
    high = min(high, 0.45 * rate)
    if len(speech) < _MIN_SPECTRUM_SECONDS * rate or high - low < 1_000.0:
        return None
    freqs, _, spectrum = signal.stft(speech, fs=rate, nperseg=round(0.025 * rate))
    power = np.abs(spectrum) ** 2
    mean_total = float(power.sum(axis=0).mean())
    band = power[(freqs >= low) & (freqs < high)].sum(axis=0)
    if mean_total <= 0:
        return None
    ratio = 10.0 * np.log10((band + _EPSILON) / mean_total)
    return float(np.percentile(ratio, _SIBILANCE_PERCENTILE))


# --- reverberation -------------------------------------------------------------------------------
def _reverb_time(mono: Floats, rate: int) -> float | None:
    """Blind RT60 from free decays after loud peaks; ``None`` without enough evidence.

    Reports reverberation that is audible as a tail after speech stops (direct-to-reverberant
    ratio around -5 dB or worse), which is exactly when dereverberation is worth considering.
    """
    size = max(1, round(_ENVELOPE_SECONDS * rate))
    power = _frame_power(mono, size)
    if len(power) < 30:
        return None
    level = 10.0 * np.log10(power + _EPSILON)
    floor = float(np.percentile(level, 10))
    estimates: list[float] = []
    index = 1
    while index < len(level) - 1:
        if (
            level[index] > floor + _DECAY_PEAK_ABOVE_FLOOR_DB
            and level[index + 1] < level[index] - _DECAY_ONSET_DROP_DB
            and level[index] >= level[index - 1] - _DECAY_STEADY_DB
        ):
            end = _decay_end(level, index, floor)
            estimate = _fit_decay(power[index : end + 1])
            if estimate is not None:
                estimates.append(estimate)
            index = end + 1
        else:
            index += 1
    return float(np.median(estimates)) if len(estimates) >= _MIN_DECAYS else None


def _decay_end(level: Floats, peak_index: int, floor: float) -> int:
    """Last frame of the free decay (before the floor, a fall of 40 dB or a new burst)."""
    peak = level[peak_index]
    lowest = peak
    end = peak_index
    while end + 1 < len(level):
        nxt = level[end + 1]
        if nxt < peak - _DECAY_TRACE_DB or nxt <= floor + _DECAY_FLOOR_MARGIN_DB:
            break
        if nxt > lowest + _DECAY_REBOUND_DB:
            break
        end += 1
        lowest = min(lowest, nxt)
    return end


def _fit_decay(power: Floats) -> float | None:
    """RT60 (s) from the Schroeder decay curve between 5 and 25 dB down, or ``None``."""
    if len(power) < _DECAY_MIN_POINTS * 2:
        return None
    curve = np.cumsum(power[::-1])[::-1]
    curve_db = 10.0 * np.log10(curve / curve[0] + _EPSILON)
    window = (curve_db <= -_DECAY_START_DB) & (curve_db >= -_DECAY_END_DB)
    if window.sum() < _DECAY_MIN_POINTS or curve_db.min() > -_DECAY_END_DB:
        return None
    times = np.flatnonzero(window) * _ENVELOPE_SECONDS
    values = curve_db[window]
    slope, intercept = np.polyfit(times, values, 1)
    if slope >= 0:
        return None
    residual = float(((values - (slope * times + intercept)) ** 2).sum())
    total = float(((values - values.mean()) ** 2).sum())
    if total <= 0 or 1.0 - residual / total < _DECAY_MIN_R2:
        return None
    rt60 = -60.0 / float(slope)
    return rt60 if _RT60_RANGE[0] <= rt60 <= _RT60_RANGE[1] else None
