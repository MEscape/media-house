"""Energy rhythm: a regular pulse in the level of the sound, from the acoustic track.

This is a BASELINE, not music analysis. It looks at how the frame level (``rms_db``, measured once
for every recording) rises and falls, and asks whether the rises repeat at a steady interval.
Where they do, it reports a tempo, how regular the pulse is (``strength``) and where the pulses
fall. It needs no extra decoding and no extra library.

Honest limits, which every consumer must keep:

* Only stretches WITHOUT speech are examined. Speech has its own syllable rhythm that would read as
  a tempo; music playing under speech is therefore not detected here.
* The tempo comes from the loudness envelope alone: a half or double tempo can fit equally well, and
  a steady machine noise or footsteps will read as a pulse. ``strength`` says how regular the pulse
  is, not whether it is music.
* Beat times are the best-fitting regular grid of the pulses, not individually detected beats.
"""

import math
from array import array
from dataclasses import dataclass

from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticTrack
from media_house.shared.errors import InvariantViolation

_FLOOR_DB = -80.0


@dataclass(frozen=True, slots=True)
class RhythmConfig:
    """How rhythm is estimated. Every number is a field, none is hidden in the algorithm."""

    #: Seconds of sound examined at once, and how far the window moves.
    window: float = 10.0
    step: float = 5.0
    min_bpm: float = 60.0
    max_bpm: float = 180.0
    #: A window where speech is active in more than this share of frames is skipped.
    max_speech_share: float = 0.25
    #: Pulses are rises of at least this many dB in one frame.
    min_rise_db: float = 3.0
    #: Regularity from which a pulse is reported (0..1).
    min_strength: float = 0.3
    #: Frames of the window that must carry a measured level.
    min_measured_share: float = 0.9

    def __post_init__(self) -> None:
        if not 0 < self.min_bpm < self.max_bpm:
            raise InvariantViolation("Rhythm needs 0 < min_bpm < max_bpm")
        if self.window <= 0 or self.step <= 0 or self.step > self.window:
            raise InvariantViolation("Rhythm window and step must be positive, step <= window")
        if not 0.0 <= self.max_speech_share <= 1.0 or not 0.0 <= self.min_strength <= 1.0:
            raise InvariantViolation("Rhythm shares and strength must lie within [0, 1]")


@dataclass(frozen=True, slots=True)
class RhythmWindow:
    """A stretch of sound whose level pulses at a steady tempo."""

    start: float
    end: float
    tempo_bpm: float
    #: Regularity of the pulse in [0, 1] (1 = perfectly periodic). Not a probability.
    strength: float
    #: The best-fitting regular grid of pulses inside the window, in seconds on the media timeline.
    beats: tuple[float, ...]
    #: How many frames carried a rise (the evidence the tempo rests on).
    pulses: int


def estimate_rhythm(
    track: AcousticTrack, config: RhythmConfig | None = None
) -> tuple[RhythmWindow, ...]:
    """Windows of the track whose level shows a regular pulse. Empty when none does."""
    cfg = config or RhythmConfig()
    rise = _rises(track, cfg.min_rise_db)
    per = track.hop
    size = max(8, round(cfg.window / per))
    step = max(1, round(cfg.step / per))
    out: list[RhythmWindow] = []
    for first in range(0, max(1, len(track) - size + 1), step):
        last = min(len(track), first + size)
        found = _window(track, rise, first, last, cfg)
        if found is not None:
            out.append(found)
    return tuple(out)


def _rises(track: AcousticTrack, minimum: float) -> array[float]:
    """Positive level change from one frame to the next; unmeasured frames contribute nothing."""
    values = array("d", [0.0]) * len(track)
    previous = _FLOOR_DB
    for i in range(len(track)):
        level = track.rms_db[i]
        if not math.isfinite(level):
            previous = _FLOOR_DB
            continue
        change = level - previous
        values[i] = change if change >= minimum else 0.0
        previous = level
    return values


def _window(
    track: AcousticTrack, rise: array[float], first: int, last: int, cfg: RhythmConfig
) -> RhythmWindow | None:
    count = last - first
    speech = sum(1 for i in range(first, last) if track.speech[i] >= 0.5)
    measured = sum(1 for i in range(first, last) if math.isfinite(track.rms_db[i]))
    if speech / count > cfg.max_speech_share or measured / count < cfg.min_measured_share:
        return None
    segment = [rise[i] for i in range(first, last)]
    total = sum(segment)
    pulses = sum(1 for v in segment if v > 0)
    if total <= 0 or pulses < 6:
        return None
    mean = total / count
    centred = [v - mean for v in segment]
    energy = sum(v * v for v in centred)
    if energy <= 0:
        return None
    hop = track.hop
    lag_low = max(1, math.floor(60.0 / cfg.max_bpm / hop))
    lag_high = min(count // 2, math.ceil(60.0 / cfg.min_bpm / hop))
    if lag_high <= lag_low:
        return None
    correlation = {
        lag: sum(centred[i] * centred[i + lag] for i in range(count - lag)) / energy
        for lag in range(lag_low - 1, lag_high + 2)
        if 0 < lag < count
    }
    best = max(range(lag_low, lag_high + 1), key=lambda lag: correlation.get(lag, -1.0))
    strength = max(0.0, min(1.0, correlation.get(best, 0.0)))
    if strength < cfg.min_strength:
        return None
    period = _refined(correlation, best) * hop
    tempo = 60.0 / period
    start = track.frame_start(first)
    return RhythmWindow(
        start=start,
        end=track.frame_start(last),
        tempo_bpm=round(tempo, 2),
        strength=round(strength, 3),
        beats=_grid(segment, hop, start, period),
        pulses=pulses,
    )


def _refined(correlation: dict[int, float], lag: int) -> float:
    """Parabolic refinement of the correlation peak, so the tempo is finer than one frame."""
    before, centre, after = (
        correlation.get(lag - 1),
        correlation.get(lag),
        correlation.get(lag + 1),
    )
    if before is None or centre is None or after is None:
        return float(lag)
    denominator = before - 2 * centre + after
    if denominator >= 0:
        return float(lag)
    return lag + 0.5 * (before - after) / denominator


def _grid(segment: list[float], hop: float, start: float, period: float) -> tuple[float, ...]:
    """The phase of a regular grid that collects the most pulse energy, as beat times."""
    frames = period / hop
    best_phase, best_score = 0.0, -1.0
    for step in range(max(1, round(frames))):
        phase = float(step)
        score = 0.0
        position = phase
        while position < len(segment):
            score += segment[int(position)]
            position += frames
        if score > best_score:
            best_phase, best_score = phase, score
    beats: list[float] = []
    position = best_phase
    while position < len(segment):
        beats.append(round(start + position * hop, 4))
        position += frames
    return tuple(beats)
