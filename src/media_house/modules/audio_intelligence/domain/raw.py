"""What a speech engine hands back, engine-neutral. Times are seconds in the PREPARED audio."""

from dataclasses import dataclass

ALIGNMENT_FORCED = "forced_alignment"
ALIGNMENT_SEGMENT = "whisper_segment"  # words estimated inside ASR segments (lower quality)
ALIGNMENT_NONE = "none"  # alignment was disabled by configuration


@dataclass(frozen=True, slots=True)
class RawWord:
    text: str
    #: ``None`` when the aligner could not time this token (digits, symbols, ...).
    start: float | None
    end: float | None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class RawSegment:
    start: float
    end: float
    text: str
    #: Empty when the engine produced no word timing for this segment.
    words: tuple[RawWord, ...] = ()


@dataclass(frozen=True, slots=True)
class RawTranscription:
    segments: tuple[RawSegment, ...]
    language: str
    language_detected: bool
    engine: str
    engine_version: str
    model: str
    #: Where the model weights come from (repository id or path), if the engine knows.
    model_source: str | None
    alignment_engine: str | None
    alignment_model: str | None
    alignment_method: str
    device: str
    compute_type: str
