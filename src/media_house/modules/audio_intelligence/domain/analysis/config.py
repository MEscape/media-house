"""Configuration and versions of the audio-intelligence analysis.

Three kinds of settings, with different reuse consequences:

* ``AcousticConfig``    -> how raw frames are MEASURED (changing it re-measures the audio)
* ``AnalysisConfig``    -> how measurements are INTERPRETED (baselines, events, pauses, windows)
* ``ScoringConfig``     -> how evidence becomes SCORES (weights and scales)

Measured frames and the transcript are stored as their own assets, so changing only
``AnalysisConfig`` or ``ScoringConfig`` recomputes the timeline without re-measuring or
re-transcribing.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.audio_intelligence.domain.values import (
    JsonValue,
    TranscriptionConfig,
)
from media_house.shared.errors import InvariantViolation

MEASUREMENTS_OPERATION = "acoustic_measurements"
#: Bump when frame extraction changes in a way that alters measured values.
MEASUREMENTS_VERSION = 1
ANALYSIS_OPERATION = "audio_intelligence"
#: Bump when the derivation pipeline (baselines, events, features, fusion) changes.
ANALYSIS_VERSION = 1


@dataclass(frozen=True, slots=True)
class AcousticConfig:
    """Measurement of the continuous acoustic timeline (one frame every ``hop`` seconds)."""

    hop: float = 0.02
    #: ``None`` = estimate the speaker's range first, then track within it (two-pass).
    pitch_floor: float | None = None
    pitch_ceiling: float | None = None
    voicing_threshold: float = 0.45
    #: Window of the momentary loudness measurement (seconds, BS.1770 uses 0.4).
    loudness_window: float = 0.4
    #: Speech activity = RMS this far above the estimated noise floor.
    activity_margin_db: float = 10.0

    def __post_init__(self) -> None:
        if not 0.005 <= self.hop <= 0.1:
            raise InvariantViolation("Acoustic hop must be 5-100 ms")
        floor, ceiling = self.pitch_floor, self.pitch_ceiling
        if (floor is not None and floor < 30) or (ceiling is not None and ceiling > 1200):
            raise InvariantViolation("Pitch range must lie within 30-1200 Hz")
        if floor is not None and ceiling is not None and floor >= ceiling:
            raise InvariantViolation("Pitch floor must be below the ceiling")
        if not 0.0 < self.voicing_threshold < 1.0:
            raise InvariantViolation("Voicing threshold must be in (0, 1)")

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "hop": self.hop,
            "pitch_floor": self.pitch_floor,
            "pitch_ceiling": self.pitch_ceiling,
            "voicing_threshold": self.voicing_threshold,
            "loudness_window": self.loudness_window,
            "activity_margin_db": self.activity_margin_db,
        }


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Interpretation of measurements. Seconds unless noted; ``st`` = semitones."""

    # pauses
    min_pause: float = 0.15
    short_pause_max: float = 0.4
    long_pause_min: float = 0.9
    # context windows
    rate_window: float = 3.0
    local_window: float = 2.0
    articulation_pause: float = 0.25  # gaps at least this long are not "speaking time"
    # what counts as measured pitch/energy
    min_pitch_confidence: float = 0.45
    min_baseline_voiced_frames: int = 30
    min_word_voiced_frames: int = 3
    smoothing_frames: int = 5
    # acoustic events (sudden changes)
    pitch_event_min_st: float = 2.5
    pitch_event_max_duration: float = 0.6
    pitch_full_scale_st: float = 8.0
    energy_event_min_db: float = 9.0
    energy_event_max_duration: float = 0.3
    energy_full_scale_db: float = 15.0
    #: Minimum duration of silence (no speech activity) reported as a ``silence`` event.
    min_silence: float = 0.3

    def __post_init__(self) -> None:
        if not 0 < self.min_pause < self.short_pause_max < self.long_pause_min:
            raise InvariantViolation("Pause thresholds must satisfy min < short < long")
        if self.rate_window <= 0 or self.local_window <= 0:
            raise InvariantViolation("Context windows must be positive")
        if self.smoothing_frames < 1 or self.smoothing_frames % 2 == 0:
            raise InvariantViolation("Smoothing frames must be a positive odd number")

    def to_config(self) -> dict[str, JsonValue]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _default_weights(**weights: float) -> Mapping[str, float]:
    return dict(sorted(weights.items()))


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Heuristic scoring strategy ``heuristic-1``: weighted means over AVAILABLE evidence.

    Every score is in [0, 1]. Missing evidence is excluded and the remaining weights are
    renormalised; with no evidence at all the score is ``None`` (unknown), never 0.
    """

    name: str = "heuristic"
    version: int = 1
    emphasis_weights: Mapping[str, float] = field(
        default_factory=lambda: _default_weights(
            pitch_change=0.22,
            energy_change=0.22,
            pitch_level=0.10,
            energy_level=0.14,
            duration=0.10,
            local_contrast=0.14,
            pause_context=0.08,
        ),
    )
    moment_weights: Mapping[str, float] = field(
        default_factory=lambda: _default_weights(
            emphasis=0.30,
            pitch_change=0.14,
            energy_change=0.14,
            rate_change=0.10,
            pause_context=0.10,
            expressiveness=0.12,
            event_overlap=0.10,
        ),
    )
    #: Weights of the generic editing signals (evidence aggregates, not decisions).
    editing_weights: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: {
            "broll": _default_weights(
                monotony=0.4,
                flatness=0.2,
                uninterrupted_speech=0.3,
                low_emphasis=0.1,
            ),
            "cut": _default_weights(
                segment_boundary=0.3,
                pause_length=0.3,
                sentence_completion=0.25,
                rate_change=0.15,
            ),
            "music_break": _default_weights(
                pause_length=0.5,
                preceding_moment=0.3,
                preceding_emphasis=0.2,
            ),
            "music_duck": _default_weights(moment=0.6, emphasis=0.4),
            "zoom_emphasis": _default_weights(emphasis=0.6, energy_change=0.2, pitch_change=0.2),
        },
    )
    #: Value that counts as "full" (1.0) for each raw measurement.
    full_scale: Mapping[str, float] = field(
        default_factory=lambda: _default_weights(
            pitch_level_st=6.0,
            energy_level_db=9.0,
            duration_ratio_excess=1.0,
            local_pitch_st=4.0,
            local_energy_db=6.0,
            pitch_range_st=8.0,
            energy_range_db=12.0,
            pitch_std_st=3.0,
            energy_std_db=5.0,
            rate_cv=0.35,
            pause_seconds=1.5,
            uninterrupted_speech_seconds=20.0,
            arousal_rate_ratio_excess=0.5,
        ),
    )

    def __post_init__(self) -> None:
        for table in (self.emphasis_weights, self.moment_weights, *self.editing_weights.values()):
            if not table or any(w < 0 for w in table.values()) or sum(table.values()) <= 0:
                raise InvariantViolation("Scoring weights must be non-negative and not all zero")
        if any(v <= 0 for v in self.full_scale.values()):
            raise InvariantViolation("Scoring full-scale values must be positive")

    @property
    def identity(self) -> str:
        return f"{self.name}-{self.version}"

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "version": self.version,
            "emphasis_weights": dict(sorted(self.emphasis_weights.items())),
            "moment_weights": dict(sorted(self.moment_weights.items())),
            "editing_weights": {
                kind: dict(sorted(weights.items()))
                for kind, weights in sorted(self.editing_weights.items())
            },
            "full_scale": dict(sorted(self.full_scale.items())),
        }


@dataclass(frozen=True, slots=True)
class AudioIntelligenceConfig:
    """Everything that shapes an Audio Intelligence Timeline."""

    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    acoustic: AcousticConfig = field(default_factory=AcousticConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    def measurements_fingerprint(self, identity: Mapping[str, str]) -> dict[str, JsonValue]:
        """Identity of the measured frames: analyzer versions + acoustic config + audio prep."""
        return {
            "acoustic": self.acoustic.to_config(),
            "analyzers": dict(sorted(identity.items())),
            "preparation": self.transcription.preparation.to_config(),
        }

    def timeline_fingerprint(
        self,
        engine_version: str,
        identity: Mapping[str, str],
    ) -> dict[str, JsonValue]:
        """Identity of the whole timeline: speech + measurements + interpretation + scoring."""
        return {
            "transcription": self.transcription.fingerprint_config(engine_version),
            "measurements": self.measurements_fingerprint(identity),
            "analysis": self.analysis.to_config(),
            "scoring": self.scoring.to_config(),
            "analysis_version": ANALYSIS_VERSION,
        }
