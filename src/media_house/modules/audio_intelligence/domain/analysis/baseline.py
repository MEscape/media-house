"""Speaker/recording baseline: what is "normal" for THIS audio.

Robust statistics only (median, percentiles, MAD): a few shouts or octave errors must not move
the baseline. Pitch is expressed in semitones relative to the speaker's median F0, so a deep and
a high voice are judged by the same relative scale. ``scope`` is ``"global"`` today; with speaker
diarization the same structure can be computed per speaker (``speaker`` set) without changing
consumers.
"""

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.audio_intelligence.domain.analysis import stats
from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticTrack
from media_house.modules.audio_intelligence.domain.analysis.config import AnalysisConfig

_QUANTILE_STEP = 5
_SIGMA_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class Distribution:
    """Robust description of one measurement's normal range."""

    median: float
    p10: float
    p90: float
    #: Standard-deviation equivalent from the MAD; may be 0 for constant signals.
    sigma: float
    count: int

    def z(self, value: float) -> float | None:
        """Robust z-score; ``None`` when the baseline has no spread to compare against."""
        return (value - self.median) / self.sigma if self.sigma > _SIGMA_EPSILON else None


def describe(values: Sequence[float]) -> Distribution | None:
    data = stats.finite(values)
    if not data:
        return None
    center = stats.median(data)
    low, high = stats.percentile(data, 10), stats.percentile(data, 90)
    if center is None or low is None or high is None:
        return None
    return Distribution(center, low, high, stats.robust_sigma(data, center) or 0.0, len(data))


@dataclass(frozen=True, slots=True)
class Baseline:
    scope: str = "global"
    speaker: str | None = None
    pitch_median_hz: float | None = None
    #: Semitones relative to ``pitch_median_hz`` over voiced speech frames.
    pitch_st: Distribution | None = None
    #: Hz at percentiles 0, 5, ..., 100 of the voiced frames (for percentile lookups).
    pitch_quantiles_hz: tuple[float, ...] = ()
    energy_db: Distribution | None = None
    loudness: Distribution | None = None
    #: Words per second, from local rates (filled in once words are known).
    speech_rate: Distribution | None = None
    #: Seconds per character of a word (filled in once words are known).
    word_duration_per_char: Distribution | None = None
    voiced_frames: int = 0
    speech_frames: int = 0

    def pitch_relative_st(self, hz: float) -> float | None:
        if self.pitch_median_hz is None or hz <= 0:
            return None
        return stats.semitones(hz, self.pitch_median_hz)

    def pitch_z(self, hz: float) -> float | None:
        relative = self.pitch_relative_st(hz)
        if relative is None or self.pitch_st is None:
            return None
        return self.pitch_st.z(relative)

    def pitch_percentile(self, hz: float) -> float | None:
        """Share (0..1) of the speaker's voiced frames at or below ``hz``."""
        table = self.pitch_quantiles_hz
        if len(table) < 2 or not math.isfinite(hz):
            return None
        if hz <= table[0]:
            return 0.0
        if hz >= table[-1]:
            return 1.0
        i = bisect_left(table, hz)
        span = table[i] - table[i - 1]
        fraction = 0.0 if span <= 0 else (hz - table[i - 1]) / span
        return ((i - 1) + fraction) * _QUANTILE_STEP / 100.0


def estimate_baseline(track: AcousticTrack, config: AnalysisConfig) -> Baseline:
    """Pitch, energy and loudness baselines from the measured frames (speech frames only)."""
    voiced_hz: list[float] = []
    speech_db: list[float] = []
    speech_loudness: list[float] = []
    for i in range(len(track)):
        if track.speech[i] < 0.5:
            continue
        speech_db.append(track.rms_db[i])
        speech_loudness.append(track.loudness[i])
        if track.is_voiced(i, config.min_pitch_confidence):
            voiced_hz.append(track.f0[i])

    pitch_median_hz: float | None = None
    pitch_st: Distribution | None = None
    quantiles: tuple[float, ...] = ()
    if len(voiced_hz) >= config.min_baseline_voiced_frames:
        pitch_median_hz = stats.median(voiced_hz)
        pitch_st = (
            describe([stats.semitones(f, pitch_median_hz) for f in voiced_hz])
            if pitch_median_hz
            else None
        )
        quantiles = tuple(
            stats.percentile(voiced_hz, p) or 0.0 for p in range(0, 101, _QUANTILE_STEP)
        )
    return Baseline(
        pitch_median_hz=pitch_median_hz,
        pitch_st=pitch_st,
        pitch_quantiles_hz=quantiles,
        energy_db=describe(speech_db),
        loudness=describe(speech_loudness),
        voiced_frames=len(voiced_hz),
        speech_frames=len(speech_db),
    )
