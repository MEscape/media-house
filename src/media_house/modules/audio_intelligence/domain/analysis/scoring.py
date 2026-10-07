"""Derived acoustic intelligence and generic editing signals from measured evidence.

Score semantics (all in [0, 1]; ``None`` = not enough evidence, never 0):

* ``*_level`` / ``*_change`` evidence: magnitude of a measurement mapped by a configured
  full-scale (``ScoringConfig.full_scale``); baseline-relative or local-relative as named.
* Scores are weighted means over the evidence that EXISTS (``ScoringConfig`` weights,
  renormalised). Every score keeps its ``contributors`` so "why 0.91?" is answerable.

Editing signals are EVIDENCE AGGREGATES for a future editing layer, not decisions: nothing here
cuts, zooms, ducks music or chooses footage.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from media_house.modules.audio_intelligence.domain.analysis import stats
from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    NONVERBAL_KINDS,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import Baseline
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AnalysisConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.detection import overlapping
from media_house.modules.audio_intelligence.domain.analysis.features import (
    ScoredSignal,
    SegmentAcoustics,
    WordAcoustics,
)
from media_house.modules.audio_intelligence.domain.analysis.pauses import Pause
from media_house.modules.audio_intelligence.domain.transcript import Transcript

MOMENT = "moment"
CUT = "cut"
BROLL = "broll"
MUSIC_DUCK = "music_duck"
MUSIC_BREAK = "music_break"
ZOOM_EMPHASIS = "zoom_emphasis"
EDITING_KINDS = (MOMENT, CUT, BROLL, MUSIC_DUCK, MUSIC_BREAK, ZOOM_EMPHASIS)

ANCHOR_WORD = "word"
ANCHOR_PAUSE = "pause"
ANCHOR_SEGMENT = "segment"
_SENTENCE_END = (".", "!", "?", "…")

# Algorithm constants of ``heuristic-1`` (not user settings; changing one changes results, so it
# needs a ``ScoringConfig.version`` / ``ANALYSIS_VERSION`` bump; pinned by a unit test).
#: A pause BEFORE a word counts half as much as one after it when judging a word's context.
PAUSE_BEFORE_CONTEXT_WEIGHT = 0.5
#: Weight of "pauses inside the segment" against the three equally weighted variation measures.
PAUSE_STRUCTURE_WEIGHT = 0.34
#: One long pause per this many seconds of speech counts as a fully structured segment.
SECONDS_PER_STRUCTURING_PAUSE = 10.0


@dataclass(frozen=True, slots=True)
class WordSignals:
    """Derived acoustic intelligence for one word."""

    emphasis: ScoredSignal | None
    expressiveness: ScoredSignal | None
    #: Acoustic activation: louder, higher and faster than the speaker's norm (not an emotion).
    arousal: ScoredSignal | None
    #: How much the word differs from the speech just before it.
    local_contrast: ScoredSignal | None
    #: Evidence for the editing signals computed on this word.
    moment: ScoredSignal | None = None


@dataclass(frozen=True, slots=True)
class EditingSignal:
    """A generic continuous hint attached to a word, pause or segment."""

    kind: str
    start: float
    end: float
    score: float
    contributors: dict[str, float]
    anchor: str
    anchor_id: int


@dataclass(frozen=True, slots=True)
class ScoringInput:
    transcript: Transcript
    acoustics: Sequence[WordAcoustics]
    segments: Sequence[SegmentAcoustics]
    pauses: Sequence[Pause]
    events: Sequence[AudioEvent]
    baseline: Baseline
    analysis: AnalysisConfig


@dataclass(frozen=True, slots=True)
class ScoringResult:
    words: tuple[WordSignals, ...]
    segments: tuple[SegmentAcoustics, ...]
    editing_signals: tuple[EditingSignal, ...]


class Scorer(Protocol):
    """Replaceable scoring strategy. ``identity`` goes into the processing fingerprint."""

    @property
    def identity(self) -> str: ...

    def score(self, data: ScoringInput) -> ScoringResult: ...


def _signal(parts: dict[str, float | None], weights: dict[str, float]) -> ScoredSignal | None:
    score = stats.weighted_mean(parts, weights)
    if score is None:
        return None
    return ScoredSignal(score, {k: v for k, v in parts.items() if v is not None and k in weights})


def _rate_change(acoustic: WordAcoustics) -> float | None:
    rate = acoustic.rate
    return None if rate is None or rate.change is None else abs(rate.change)


def _full(config: ScoringConfig, name: str) -> float:
    return config.full_scale[name]


class HeuristicScorer:
    """Strategy ``heuristic-N``: transparent weighted evidence, fully configurable."""

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self._config = config or ScoringConfig()

    @property
    def identity(self) -> str:
        return self._config.identity

    def score(self, data: ScoringInput) -> ScoringResult:
        words = self._score_words(data)
        segments = self._score_segments(data, words)
        signals = self._editing_signals(data, words, segments)
        return ScoringResult(tuple(words), tuple(segments), tuple(signals))

    # --- words ------------------------------------------------------------------------------
    def _score_words(self, data: ScoringInput) -> list[WordSignals]:
        cfg = self._config
        after = {p.before_word: p for p in data.pauses if p.before_word is not None}
        before = {p.after_word: p for p in data.pauses if p.after_word is not None}
        result: list[WordSignals] = []
        for word, acoustic in zip(data.transcript.words, data.acoustics, strict=True):
            pitch, energy, rate = acoustic.pitch, acoustic.energy, acoustic.rate
            pitch_change = acoustic.pitch_change.strength if acoustic.pitch_change else None
            energy_change = acoustic.energy_change.strength if acoustic.energy_change else None
            relative_st = pitch.relative_st if pitch else None
            relative_db = energy.relative_db if energy else None
            pitch_level = stats.saturate(
                None if relative_st is None else abs(relative_st),
                _full(cfg, "pitch_level_st"),
            )
            energy_level = stats.saturate(
                None if relative_db is None else max(0.0, relative_db),
                _full(cfg, "energy_level_db"),
            )
            duration = stats.saturate(
                None
                if rate is None or rate.duration_ratio is None
                else max(0.0, rate.duration_ratio - 1.0),
                _full(cfg, "duration_ratio_excess"),
            )
            local = stats.mean(
                [
                    v
                    for v in (
                        stats.saturate(
                            None
                            if pitch is None or pitch.local_deviation_st is None
                            else abs(pitch.local_deviation_st),
                            _full(cfg, "local_pitch_st"),
                        ),
                        stats.saturate(
                            None
                            if energy is None or energy.local_deviation_db is None
                            else abs(energy.local_deviation_db),
                            _full(cfg, "local_energy_db"),
                        ),
                        None if rate is None or rate.change is None else abs(rate.change),
                    )
                    if v is not None
                ],
            )
            pause_after = after.get(word.index)
            pause_before = before.get(word.index)
            pause_context = stats.saturate(
                max(
                    [
                        p.duration * w
                        for p, w in (
                            (pause_after, 1.0),
                            (pause_before, PAUSE_BEFORE_CONTEXT_WEIGHT),
                        )
                        if p is not None
                    ],
                    default=0.0,
                ),
                _full(cfg, "pause_seconds"),
            )
            emphasis = _signal(
                {
                    "pitch_change": pitch_change,
                    "energy_change": energy_change,
                    "pitch_level": pitch_level,
                    "energy_level": energy_level,
                    "duration": duration,
                    "local_contrast": local,
                    "pause_context": pause_context,
                },
                dict(cfg.emphasis_weights),
            )
            expressiveness = _signal(
                {
                    "pitch_range": stats.saturate(
                        pitch.range_st if pitch else None,
                        _full(cfg, "pitch_range_st"),
                    ),
                    "energy_range": stats.saturate(
                        energy.dynamic_range_db if energy else None,
                        _full(cfg, "energy_range_db"),
                    ),
                    "local_contrast": local,
                },
                {"pitch_range": 1.0, "energy_range": 1.0, "local_contrast": 1.0},
            )
            arousal = _signal(
                {
                    "energy_level": energy_level,
                    "pitch_height": stats.saturate(
                        None if relative_st is None else max(0.0, relative_st),
                        _full(cfg, "pitch_level_st"),
                    ),
                    "rate": stats.saturate(
                        None
                        if rate is None or rate.relative is None
                        else max(0.0, rate.relative - 1.0),
                        _full(cfg, "arousal_rate_ratio_excess"),
                    ),
                },
                {"energy_level": 1.0, "pitch_height": 1.0, "rate": 1.0},
            )
            nonverbal = overlapping(data.events, word.start, word.end, NONVERBAL_KINDS)
            moment = _signal(
                {
                    "emphasis": emphasis.score if emphasis else None,
                    "pitch_change": pitch_change,
                    "energy_change": energy_change,
                    "rate_change": _rate_change(acoustic),
                    "pause_context": pause_context,
                    "expressiveness": expressiveness.score if expressiveness else None,
                    "event_overlap": max((e.strength for e in nonverbal), default=None),
                },
                dict(cfg.moment_weights),
            )
            result.append(
                WordSignals(
                    emphasis,
                    expressiveness,
                    arousal,
                    _signal({"local_contrast": local}, {"local_contrast": 1.0}),
                    moment,
                ),
            )
        return result

    # --- segments ---------------------------------------------------------------------------
    def _score_segments(
        self,
        data: ScoringInput,
        words: Sequence[WordSignals],
    ) -> list[SegmentAcoustics]:
        cfg = self._config
        result: list[SegmentAcoustics] = []
        for segment, summary in zip(data.transcript.segments, data.segments, strict=True):
            indices = [w.index for w in segment.words]
            energies = [
                e.mean_db for e in (data.acoustics[i].energy for i in indices) if e is not None
            ]
            inside = sum(
                1
                for p in data.pauses
                if p.before_segment == segment.id == p.after_segment
                and p.duration >= data.analysis.short_pause_max
            )
            variation = {
                "pitch_variation": stats.saturate(summary.pitch_std_st, _full(cfg, "pitch_std_st")),
                "energy_variation": stats.saturate(
                    stats.std(energies), _full(cfg, "energy_std_db")
                ),
                "rate_variation": stats.saturate(summary.rate_cv, _full(cfg, "rate_cv")),
            }
            equal = {"pitch_variation": 1.0, "energy_variation": 1.0, "rate_variation": 1.0}
            dynamic = stats.weighted_mean(variation, equal)
            structure = (
                min(1.0, inside / max(1.0, segment.duration / SECONDS_PER_STRUCTURING_PAUSE))
                if segment.words
                else None
            )
            expressiveness = _signal(
                {**variation, "pause_structure": structure},
                {**equal, "pause_structure": PAUSE_STRUCTURE_WEIGHT},
            )
            monotony = (
                None
                if dynamic is None
                else ScoredSignal(
                    1.0 - dynamic,
                    {k: v for k, v in variation.items() if v is not None},
                )
            )
            emphasis = [e.score for e in (words[i].emphasis for i in indices) if e is not None]
            moments = [m.score for m in (words[i].moment for i in indices) if m is not None]
            result.append(
                replace(
                    summary,
                    expressiveness=expressiveness,
                    monotony=monotony,
                    emphasis_mean=stats.mean(emphasis),
                    emphasis_max=max(emphasis, default=None),
                    moment_max=max(moments, default=None),
                ),
            )
        return result

    # --- editing signals --------------------------------------------------------------------
    def _editing_signals(
        self,
        data: ScoringInput,
        words: Sequence[WordSignals],
        segments: Sequence[SegmentAcoustics],
    ) -> list[EditingSignal]:
        cfg = self._config
        weights = {k: dict(v) for k, v in cfg.editing_weights.items()}
        signals: list[EditingSignal] = []

        def emit(
            kind: str,
            start: float,
            end: float,
            parts: dict[str, float | None],
            anchor: str,
            anchor_id: int,
        ) -> None:
            scored = _signal(parts, weights[kind])
            if scored is not None:
                signals.append(
                    EditingSignal(
                        kind, start, end, scored.score, scored.contributors, anchor, anchor_id
                    ),
                )

        for word, derived, acoustic in zip(
            data.transcript.words, words, data.acoustics, strict=True
        ):
            emphasis = derived.emphasis.score if derived.emphasis else None
            moment = derived.moment.score if derived.moment else None
            if derived.moment:
                signals.append(
                    EditingSignal(
                        MOMENT,
                        word.start,
                        word.end,
                        derived.moment.score,
                        dict(derived.moment.contributors),
                        ANCHOR_WORD,
                        word.index,
                    ),
                )
            pitch_change = acoustic.pitch_change.strength if acoustic.pitch_change else None
            energy_change = acoustic.energy_change.strength if acoustic.energy_change else None
            emit(
                ZOOM_EMPHASIS,
                word.start,
                word.end,
                {
                    "emphasis": emphasis,
                    "energy_change": energy_change,
                    "pitch_change": pitch_change,
                },
                ANCHOR_WORD,
                word.index,
            )
            emit(
                MUSIC_DUCK,
                word.start,
                word.end,
                {"moment": moment, "emphasis": emphasis},
                ANCHOR_WORD,
                word.index,
            )

        for number, pause in enumerate(data.pauses):
            before = pause.before_word
            after = pause.after_word
            before_signals = words[before] if before is not None else None
            length = stats.saturate(pause.duration, _full(cfg, "pause_seconds"))
            completion = (
                None
                if before is None
                else float(
                    data.transcript.words[before].raw_word.rstrip("\"'»)").endswith(_SENTENCE_END)
                )
            )
            rate_change = None if after is None else _rate_change(data.acoustics[after])
            emit(
                MUSIC_BREAK,
                pause.start,
                pause.end,
                {
                    "pause_length": length,
                    "preceding_moment": before_signals.moment.score
                    if before_signals and before_signals.moment
                    else None,
                    "preceding_emphasis": before_signals.emphasis.score
                    if before_signals and before_signals.emphasis
                    else None,
                },
                ANCHOR_PAUSE,
                number,
            )
            emit(
                CUT,
                pause.start,
                pause.end,
                {
                    "segment_boundary": float(pause.at_segment_boundary),
                    "pause_length": length,
                    "sentence_completion": completion,
                    "rate_change": rate_change,
                },
                ANCHOR_PAUSE,
                number,
            )

        for segment, summary in zip(data.transcript.segments, segments, strict=True):
            speaking = stats.saturate(segment.duration, _full(cfg, "uninterrupted_speech_seconds"))
            emit(
                BROLL,
                segment.start,
                segment.end,
                {
                    "monotony": summary.monotony.score if summary.monotony else None,
                    "flatness": None
                    if summary.expressiveness is None
                    else 1.0 - summary.expressiveness.score,
                    "uninterrupted_speech": speaking,
                    "low_emphasis": None
                    if summary.emphasis_max is None
                    else 1.0 - summary.emphasis_max,
                },
                ANCHOR_SEGMENT,
                segment.id,
            )
        return sorted(signals, key=lambda s: (s.start, s.kind, s.anchor_id))
