"""The real acoustic extractor (Praat, NumPy, SciPy) on synthetic voices with known properties."""

import math
import random
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    PITCH_RISE,
    AcousticMeasurements,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import estimate_baseline
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AcousticConfig,
    AnalysisConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.detection import detect_acoustic_events
from media_house.modules.audio_intelligence.domain.analysis.serialization import FRAME_PRECISION
from media_house.modules.audio_intelligence.infrastructure.acoustic_extractor import (
    AcousticExtractor,
    AudioSignal,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError
from tests.support.analysis_fakes import harmonic, write_wav

pytestmark = pytest.mark.integration

CONFIG = AcousticConfig()


def measure(
    tmp_path: Path,
    samples: list[float],
    extractor: AcousticExtractor | None = None,
) -> AcousticMeasurements:
    wav = write_wav(tmp_path / "voice.wav", samples)
    return (extractor or AcousticExtractor()).measure(wav, CONFIG, CancellationToken())


def frames_in(track: AcousticTrack, start: float, end: float) -> range:
    return track.span(start, end)


def test_steady_voice_has_the_right_pitch_and_is_marked_as_speech(tmp_path: Path) -> None:
    result = measure(tmp_path, harmonic(3.0, lambda _t: 150.0, lambda _t: 0.3))
    track = result.track

    voiced = [track.f0[i] for i in frames_in(track, 0.3, 2.7) if math.isfinite(track.f0[i])]
    assert len(voiced) > 0.9 * len(frames_in(track, 0.3, 2.7))
    assert np.median(voiced) == pytest.approx(150.0, abs=3.0)
    assert all(track.speech[i] == 1.0 for i in frames_in(track, 0.3, 2.7))
    assert len(track) == pytest.approx(150, abs=1)
    assert result.parameters["pitch_floor"] < 150.0 < result.parameters["pitch_ceiling"]
    assert "pitch" in result.identity
    assert result.identity["pitch"].startswith("parselmouth-")


def steady(hz: float) -> Callable[[float], float]:
    return lambda _t: hz


def test_a_deep_and_a_high_voice_are_both_tracked(tmp_path: Path) -> None:
    for hz in (95.0, 260.0):
        track = measure(tmp_path, harmonic(3.0, steady(hz), lambda _t: 0.3)).track

        voiced = [track.f0[i] for i in frames_in(track, 0.3, 2.7) if math.isfinite(track.f0[i])]
        assert np.median(voiced) == pytest.approx(hz, rel=0.04)


def test_silence_has_no_pitch_no_speech_and_no_error(tmp_path: Path) -> None:
    track = measure(tmp_path, [0.0] * 32_000).track

    assert all(math.isnan(v) for v in track.f0)  # unknown, not 0
    assert all(v == 0.0 for v in track.speech)
    assert min(track.rms_db) == max(track.rms_db) == -120.0
    assert len(track) == 100


def test_noise_is_mostly_unvoiced(tmp_path: Path) -> None:
    rng = random.Random(7)  # noqa: S311  # seeded test noise, not security
    track = measure(tmp_path, [rng.uniform(-0.2, 0.2) for _ in range(48_000)]).track

    voiced = sum(1 for v in track.f0 if math.isfinite(v))
    assert voiced < 0.15 * len(track)


def test_gaps_between_voiced_stretches_are_unvoiced_and_inactive(tmp_path: Path) -> None:
    def amplitude(t: float) -> float:
        return 0.3 if t < 1.0 or t >= 2.0 else 0.0

    track = measure(tmp_path, harmonic(3.0, lambda _t: 150.0, amplitude)).track

    assert all(math.isnan(track.f0[i]) for i in frames_in(track, 1.15, 1.85))
    assert all(track.speech[i] == 0.0 for i in frames_in(track, 1.15, 1.85))
    assert all(track.speech[i] == 1.0 for i in frames_in(track, 0.3, 0.9))
    assert all(track.speech[i] == 1.0 for i in frames_in(track, 2.2, 2.8))


def test_a_pitch_rise_is_visible_in_the_frames_and_becomes_an_event(tmp_path: Path) -> None:
    def f0(t: float) -> float:
        if t < 1.5:
            return 120.0
        return 120.0 + 80.0 * min(1.0, (t - 1.5) / 0.3)  # +8.5 semitones in 0.3 s

    track = measure(tmp_path, harmonic(3.5, f0, lambda _t: 0.3)).track
    baseline = estimate_baseline(track, AnalysisConfig())
    events = [
        e for e in detect_acoustic_events(track, baseline, AnalysisConfig()) if e.kind == PITCH_RISE
    ]

    before = [track.f0[i] for i in frames_in(track, 1.0, 1.4) if math.isfinite(track.f0[i])]
    after = [track.f0[i] for i in frames_in(track, 2.1, 2.6) if math.isfinite(track.f0[i])]
    assert np.median(before) == pytest.approx(120.0, abs=3.0)
    assert np.median(after) == pytest.approx(200.0, abs=5.0)
    assert len(events) == 1
    assert 1.3 <= events[0].start <= 1.9
    assert events[0].strength > 0.7


def test_level_changes_are_measured_in_db_and_loudness(tmp_path: Path) -> None:
    track = measure(
        tmp_path,
        harmonic(3.0, lambda _t: 150.0, lambda t: 0.05 if t < 1.5 else 0.5),
    ).track

    quiet = np.mean([track.rms_db[i] for i in frames_in(track, 0.5, 1.2)])
    loud = np.mean([track.rms_db[i] for i in frames_in(track, 1.9, 2.8)])
    quiet_lufs = np.mean([track.loudness[i] for i in frames_in(track, 0.7, 1.2)])
    loud_lufs = np.mean([track.loudness[i] for i in frames_in(track, 2.0, 2.8)])
    assert loud - quiet == pytest.approx(20.0, abs=1.0)
    assert loud_lufs - quiet_lufs == pytest.approx(20.0, abs=1.5)


def test_loudness_follows_bs1770_for_a_reference_tone(tmp_path: Path) -> None:
    rate = 16_000
    tone = [0.1 * math.sin(2 * math.pi * 997 * i / rate) for i in range(3 * rate)]

    track = measure(tmp_path, tone).track

    middle = np.mean([track.loudness[i] for i in frames_in(track, 1.0, 2.0)])
    assert middle == pytest.approx(-23.0, abs=0.5)  # a 997 Hz sine at -20 dBFS peak


def test_pluggable_detectors_add_events_and_failures_are_reported(tmp_path: Path) -> None:
    class Laughter:
        name = "fake-laughter"
        version = "9"

        def detect(self, signal: AudioSignal) -> list[AudioEvent]:
            assert signal.sample_rate == 16_000
            return [AudioEvent("laughter", 1.0, 1.5, 0.8, confidence=0.9, source=self.name)]

    class Broken:
        name = "broken"
        version = "1"

        def detect(self, signal: AudioSignal) -> list[AudioEvent]:
            raise RuntimeError("model exploded")

    samples = harmonic(2.0, lambda _t: 150.0, lambda _t: 0.3)

    result = measure(tmp_path, samples, AcousticExtractor(detectors=(Laughter(),)))
    assert [(e.kind, e.confidence) for e in result.events] == [("laughter", 0.9)]
    assert result.identity["detector:fake-laughter"] == "9"
    with pytest.raises(ExternalSystemError, match="broken"):
        measure(tmp_path, samples, AcousticExtractor(detectors=(Broken(),)))


def test_measurement_is_deterministic_and_stored_at_the_documented_precision(
    tmp_path: Path,
) -> None:
    samples = harmonic(2.0, lambda t: 150.0 + 20.0 * t, lambda _t: 0.3)

    first, second = measure(tmp_path, samples).track, measure(tmp_path, samples).track

    for name, column in first.columns().items():
        assert list(column) == list(second.columns()[name]) or all(
            a == b or (math.isnan(a) and math.isnan(b))
            for a, b in zip(column, second.columns()[name], strict=True)
        )
        digits = FRAME_PRECISION[name]
        assert all(math.isnan(v) or round(v, digits) == v for v in column)


def test_very_short_audio_degrades_gracefully(tmp_path: Path) -> None:
    result = measure(tmp_path, harmonic(0.1, lambda _t: 150.0, lambda _t: 0.3))

    assert all(math.isnan(v) for v in result.track.f0)
    assert any("too short" in w for w in result.warnings)


def test_cancellation_is_honoured(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "voice.wav", [0.0] * 16_000)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelledError):
        AcousticExtractor().measure(wav, CONFIG, token)
