"""The beat tracker on synthetic tracks with known tempo, beats and downbeats.

Measured on this suite (see the README): tempo within 2 % on the clear cases below, beat
F-measure (+-70 ms) >= 0.95, downbeat F-measure >= 0.9. Octave errors (half/double tempo) are
expected on some patterns and are tested as such, not hidden.
"""

import numpy as np
import pytest

from media_house.modules.music_and_sound_design.domain.analysis import Loudness
from media_house.modules.music_and_sound_design.domain.values import EditPointKind, SectionLabel
from media_house.modules.music_and_sound_design.infrastructure.music_signal import (
    HOP,
    SAMPLE_RATE,
    measure_music_signal,
)

SR = SAMPLE_RATE
_LOUD = Loudness(integrated_lufs=-14.0, loudness_range_lu=3.0, true_peak_dbtp=-1.0)


def _place(x: np.ndarray, sound: np.ndarray, at: float) -> None:
    start = int(at * SR)
    end = min(len(x), start + len(sound))
    x[start:end] += sound[: end - start]


def _click() -> np.ndarray:
    t = np.arange(400) / SR
    return np.sin(2 * np.pi * 1000 * t) * np.exp(-t * 60)


def _kick() -> np.ndarray:
    t = np.arange(4000) / SR
    return np.sin(2 * np.pi * (60 + 80 * np.exp(-t * 40)) * t) * np.exp(-t * 12)


def _snare(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(3000) / SR
    return rng.standard_normal(len(t)) * np.exp(-t * 25) * 0.6


def track(
    bpm: float, *, seconds: float = 60.0, drums: bool = False, offset: float = 0.37
) -> tuple[np.ndarray, np.ndarray]:
    """A click track (accented downbeats) or a kick/snare/hat pattern, with bar-wise chords."""
    rng = np.random.default_rng(1)
    x = np.zeros(int(seconds * SR))
    period = 60.0 / bpm
    beats = np.arange(offset, seconds - 0.5, period)
    for i, t in enumerate(beats):
        if drums:
            _place(x, _kick() if i % 2 == 0 else _snare(rng), t)
            _place(x, rng.standard_normal(600) * np.exp(-np.arange(600) / SR * 80) * 0.1, t)
        else:
            _place(x, _click() * (1.0 if i % 4 == 0 else 0.6), t)
    tt = np.arange(len(x)) / SR
    roots = np.array([261.6, 349.2, 392.0, 220.0])[((tt - offset) // (4 * period)).astype(int) % 4]
    x += 0.08 * (np.sin(2 * np.pi * roots * tt) + np.sin(2 * np.pi * roots * 1.5 * tt))
    return x / np.abs(x).max() * 0.8, beats


def f_measure(estimated: np.ndarray, reference: np.ndarray, tolerance: float = 0.07) -> float:
    used: set[int] = set()
    hits = 0
    for r in reference:
        for j, e in enumerate(estimated):
            if j not in used and abs(e - r) <= tolerance:
                used.add(j)
                hits += 1
                break
    if not hits:
        return 0.0
    precision, recall = hits / len(estimated), hits / len(reference)
    return 2 * precision * recall / (precision + recall)


@pytest.mark.parametrize(("bpm", "drums"), [(90, False), (120, False), (128, True), (140, True)])
def test_tempo_beats_and_downbeats_are_measured_on_clear_tracks(bpm: float, drums: bool) -> None:
    x, reference = track(bpm, drums=drums)

    m = measure_music_signal(x, loudness=_LOUD)

    assert m.tempo is not None
    assert abs(m.tempo.bpm - bpm) / bpm < 0.02
    assert m.tempo.confidence > 0.3
    assert m.tempo.stability == 1.0
    beats = np.array(m.beat_frames) * HOP / SR
    downbeats = np.array(m.downbeat_frames) * HOP / SR
    assert f_measure(beats, reference) >= 0.95
    assert f_measure(downbeats, reference[::4]) >= 0.9
    assert np.median([min(abs(beats - r)) for r in reference[2:-2]]) < 0.02


def test_a_tempo_may_be_read_at_double_speed_and_is_never_off_the_octave_grid() -> None:
    x, _ = track(60, drums=True)  # kick/snare alternation: 60 or 120 are both defensible

    m = measure_music_signal(x, loudness=_LOUD)

    assert m.tempo is not None
    ratio = m.tempo.bpm / 60
    assert min(abs(ratio - k) / k for k in (1.0, 2.0)) < 0.02
    half, double = m.tempo.octave_alternatives
    assert half == pytest.approx(m.tempo.bpm / 2, abs=0.01)
    assert double == pytest.approx(m.tempo.bpm * 2, abs=0.01)


def test_structure_loop_edit_points_and_honest_gaps() -> None:
    x, _ = track(120)
    quiet = x.copy()
    quiet[: 8 * SR] *= 0.1  # a quiet intro
    quiet[-8 * SR :] *= 0.1  # and a quiet ending

    m = measure_music_signal(quiet, loudness=_LOUD)

    assert m.sections[0].start_frame == 0
    assert m.sections[-1].end_frame >= m.sections[-1].start_frame
    labels = {s.label for s in m.sections}
    assert SectionLabel.INTRO in labels and SectionLabel.OUTRO in labels
    assert all(s.evidence for s in m.sections if s.label is not SectionLabel.UNLABELED)
    assert m.loop is not None and m.loop.similarity >= 0.8
    assert m.loop.start_frame in m.downbeat_frames and m.loop.end_frame in m.downbeat_frames
    kinds = {p.kind for p in m.edit_points}
    assert {EditPointKind.TRACK_START, EditPointKind.ENDING, EditPointKind.LOOP_START} <= kinds
    assert m.vocal_presence == "not_available"
    assert "vocal_presence" in {u.what for u in m.not_available}
    assert m.meter_assumed == 4


def test_too_short_or_silent_audio_has_no_invented_tempo() -> None:
    m = measure_music_signal(np.zeros(3 * SR), loudness=_LOUD)

    assert m.tempo is None
    assert m.beat_frames == ()
    assert {"tempo", "downbeats"} <= {u.what for u in m.not_available}
