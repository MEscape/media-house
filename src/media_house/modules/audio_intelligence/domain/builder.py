"""Turn an engine's raw output into the canonical ``Transcript``.

Deterministic and conservative: measured times are never changed, only shifted by the known
preparation offset. Words the aligner could not time are spread between timed neighbours and
flagged, never silently passed off as measured.
"""

import math
import statistics
from dataclasses import dataclass, replace
from datetime import datetime

from media_house.modules.audio_intelligence.domain.raw import RawSegment, RawTranscription, RawWord
from media_house.modules.audio_intelligence.domain.text import (
    DEFAULT_NORMALIZATION,
    normalize_text,
)
from media_house.modules.audio_intelligence.domain.transcript import (
    EMPHASIS_LONG_DURATION,
    EMPHASIS_UPPERCASE,
    Transcript,
    TranscriptMetadata,
    TranscriptSegment,
    TranscriptWord,
    WordTiming,
)
from media_house.modules.audio_intelligence.domain.validation import summarize, validate_transcript

#: A word is "long" when its seconds-per-character exceed the median by this factor ...
_LONG_FACTOR = 2.0
#: ... and it lasts at least this long, and has at least this many characters.
_LONG_MIN_SECONDS = 0.6
_LONG_MIN_CHARS = 3


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Facts about the run that are not in the engine output."""

    source_asset_id: str
    audio_asset_id: str
    duration: float
    audio_offset: float
    sample_rate: int
    channels: int
    processing_version: int
    created_at: datetime


def build_transcript(raw: RawTranscription, context: BuildContext) -> Transcript:
    segments: list[TranscriptSegment] = []
    index = 0
    for segment_id, raw_segment in enumerate(raw.segments):
        timed = _timed_words(raw_segment)
        words = tuple(
            TranscriptWord(
                index=index + i,
                segment_id=segment_id,
                raw_word=text,
                normalized_word=normalize_text(text, DEFAULT_NORMALIZATION),
                start=start + context.audio_offset,
                end=end + context.audio_offset,
                confidence=confidence,
                timing=timing,
            )
            for i, (text, start, end, confidence, timing) in enumerate(timed)
        )
        index += len(words)
        segments.append(
            TranscriptSegment(
                id=segment_id,
                start=raw_segment.start + context.audio_offset,
                end=raw_segment.end + context.audio_offset,
                text=raw_segment.text.strip(),
                words=words,
            ),
        )
    segments = _with_emphasis(segments)

    metadata = TranscriptMetadata(
        source_asset_id=context.source_asset_id,
        audio_asset_id=context.audio_asset_id,
        duration=context.duration,
        language=raw.language,
        language_detected=raw.language_detected,
        transcription_engine=raw.engine,
        engine_version=raw.engine_version,
        model=raw.model,
        model_source=raw.model_source,
        alignment_engine=raw.alignment_engine,
        alignment_model=raw.alignment_model,
        alignment_method=raw.alignment_method,
        processing_version=context.processing_version,
        created_at=context.created_at,
        sample_rate=context.sample_rate,
        channels=context.channels,
        device=raw.device,
        compute_type=raw.compute_type,
        audio_offset=context.audio_offset,
    )
    transcript = Transcript(metadata, tuple(segments))
    warnings = summarize(validate_transcript(transcript))
    if not warnings:
        return transcript
    return Transcript(replace(metadata, warnings=warnings), transcript.segments)


type _Timed = tuple[str, float, float, float | None, WordTiming]


def _timed_words(segment: RawSegment) -> list[_Timed]:
    """Every word of the segment with a start/end and an honest timing label."""
    if not any(w.text.strip() for w in segment.words):
        return _estimate_from_text(segment)
    words = [w for w in segment.words if w.text.strip()]
    result: list[_Timed | None] = [None] * len(words)
    for i, w in enumerate(words):
        if (
            w.start is not None
            and w.end is not None
            and math.isfinite(w.start)
            and math.isfinite(w.end)
        ):
            result[i] = (w.text.strip(), w.start, w.end, w.confidence, WordTiming.ALIGNED)
    i = 0
    while i < len(words):
        if result[i] is not None:
            i += 1
            continue
        j = i
        while j < len(words) and result[j] is None:
            j += 1
        previous_word = result[i - 1] if i > 0 else None
        next_word = result[j] if j < len(words) else None
        before = previous_word[2] if previous_word else segment.start
        after = next_word[1] if next_word else segment.end
        spans = _spread(words[i:j], before, max(before, after))
        for k, (start, end) in enumerate(spans):
            w = words[i + k]
            result[i + k] = (w.text.strip(), start, end, w.confidence, WordTiming.INTERPOLATED)
        i = j
    return [r for r in result if r is not None]


def _estimate_from_text(segment: RawSegment) -> list[_Timed]:
    tokens = [RawWord(t, None, None) for t in segment.text.split()]
    spans = _spread(tokens, segment.start, segment.end)
    return [
        (token.text, start, end, None, WordTiming.SEGMENT_ESTIMATE)
        for token, (start, end) in zip(tokens, spans, strict=True)
    ]


def _spread(words: list[RawWord], start: float, end: float) -> list[tuple[float, float]]:
    """Split ``[start, end]`` between words in proportion to their character counts."""
    weights = [max(1, len(w.text.strip())) for w in words]
    total = sum(weights)
    spans: list[tuple[float, float]] = []
    cursor = start
    for weight in weights:
        step = (end - start) * weight / total
        spans.append((cursor, cursor + step))
        cursor += step
    return spans


def _with_emphasis(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    measured = [
        w.duration / len(w.normalized_word)
        for s in segments
        for w in s.words
        if w.timing is WordTiming.ALIGNED
        and len(w.normalized_word) >= _LONG_MIN_CHARS
        and w.duration > 0
    ]
    median_rate = statistics.median(measured) if measured else None

    def hints(word: TranscriptWord) -> tuple[str, ...]:
        found: list[str] = []
        letters = [c for c in word.raw_word if c.isalpha()]
        if len(letters) >= 2 and all(c.isupper() for c in letters):
            found.append(EMPHASIS_UPPERCASE)
        if (
            median_rate is not None
            and word.timing is WordTiming.ALIGNED
            and len(word.normalized_word) >= _LONG_MIN_CHARS
            and word.duration >= _LONG_MIN_SECONDS
            and word.duration / len(word.normalized_word) > _LONG_FACTOR * median_rate
        ):
            found.append(EMPHASIS_LONG_DURATION)
        return tuple(found)

    return [
        replace(s, words=tuple(replace(w, emphasis_hints=hints(w)) for w in s.words))
        for s in segments
    ]
