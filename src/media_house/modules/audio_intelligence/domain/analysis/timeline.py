"""The Audio Intelligence Timeline: ONE synchronized, queryable result.

Speech (transcript + word timing), the continuous acoustic frames, events, pauses, baseline,
per-word/segment evidence and editing signals, all on the ORIGINAL media timeline in seconds.
Consumers use this object only; they never see WhisperX, Praat or FFmpeg.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import Baseline
from media_house.modules.audio_intelligence.domain.analysis.features import (
    EnergyChange,
    EnergySummary,
    PitchChange,
    PitchSummary,
    RateSummary,
    ScoredSignal,
    SegmentAcoustics,
    WordAcoustics,
)
from media_house.modules.audio_intelligence.domain.analysis.pauses import Pause
from media_house.modules.audio_intelligence.domain.analysis.scoring import (
    MOMENT,
    EditingSignal,
    WordSignals,
)
from media_house.modules.audio_intelligence.domain.frames import Fps, FrameRounding
from media_house.modules.audio_intelligence.domain.transcript import (
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TimelineWord:
    """A transcript word together with everything known about how it was said."""

    word: TranscriptWord
    acoustics: WordAcoustics | None
    signals: WordSignals

    # speech identity -------------------------------------------------------------------------
    @property
    def index(self) -> int:
        return self.word.index

    @property
    def segment_id(self) -> int:
        return self.word.segment_id

    @property
    def raw_word(self) -> str:
        return self.word.raw_word

    @property
    def normalized_word(self) -> str:
        return self.word.normalized_word

    @property
    def start(self) -> float:
        return self.word.start

    @property
    def end(self) -> float:
        return self.word.end

    @property
    def duration(self) -> float:
        return self.word.duration

    @property
    def confidence(self) -> float | None:
        return self.word.confidence

    # measured evidence -----------------------------------------------------------------------
    @property
    def pitch(self) -> PitchSummary | None:
        return self.acoustics.pitch if self.acoustics else None

    @property
    def energy(self) -> EnergySummary | None:
        return self.acoustics.energy if self.acoustics else None

    @property
    def rate(self) -> RateSummary | None:
        return self.acoustics.rate if self.acoustics else None

    @property
    def pitch_change(self) -> PitchChange | None:
        return self.acoustics.pitch_change if self.acoustics else None

    @property
    def energy_change(self) -> EnergyChange | None:
        return self.acoustics.energy_change if self.acoustics else None

    # derived scores (``None`` = not enough evidence) -------------------------------------------
    @property
    def emphasis_score(self) -> float | None:
        return self.signals.emphasis.score if self.signals.emphasis else None

    @property
    def expressiveness_score(self) -> float | None:
        return self.signals.expressiveness.score if self.signals.expressiveness else None

    @property
    def arousal_score(self) -> float | None:
        return self.signals.arousal.score if self.signals.arousal else None

    @property
    def local_contrast(self) -> float | None:
        return self.signals.local_contrast.score if self.signals.local_contrast else None

    @property
    def moment_score(self) -> float | None:
        return self.signals.moment.score if self.signals.moment else None


@dataclass(frozen=True, slots=True)
class TimelineSegment:
    segment: TranscriptSegment
    acoustics: SegmentAcoustics

    @property
    def id(self) -> int:
        return self.segment.id

    @property
    def start(self) -> float:
        return self.segment.start

    @property
    def end(self) -> float:
        return self.segment.end

    @property
    def text(self) -> str:
        return self.segment.text

    @property
    def words(self) -> tuple[TranscriptWord, ...]:
        return self.segment.words


@dataclass(frozen=True, slots=True)
class AcousticSample:
    """One acoustic frame with its baseline-relative values (computed on access)."""

    index: int
    start: float
    duration: float
    f0: float | None
    voiced: bool
    pitch_confidence: float | None
    rms_db: float
    loudness: float | None
    speech_activity: bool
    #: Pitch in semitones relative to the speaker's median (``None`` if unvoiced / no baseline).
    relative_pitch_st: float | None
    #: Level in dB relative to the speaker's median speech level.
    relative_energy_db: float | None


@dataclass(frozen=True, slots=True)
class AnalysisMetadata:
    """Provenance: which inputs and which versions of everything produced this timeline."""

    source_asset_id: str
    audio_asset_id: str
    duration: float
    audio_offset: float
    created_at: datetime
    analysis_version: int
    scoring: str
    #: Frames at or above this voicing strength count as voiced (from ``AnalysisConfig``).
    min_pitch_confidence: float
    #: ``{analyzer: version}`` of the measurements (pitch, energy, detectors, ...).
    analyzers: dict[str, str]
    #: Parameters analyzers actually used (e.g. the pitch range chosen by pass one).
    parameters: dict[str, float]
    #: Interpretation settings (``AnalysisConfig`` / ``ScoringConfig``) as JSON-like values.
    settings: dict[str, object]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioIntelligenceTimeline:
    metadata: AnalysisMetadata
    transcript: Transcript
    track: AcousticTrack
    baseline: Baseline
    events: tuple[AudioEvent, ...]
    pauses: tuple[Pause, ...]
    words: tuple[TimelineWord, ...]
    segments: tuple[TimelineSegment, ...]
    editing_signals: tuple[EditingSignal, ...]

    # --- basics ------------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        return self.metadata.duration

    @property
    def language(self) -> str:
        return self.transcript.language

    @property
    def acoustic_frames(self) -> Iterable[AcousticSample]:
        """Lazily every frame; use ``frames_between`` for a range."""
        return (self.sample(i) for i in range(len(self.track)))

    # --- speech ------------------------------------------------------------------------------
    def word_at(self, timestamp: float) -> TimelineWord | None:
        word = self.transcript.word_at(timestamp)
        return None if word is None else self.words[word.index]

    def words_between(self, start: float, end: float) -> tuple[TimelineWord, ...]:
        return tuple(self.words[w.index] for w in self.transcript.words_between(start, end))

    def segment_at(self, timestamp: float) -> TimelineSegment | None:
        segment = self.transcript.segment_at(timestamp)
        return None if segment is None else self.segments[segment.id]

    def segments_between(self, start: float, end: float) -> tuple[TimelineSegment, ...]:
        return tuple(self.segments[s.id] for s in self.transcript.segments_between(start, end))

    # --- acoustics ---------------------------------------------------------------------------
    def sample(self, index: int) -> AcousticSample:
        track = self.track
        hz = track.f0[index]
        confidence = track.pitch_confidence[index]
        voiced = track.is_voiced(index, self.metadata.min_pitch_confidence)
        loudness = track.loudness[index]
        energy_baseline = self.baseline.energy_db
        return AcousticSample(
            index=index,
            start=track.frame_start(index),
            duration=track.hop,
            f0=hz if math.isfinite(hz) else None,
            voiced=voiced,
            pitch_confidence=confidence if math.isfinite(confidence) else None,
            rms_db=track.rms_db[index],
            loudness=loudness if math.isfinite(loudness) else None,
            speech_activity=track.speech[index] >= 0.5,
            relative_pitch_st=self.baseline.pitch_relative_st(hz) if voiced else None,
            relative_energy_db=(
                track.rms_db[index] - energy_baseline.median if energy_baseline else None
            ),
        )

    def acoustic_at(self, timestamp: float) -> AcousticSample | None:
        index = self.track.index_at(timestamp)
        return None if index is None else self.sample(index)

    def frames_between(self, start: float, end: float) -> tuple[AcousticSample, ...]:
        return tuple(self.sample(i) for i in self.track.span(start, end))

    # --- events and pauses -------------------------------------------------------------------
    def events_between(
        self,
        start: float,
        end: float,
        kinds: Iterable[str] | None = None,
    ) -> tuple[AudioEvent, ...]:
        """Events overlapping ``[start, end)``, optionally only the given kinds."""
        wanted = None if kinds is None else frozenset(kinds)
        return tuple(
            e
            for e in self.events
            if e.end > start and e.start < end and (wanted is None or e.kind in wanted)
        )

    def pause_after(self, word: TimelineWord | TranscriptWord) -> Pause | None:
        return next((p for p in self.pauses if p.before_word == word.index), None)

    def pause_before(self, word: TimelineWord | TranscriptWord) -> Pause | None:
        return next((p for p in self.pauses if p.after_word == word.index), None)

    # --- editing signals ---------------------------------------------------------------------
    def signals_between(
        self,
        start: float,
        end: float,
        kind: str | None = None,
    ) -> tuple[EditingSignal, ...]:
        return tuple(
            s
            for s in self.editing_signals
            if s.end >= start and s.start <= end and (kind is None or s.kind == kind)
        )

    def moment_at(self, timestamp: float) -> EditingSignal | None:
        """The ``moment`` signal of the word being spoken at ``timestamp`` (else ``None``)."""
        word = self.transcript.word_at(timestamp)
        if word is None:
            return None
        return next(
            (s for s in self.editing_signals if s.kind == MOMENT and s.anchor_id == word.index),
            None,
        )

    def signal_curve(self, kind: str, hop: float = 0.1) -> tuple[tuple[float, float], ...]:
        """``(time, score)`` samples of one signal: the strongest anchor covering each instant;
        instants no anchor covers are omitted (unknown), never reported as 0."""
        anchors = [s for s in self.editing_signals if s.kind == kind]
        curve: list[tuple[float, float]] = []
        for i in range(math.ceil(self.duration / hop)):
            t = i * hop
            covering = [s.score for s in anchors if s.start <= t < max(s.end, s.start + 1e-9)]
            if covering:
                curve.append((t, max(covering)))
        return tuple(curve)

    # --- frames and explanation --------------------------------------------------------------
    @staticmethod
    def to_frame(
        timestamp: float,
        fps: Fps,
        rounding: FrameRounding = FrameRounding.FLOOR,
    ) -> int:
        return Transcript.to_frame(timestamp, fps, rounding)

    def explain_word(self, word: TimelineWord) -> dict[str, dict[str, float]]:
        """Why the word got its scores: each score's contributing evidence (each 0..1)."""
        signals = word.signals
        parts: dict[str, ScoredSignal | None] = {
            "emphasis": signals.emphasis,
            "expressiveness": signals.expressiveness,
            "arousal": signals.arousal,
            "local_contrast": signals.local_contrast,
            "moment": signals.moment,
        }
        return {
            name: {"score": scored.score, **scored.contributors}
            for name, scored in parts.items()
            if scored is not None
        }
