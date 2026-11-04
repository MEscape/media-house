"""Music measurement on mono float PCM (numpy/scipy only; no model, no librosa).

Pipeline (all deterministic):

1. Short-time spectrum (1024-sample Hann window, 256-sample hop at 22.05 kHz, about 86 frames/s),
   pooled into 64 log-spaced bands and 12 pitch classes, in chunks so a long track never holds
   the whole spectrogram.
2. Onset envelope = mean half-wave-rectified rise of the band levels in dB (spectral flux).
3. Tempo = the strongest periodicity of the onset envelope: autocorrelation averaged over 8 s
   windows, weighted by a log-normal prior around 120 BPM (one octave wide), refined by
   parabolic interpolation. Confidence = how far that peak stands above the median of the
   autocorrelation; stability = the share of 20 s windows that agree within 4 %.
4. Beats = dynamic-programming beat tracker after Ellis (2007), "Beat Tracking by Dynamic
   Programming": maximise onset strength at the beats minus a penalty on the squared log
   deviation of each inter-beat interval from the tempo period (tightness 100). The final BPM
   is the least-squares slope of the beat times.
5. Downbeats = the beat phase (of ``METER`` = 4, an assumption: there is no meter detector) with
   the strongest low-band onsets and harmonic change; confidence = its margin over the others.
6. Sections = peaks of Foote's checkerboard novelty on the beat-synchronous self-similarity of
   z-scored chroma + level, snapped to downbeats. Labels only from level evidence.
7. Loop = the pair of downbeats at least 8 bars apart whose following 4 bars are most alike.
8. Edit points = track start, section starts, 4-bar phrases, loop points, fade-out, ending.

Vocal presence needs a separation/classification model that is not installed: ``not_available``.
"""

import itertools
from dataclasses import dataclass
from fractions import Fraction

import numpy as np
from numpy.typing import NDArray
from scipy import signal as sps

from media_house.core.domain import Rational
from media_house.modules.music_and_sound_design.domain.analysis import (
    EditPoint,
    LoopRegion,
    Loudness,
    MusicMeasurement,
    MusicSection,
    Tempo,
    Unavailable,
)
from media_house.modules.music_and_sound_design.domain.values import (
    NOT_AVAILABLE,
    EditPointKind,
    SectionLabel,
)

type Signal = NDArray[np.float64]

SAMPLE_RATE = 22_050
HOP = 256
N_FFT = 1024
METER = 4
ENERGY_RATE = 2  # energy values per second

_BANDS = 64
_LOW_BAND_HZ = 200.0
_TEMPO_MIN, _TEMPO_MAX = 40.0, 240.0
_PRIOR_BPM, _PRIOR_OCTAVES = 120.0, 1.0
_AC_WINDOW_S = 8.0
_STABILITY_WINDOW_S, _STABILITY_HOP_S, _STABILITY_TOLERANCE = 20.0, 10.0, 0.04
_TIGHTNESS = 100.0
#: The centred spectral window sees an onset about one hop early (measured on synthetic tracks).
_LATENCY_FRAMES = 1
_KERNEL_BEATS = 16
_MIN_SECTION_BEATS = 16
_MAX_SECTIONS = 12
_LOOP_WINDOW_BEATS = 16
_LOOP_MIN_GAP_BEATS = 32
_LOOP_MIN_SIMILARITY = 0.8
_LABEL_MARGIN_DB = 3.0
_DROP_JUMP_DB = 6.0
_BUILD_RISE_DB = 4.0
_FADE_DROP_DB = 12.0
_AUDIBLE_BELOW_MAX_DB = 40.0


@dataclass(frozen=True, slots=True)
class _Spectral:
    onset: Signal  # per frame
    low_onset: Signal  # per frame
    chroma: Signal  # (frames, 12)
    rms: Signal  # per frame (linear)


def frame_rate() -> Rational:
    return Rational(SAMPLE_RATE, HOP)


def measure_music_signal(
    samples: Signal,
    *,
    loudness: Loudness,
    engines: tuple[tuple[str, str], ...] = (),
) -> MusicMeasurement:
    """Every music measurement of mono ``samples`` at ``SAMPLE_RATE``."""
    x = np.asarray(samples, dtype=np.float64)
    duration = len(x) / SAMPLE_RATE
    fps = SAMPLE_RATE / HOP
    unavailable = [
        Unavailable(
            what="vocal_presence",
            reason="no source-separation or vocal-detection model is installed",
        )
    ]
    warnings: list[str] = []
    spectral = _spectral(x)
    onset = spectral.onset
    energy = _energy_curve(x)
    onset_rate = _onset_rate(onset, duration)

    tempo: Tempo | None = None
    beats = np.zeros(0, dtype=np.int64)
    if duration < 6.0 or float(np.max(onset, initial=0.0)) <= 1e-9:
        unavailable.append(
            Unavailable(what="tempo", reason="too short or no onsets to measure a tempo")
        )
    else:
        bpm, confidence, _ac = _tempo(onset, fps)
        beats = _track_beats(onset, fps, bpm)
        if len(beats) >= 8:
            slope = float(np.polyfit(np.arange(len(beats)), beats / fps, 1)[0])
            if slope > 0 and abs(60.0 / slope - bpm) / bpm < 0.1:
                bpm = 60.0 / slope
        stability = _stability(onset, fps, bpm)
        tempo = Tempo(
            bpm=round(bpm, 2),
            confidence=round(confidence, 3),
            stability=round(stability, 3),
            octave_alternatives=(round(bpm / 2, 2), round(bpm * 2, 2)),
        )
        if confidence < 0.2:
            warnings.append(f"weak periodicity (confidence {confidence:.2f}): beats are uncertain")

    downbeats, down_conf = _downbeats(beats, spectral)
    if not len(downbeats):
        unavailable.append(Unavailable(what="downbeats", reason="fewer than 8 beats"))
    sync = _beat_features(beats, spectral, len(onset))
    starts, strength = _boundaries(sync, beats, downbeats)
    frames_of = [0 if b == 0 else int(beats[b]) for b in starts]
    novelty = {f: strength[b] for f, b in zip(frames_of, starts, strict=True)}
    sections = _label(frames_of, len(onset), energy)
    loop = _loop(beats, downbeats, sync)
    if loop is None:
        unavailable.append(
            Unavailable(what="loop", reason="no two 4-bar passages are alike enough to loop")
        )
    points = _edit_points(beats, downbeats, sections, novelty, loop, energy, fps)
    return MusicMeasurement(
        duration_seconds=round(duration, 4),
        frame_rate=frame_rate(),
        loudness=loudness,
        onset_envelope=tuple(round(float(v), 3) for v in onset / max(float(onset.max()), 1e-9)),
        onset_rate=round(onset_rate, 3),
        tempo=tempo,
        beat_frames=tuple(int(b) for b in beats),
        downbeat_frames=tuple(int(b) for b in downbeats),
        downbeat_confidence=None if down_conf is None else round(down_conf, 3),
        meter_assumed=METER,
        energy_rate=Rational(ENERGY_RATE, 1),
        energy_db=tuple(round(float(v), 1) for v in energy),
        sections=sections,
        loop=loop,
        edit_points=points,
        vocal_presence=NOT_AVAILABLE,
        not_available=tuple(unavailable),
        warnings=tuple(warnings),
        engines=engines,
    )


# --- spectrum ------------------------------------------------------------------------------
def _band_matrix(freqs: Signal) -> Signal:
    edges = np.geomspace(30.0, min(8000.0, SAMPLE_RATE / 2), _BANDS + 1)
    matrix = np.zeros((len(freqs), _BANDS))
    for b in range(_BANDS):
        inside = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not inside.any():  # narrow low bands: take the nearest bin
            inside = np.zeros(len(freqs), dtype=bool)
            inside[int(np.argmin(np.abs(freqs - (edges[b] + edges[b + 1]) / 2)))] = True
        matrix[inside, b] = 1.0 / inside.sum()
    return matrix


def _chroma_matrix(freqs: Signal) -> Signal:
    matrix = np.zeros((len(freqs), 12))
    usable = (freqs >= 60.0) & (freqs <= 5000.0)
    pitch = np.round(12 * np.log2(np.where(usable, freqs, 1.0) / 440.0) + 69).astype(int) % 12
    matrix[np.flatnonzero(usable), pitch[usable]] = 1.0
    return matrix


def _spectral(x: Signal) -> _Spectral:
    padded = np.pad(x, (N_FFT // 2, N_FFT // 2))
    frames = 1 + max(0, len(padded) - N_FFT) // HOP
    window = np.hanning(N_FFT)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SAMPLE_RATE)
    bands, pitch = _band_matrix(freqs), _chroma_matrix(freqs)
    low = np.geomspace(30.0, min(8000.0, SAMPLE_RATE / 2), _BANDS + 1)[1:] <= _LOW_BAND_HZ
    view = np.lib.stride_tricks.sliding_window_view(padded, N_FFT)[::HOP][:frames]
    onset, low_onset = np.zeros(frames), np.zeros(frames)
    chroma = np.zeros((frames, 12))
    previous: Signal | None = None
    chunk = 1024
    for start in range(0, frames, chunk):
        power = np.abs(np.fft.rfft(view[start : start + chunk] * window, axis=1)) ** 2
        level = 10.0 * np.log10(np.maximum(power @ bands, 1e-10))
        level = np.maximum(level, level.max(initial=-100.0) - 80.0)
        stacked = level if previous is None else np.vstack([previous[None, :], level])
        rise = np.maximum(np.diff(stacked, axis=0), 0.0)
        if previous is None:
            rise = np.vstack([np.zeros((1, _BANDS)), rise])
        onset[start : start + len(rise)] = rise.mean(axis=1)
        low_onset[start : start + len(rise)] = rise[:, low].mean(axis=1)
        c = power @ pitch
        chroma[start : start + len(c)] = c / np.maximum(c.max(axis=1, keepdims=True), 1e-10)
        previous = level[-1]
    rms = np.sqrt(np.mean(view**2, axis=1)) if frames else np.zeros(0)
    return _Spectral(onset, low_onset, chroma, rms)


def _energy_curve(x: Signal) -> Signal:
    step = SAMPLE_RATE // ENERGY_RATE
    count = max(1, int(np.ceil(len(x) / step)))
    padded = np.pad(x, (0, count * step - len(x)))
    rms = np.sqrt(np.mean(padded.reshape(count, step) ** 2, axis=1))
    return np.asarray(20.0 * np.log10(np.maximum(rms, 1e-5)), dtype=np.float64)


def _onset_rate(onset: Signal, duration: float) -> float:
    if duration <= 0 or not len(onset):
        return 0.0
    peaks, _ = sps.find_peaks(onset, height=float(onset.mean() + onset.std()), distance=4)
    return len(peaks) / duration


# --- tempo ---------------------------------------------------------------------------------
def _autocorrelation(onset: Signal, fps: float) -> Signal:
    """Window-averaged, per-window normalised autocorrelation of the onset envelope."""
    window = int(_AC_WINDOW_S * fps)
    max_lag = 2 * int(np.ceil(60.0 * fps / _TEMPO_MIN)) + 2  # doubles for TPS2
    env = onset - onset.mean()
    if len(env) <= window:
        starts = [0]
        window = len(env)
    else:
        starts = list(range(0, len(env) - window + 1, window // 2))
    total = np.zeros(max_lag + 1)
    used = 0
    for s in starts:
        seg = env[s : s + window] - env[s : s + window].mean()
        size = 1 << int(np.ceil(np.log2(2 * len(seg) + 1)))
        spec = np.fft.rfft(seg, size)
        ac = np.fft.irfft(spec * np.conj(spec), size)[: max_lag + 1]
        if ac[0] > 1e-12:
            total += ac / ac[0]
            used += 1
    return total / max(used, 1)


def _tempo(onset: Signal, fps: float) -> tuple[float, float, Signal]:
    ac = _autocorrelation(onset, fps)
    return (*_pick_tempo(ac, fps, _PRIOR_BPM, _PRIOR_OCTAVES), ac)


def _pick_tempo(ac: Signal, fps: float, centre: float, octaves: float) -> tuple[float, float]:
    lags = np.arange(len(ac), dtype=np.float64)
    valid = (lags >= 60.0 * fps / _TEMPO_MAX) & (lags <= 60.0 * fps / _TEMPO_MIN)
    bpm = np.where(lags > 0, 60.0 * fps / np.maximum(lags, 1e-9), 0.0)
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1e-9) / centre) / octaves) ** 2)
    # Ellis's TPS2: a period also gains from its double, so a 2-beat pattern (kick/snare) does
    # not halve the tempo.
    positive = np.maximum(ac, 0.0)
    doubled = np.zeros_like(positive)
    for k in range(len(positive)):
        if 2 * k + 1 < len(positive):
            doubled[k] = 0.5 * positive[2 * k] + 0.25 * (positive[2 * k - 1] + positive[2 * k + 1])
    weighted = np.where(valid, (positive + doubled) * prior, -np.inf)
    lag = int(np.argmax(weighted))
    refined = float(lag)
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denominator = a - 2 * b + c
        if abs(denominator) > 1e-12:
            refined = lag + float(np.clip(0.5 * (a - c) / denominator, -0.5, 0.5))
    median = float(np.median(ac[valid]))
    confidence = float(np.clip((ac[lag] - median) / max(1e-9, 1.0 - median), 0.0, 1.0))
    return 60.0 * fps / refined, confidence


def _stability(onset: Signal, fps: float, bpm: float) -> float:
    window, hop = int(_STABILITY_WINDOW_S * fps), int(_STABILITY_HOP_S * fps)
    if len(onset) < window:
        return 1.0
    agree = total = 0
    for s in range(0, len(onset) - window + 1, hop):
        ac = _autocorrelation(onset[s : s + window], fps)
        local, _ = _pick_tempo(ac, fps, bpm, 0.5)
        ratio = local / bpm
        folded = min(abs(ratio - k) / k for k in (0.5, 1.0, 2.0))
        agree += folded <= _STABILITY_TOLERANCE
        total += 1
    return agree / max(total, 1)


# --- beats (Ellis 2007) ----------------------------------------------------------------------
def _track_beats(onset: Signal, fps: float, bpm: float) -> NDArray[np.int64]:
    period = 60.0 * fps / bpm
    std = float(onset.std())
    norm = onset / std if std > 0 else onset
    half = round(period)
    kernel = np.exp(-0.5 * (np.arange(-half, half + 1) * 32.0 / period) ** 2)
    local = np.convolve(norm, kernel, mode="same")
    n = len(local)
    offsets = np.arange(-round(2 * period), -round(period / 2) + 1)
    penalty = -_TIGHTNESS * np.log(-offsets / period) ** 2
    cumulative = np.zeros(n)
    backlink = np.full(n, -1, dtype=np.int64)
    threshold = 0.01 * float(local.max(initial=0.0))
    started = False
    for i in range(n):
        candidates = i + offsets
        ok = candidates >= 0
        if started and ok.any():
            scores = cumulative[candidates[ok]] + penalty[ok]
            best = int(np.argmax(scores))
            cumulative[i] = local[i] + scores[best]
            backlink[i] = candidates[ok][best]
        else:
            cumulative[i] = local[i]
        started = started or local[i] > threshold
    peaks = np.flatnonzero(
        (cumulative[1:-1] > cumulative[:-2]) & (cumulative[1:-1] >= cumulative[2:])
    )
    if not len(peaks):
        return np.zeros(0, dtype=np.int64)
    peaks = peaks + 1
    median = float(np.median(cumulative[peaks]))
    last = int(peaks[cumulative[peaks] >= 0.5 * median][-1])
    beats = [last]
    while backlink[beats[-1]] >= 0:
        beats.append(int(backlink[beats[-1]]))
    out = np.minimum(np.array(beats[::-1], dtype=np.int64) + _LATENCY_FRAMES, n - 1)
    strength = local[out]
    rms = float(np.sqrt(np.mean(local**2)))
    keep = np.flatnonzero(strength >= 0.5 * rms)
    if not len(keep):
        return np.zeros(0, dtype=np.int64)
    return out[keep[0] : keep[-1] + 1]


def _downbeats(
    beats: NDArray[np.int64], spectral: _Spectral
) -> tuple[NDArray[np.int64], float | None]:
    if len(beats) < 2 * METER:
        return np.zeros(0, dtype=np.int64), None
    reach = max(1, int(np.median(np.diff(beats))) // 4)
    frames = len(spectral.onset)

    def peak(curve: Signal) -> Signal:
        return np.array(
            [float(curve[max(0, b - reach) : min(frames, b + reach + 1)].max()) for b in beats]
        )

    edges = [*(int(b) for b in beats), min(frames, int(beats[-1]) + 2 * reach)]
    means = np.array(
        [spectral.chroma[a : max(a + 1, b)].mean(axis=0) for a, b in itertools.pairwise(edges)]
    )
    change = np.r_[0.0, np.linalg.norm(np.diff(means, axis=0), axis=1)]
    cues = [peak(spectral.low_onset), peak(spectral.onset), change]
    total = sum(c / max(float(c.mean()), 1e-9) for c in cues)
    scores = np.array([float(np.mean(total[p::METER])) for p in range(METER)])
    order = np.argsort(scores)[::-1]
    best = int(order[0])
    others = float(np.mean(scores[order[1:]]))
    confidence = float(np.clip((scores[best] - others) / max(scores[best], 1e-9), 0.0, 1.0))
    return beats[best::METER], confidence


# --- structure -----------------------------------------------------------------------------
def _beat_features(beats: NDArray[np.int64], spectral: _Spectral, frames: int) -> Signal:
    if len(beats) < 2:
        return np.zeros((0, 13))
    edges = np.r_[beats, min(frames, int(beats[-1] + np.median(np.diff(beats))))]
    rows = []
    for a, b in itertools.pairwise(edges):
        b = max(int(b), int(a) + 1)
        chroma = spectral.chroma[a:b].mean(axis=0)
        level = 20 * np.log10(max(float(spectral.rms[a:b].mean()), 1e-5))
        rows.append(np.r_[chroma, level])
    feats = np.asarray(rows, dtype=np.float64)
    std = feats.std(axis=0)
    return np.asarray((feats - feats.mean(axis=0)) / np.where(std > 1e-9, std, 1.0))


def _cosine_ssm(feats: Signal) -> Signal:
    norm = feats / np.maximum(np.linalg.norm(feats, axis=1, keepdims=True), 1e-9)
    return np.asarray(norm @ norm.T)


def _boundaries(
    feats: Signal, beats: NDArray[np.int64], downbeats: NDArray[np.int64]
) -> tuple[list[int], dict[int, float]]:
    """Section starts as BEAT indices (0 first) and their novelty strength (0..1)."""
    if len(feats) < 2 * _MIN_SECTION_BEATS:
        return [0], {0: 1.0}
    ssm = _cosine_ssm(feats)
    half = _KERNEL_BEATS // 2
    ramp = np.arange(-half, half) + 0.5
    taper = np.exp(-0.5 * (ramp / (half / 2)) ** 2)
    kernel = np.outer(np.sign(ramp), np.sign(ramp)) * np.outer(taper, taper)
    padded = np.pad(ssm, half, mode="edge")
    novelty = np.array(
        [
            float(np.sum(padded[i : i + 2 * half, i : i + 2 * half] * kernel))
            for i in range(len(feats))
        ]
    )
    novelty = np.maximum(novelty, 0.0)
    top = float(novelty.max(initial=0.0))
    if top <= 0:
        return [0], {0: 1.0}
    novelty /= top
    peaks, props = sps.find_peaks(
        novelty,
        distance=_MIN_SECTION_BEATS,
        height=float(novelty.mean() + 0.5 * novelty.std()),
    )
    ranked = sorted(zip(peaks, props["peak_heights"], strict=True), key=lambda p: -p[1])
    chosen = sorted(ranked[: _MAX_SECTIONS - 1], key=lambda p: p[0])
    down_index = {int(b): i for i, b in enumerate(beats)}
    down_beats = [down_index[int(d)] for d in downbeats if int(d) in down_index]
    starts: dict[int, float] = {0: 1.0}
    for beat, height in chosen:
        if down_beats:
            nearest = min(down_beats, key=lambda d: abs(d - int(beat)))
            beat = nearest if abs(nearest - int(beat)) <= 2 else int(beat)
        if (
            int(beat) >= _MIN_SECTION_BEATS // 2
            and len(feats) - int(beat) >= _MIN_SECTION_BEATS // 2
        ):
            starts[int(beat)] = max(starts.get(int(beat), 0.0), float(height))
    order = sorted(starts)
    return order, starts


def _section_level(energy: Signal, start: int, end: int) -> float:
    fps = SAMPLE_RATE / HOP
    a = int(start / fps * ENERGY_RATE)
    b = max(a + 1, int(end / fps * ENERGY_RATE))
    seg = energy[a:b]
    return float(np.mean(seg)) if len(seg) else float(energy.mean())


def _label(starts_frames: list[int], frames: int, energy: Signal) -> tuple[MusicSection, ...]:
    edges = [*starts_frames, frames]
    spans = [(edges[i], edges[i + 1]) for i in range(len(starts_frames)) if edges[i + 1] > edges[i]]
    if not spans:
        spans = [(0, frames)]
    levels = [_section_level(energy, a, b) for a, b in spans]
    median = float(np.median(levels))
    labels = [SectionLabel.UNLABELED] * len(spans)
    evidence = [""] * len(spans)
    fps = SAMPLE_RATE / HOP

    def rise(a: int, b: int) -> float:
        lo = int(a / fps * ENERGY_RATE)
        hi = max(lo + 4, int(b / fps * ENERGY_RATE))
        seg = energy[lo:hi]
        quarter = max(1, len(seg) // 4)
        return float(np.mean(seg[-quarter:]) - np.mean(seg[:quarter])) if len(seg) >= 4 else 0.0

    if len(spans) > 1:
        if levels[0] < median - _LABEL_MARGIN_DB and spans[0][1] - spans[0][0] < 0.3 * frames:
            labels[0] = SectionLabel.INTRO
            evidence[0] = f"{median - levels[0]:.1f} dB below the track median at the start"
        if levels[-1] < median - _LABEL_MARGIN_DB:
            labels[-1] = SectionLabel.OUTRO
            evidence[-1] = f"{median - levels[-1]:.1f} dB below the track median at the end"
        for i in range(1, len(spans)):
            jump = levels[i] - levels[i - 1]
            if jump >= _DROP_JUMP_DB and labels[i] is SectionLabel.UNLABELED:
                labels[i] = SectionLabel.DROP
                evidence[i] = f"level jumps {jump:.1f} dB at the section start"
        for i in range(len(spans) - 1):
            r = rise(*spans[i])
            if (
                r >= _BUILD_RISE_DB
                and levels[i + 1] > levels[i]
                and labels[i] is SectionLabel.UNLABELED
            ):
                labels[i] = SectionLabel.BUILD
                evidence[i] = f"level rises {r:.1f} dB through the section into a louder one"
        loudest = int(np.argmax(levels))
        if (
            loudest > 0
            and levels[loudest] >= median + 2.0
            and labels[loudest] in (SectionLabel.UNLABELED, SectionLabel.DROP)
        ):
            labels[loudest] = SectionLabel.CLIMAX
            evidence[loudest] = (
                f"loudest section, {levels[loudest] - median:.1f} dB above the track median"
            )
    return tuple(
        MusicSection(
            start_frame=int(a),
            end_frame=int(b),
            label=labels[i],
            energy_db=round(levels[i], 1),
            evidence=evidence[i],
        )
        for i, (a, b) in enumerate(spans)
    )


def _loop(
    beats: NDArray[np.int64], downbeats: NDArray[np.int64], feats: Signal
) -> LoopRegion | None:
    if not len(downbeats) or len(feats) < _LOOP_MIN_GAP_BEATS + _LOOP_WINDOW_BEATS:
        return None
    index = {int(b): i for i, b in enumerate(beats)}
    starts = [index[int(d)] for d in downbeats if int(d) in index]
    starts = [s for s in starts if s + _LOOP_WINDOW_BEATS <= len(feats)]
    if len(starts) < 2:
        return None
    windows = np.array([feats[s : s + _LOOP_WINDOW_BEATS].ravel() for s in starts])
    sim = _cosine_ssm(windows)
    best: tuple[float, int, int] | None = None
    for i, a in enumerate(starts):
        for j in range(i + 1, len(starts)):
            b = starts[j]
            if b - a < _LOOP_MIN_GAP_BEATS:
                continue
            score = (float(sim[i, j]), b - a, -a)
            if best is None or score > (best[0], best[2] - best[1], -best[1]):
                best = (float(sim[i, j]), a, b)
    if best is None or best[0] < _LOOP_MIN_SIMILARITY:
        return None
    return LoopRegion(
        start_frame=int(beats[best[1]]),
        end_frame=int(beats[best[2]]),
        similarity=round(best[0], 3),
    )


def _edit_points(
    beats: NDArray[np.int64],
    downbeats: NDArray[np.int64],
    sections: tuple[MusicSection, ...],
    novelty: dict[int, float],
    loop: LoopRegion | None,
    energy: Signal,
    fps: float,
) -> tuple[EditPoint, ...]:
    points: list[EditPoint] = [
        EditPoint(
            kind=EditPointKind.TRACK_START,
            frame=int(beats[0]) if len(beats) else 0,
            strength=1.0,
        )
    ]
    section_frames = {s.start_frame for s in sections[1:]}
    for s in sections[1:]:
        points.append(
            EditPoint(
                kind=EditPointKind.SECTION_START,
                frame=s.start_frame,
                strength=round(novelty.get(s.start_frame, 0.5), 3),
            )
        )
    for i, frame in enumerate(downbeats):
        if i and i % 4 == 0 and int(frame) not in section_frames:
            points.append(
                EditPoint(kind=EditPointKind.PHRASE_START, frame=int(frame), strength=0.4)
            )
    if loop is not None:
        points.append(
            EditPoint(
                kind=EditPointKind.LOOP_START, frame=loop.start_frame, strength=loop.similarity
            )
        )
        points.append(
            EditPoint(kind=EditPointKind.LOOP_END, frame=loop.end_frame, strength=loop.similarity)
        )
    if len(energy):
        audible = np.flatnonzero(energy > float(energy.max()) - _AUDIBLE_BELOW_MAX_DB)
        end_index = int(audible[-1]) + 1 if len(audible) else len(energy)
        ending = round(end_index / ENERGY_RATE * fps)
        fade = _fade_start(energy[:end_index])
        if fade is not None:
            points.append(
                EditPoint(
                    kind=EditPointKind.FADE_OUT_START,
                    frame=round(fade / ENERGY_RATE * fps),
                    strength=1.0,
                )
            )
        points.append(EditPoint(kind=EditPointKind.ENDING, frame=ending, strength=1.0))
    return tuple(sorted(points, key=lambda p: (p.frame, p.kind.value)))


def _fade_start(energy: Signal) -> int | None:
    """Index where a final fade-out starts: the level then falls >= 12 dB over >= 2 s."""
    if len(energy) < 4 * ENERGY_RATE:
        return None
    tail = energy[-30 * ENERGY_RATE :]
    offset = len(energy) - len(tail)
    reference = float(np.median(energy))
    near = np.flatnonzero(tail >= reference - _LABEL_MARGIN_DB)
    if not len(near):
        return None
    start = int(near[-1])
    if len(tail) - start < 2 * ENERGY_RATE:
        return None
    if float(tail[start] - tail[-1]) < _FADE_DROP_DB:
        return None
    return offset + start


def seconds_of(frame: int) -> Fraction:
    return Fraction(frame * HOP, SAMPLE_RATE)
