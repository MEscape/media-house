"""Builders for audio-intelligence analysis tests: synthetic acoustic tracks and timelines."""

import math
import struct
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    AcousticMeasurements,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AudioIntelligenceConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.fusion import build_timeline
from media_house.modules.audio_intelligence.domain.analysis.scoring import HeuristicScorer
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_intelligence.domain.builder import BuildContext, build_transcript
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_FORCED,
    RawSegment,
    RawTranscription,
    RawWord,
)
from tests.support.audio_fakes import CREATED

NAN = math.nan
SILENT_DB = -90.0

type Word = tuple[str, float, float]


@dataclass(frozen=True, slots=True)
class Delivery:
    """How one word is spoken: pitch in Hz (``None`` = unvoiced) and level in dBFS."""

    f0: float | None = 150.0
    db: float = -25.0


def make_track(
    *,
    seconds: float,
    hop: float = 0.02,
    origin: float = 0.0,
    f0: Callable[[float], float] | None = None,
    db: Callable[[float], float] | None = None,
    speech: Callable[[float], bool] | None = None,
    confidence: float = 0.9,
) -> AcousticTrack:
    """Frame-synchronous track from functions of time (evaluated at frame centres)."""
    count = round(seconds / hop)
    centres = [(i + 0.5) * hop for i in range(count)]
    f0_fn = f0 or (lambda _t: NAN)
    db_fn = db or (lambda _t: SILENT_DB)
    speech_fn = speech or (lambda t: db_fn(t) > -50.0)
    f0_values = [f0_fn(t) for t in centres]
    return AcousticTrack.of(
        origin=origin,
        hop=hop,
        f0=f0_values,
        pitch_confidence=[NAN if math.isnan(v) else confidence for v in f0_values],
        rms_db=[db_fn(t) for t in centres],
        loudness=[db_fn(t) - 0.7 for t in centres],
        speech=[1.0 if speech_fn(t) else 0.0 for t in centres],
    )


def speech_track(
    words: Sequence[Word],
    delivery: Mapping[int, Delivery] | None = None,
    *,
    seconds: float | None = None,
    default: Delivery = Delivery(),  # noqa: B008
    origin: float = 0.0,
) -> AcousticTrack:
    """A track that is voiced exactly where words are, with per-word pitch/level."""
    delivery = delivery or {}

    def word_at(t: float) -> int | None:
        return next((i for i, (_, s, e) in enumerate(words) if s <= t < e), None)

    def f0(t: float) -> float:
        i = word_at(t)
        value = default.f0 if i is None else delivery.get(i, default).f0
        return NAN if i is None or value is None else value

    def db(t: float) -> float:
        i = word_at(t)
        return SILENT_DB if i is None else delivery.get(i, default).db

    end = seconds if seconds is not None else (words[-1][2] + 1.0 if words else 1.0)
    return make_track(seconds=end, f0=f0, db=db, origin=origin)


def raw_segments(
    groups: Sequence[Sequence[Word]],
    *,
    language: str = "en",
) -> RawTranscription:
    segments = tuple(
        RawSegment(
            g[0][1],
            g[-1][2],
            " ".join(w for w, _, _ in g),
            tuple(RawWord(w, s, e, 0.9) for w, s, e in g),
        )
        for g in groups
        if g
    )
    return RawTranscription(
        segments=segments,
        language=language,
        language_detected=False,
        engine="fake",
        engine_version="1",
        model="large-v3",
        model_source=None,
        alignment_engine="fake",
        alignment_model="fake-aligner",
        alignment_method=ALIGNMENT_FORCED,
        device="cpu",
        compute_type="int8",
    )


def measurements(
    track: AcousticTrack,
    events: Sequence[AudioEvent] = (),
    identity: Mapping[str, str] | None = None,
) -> AcousticMeasurements:
    return AcousticMeasurements(
        track, dict(identity or {"pitch": "synthetic-1"}), {}, tuple(events)
    )


def timeline(
    groups: Sequence[Sequence[Word]],
    track: AcousticTrack | None = None,
    *,
    config: AudioIntelligenceConfig | None = None,
    duration: float | None = None,
    offset: float = 0.0,
    events: Sequence[AudioEvent] = (),
    delivery: Mapping[int, Delivery] | None = None,
) -> AudioIntelligenceTimeline:
    """Fuse scripted words + a synthetic track. All input times are PREPARED-audio times."""
    config = config or AudioIntelligenceConfig()
    flat = [w for g in groups for w in g]
    total = duration if duration is not None else (flat[-1][2] + 1.0 if flat else 1.0)
    transcript = build_transcript(
        raw_segments(groups),
        BuildContext("src", "aud", total, offset, 16_000, 1, 1, CREATED),
    )
    # Words and the track are in PREPARED-audio time; ``offset`` maps both to the source.
    track = track or speech_track(flat, delivery, seconds=total - offset)
    return build_timeline(
        transcript=transcript,
        measurements=measurements(track, events),
        config=config,
        scorer=HeuristicScorer(config.scoring),
        audio_offset=offset,
        created_at=CREATED,
    )


def write_wav(path: Path, samples: Sequence[float], rate: int = 16_000) -> Path:
    """Mono 16-bit PCM from floats in [-1, 1]."""
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(struct.pack("<h", max(-32768, min(32767, round(s * 32767)))) for s in samples),
        )
    return path


def harmonic(
    seconds: float,
    f0: Callable[[float], float],
    amplitude: Callable[[float], float],
    rate: int = 16_000,
) -> list[float]:
    """Voice-like signal: 6 harmonics of a (possibly gliding) F0; 0 amplitude = silence."""
    samples: list[float] = []
    phase = 0.0
    for i in range(round(seconds * rate)):
        t = i / rate
        amp = amplitude(t)
        if amp <= 0:
            samples.append(0.0)
            continue
        phase += 2 * math.pi * f0(t) / rate
        samples.append(
            amp * sum(math.sin(k * phase) / k for k in range(1, 7)) / 2.0,
        )
    return samples
