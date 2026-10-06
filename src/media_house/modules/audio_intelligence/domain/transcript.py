"""The transcript timeline: typed, immutable, queryable. Seconds are canonical.

All times refer to the ORIGINAL media timeline (any preparation offset is already applied).
Queries assume words and segments are ordered by start time; ``validate_transcript`` reports
when they are not.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from itertools import accumulate
from typing import Final

from media_house.modules.audio_intelligence.domain.frames import (
    Fps,
    FrameRounding,
    frame_to_seconds,
    seconds_to_frame,
)
from media_house.modules.audio_intelligence.domain.text import (
    NormalizationOptions,
    normalize_text,
)


class WordTiming(StrEnum):
    """How trustworthy a word's start/end are."""

    ALIGNED = "aligned"  # measured by forced alignment
    INTERPOLATED = "interpolated"  # aligner skipped it; spread between timed neighbours
    SEGMENT_ESTIMATE = "segment_estimate"  # no word timing at all; spread across the segment


EMPHASIS_UPPERCASE: Final = "uppercase"
EMPHASIS_LONG_DURATION: Final = "long_duration"


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """One spoken word. ``raw_word`` is canonical; ``normalized_word`` is derived for display."""

    index: int
    segment_id: int
    raw_word: str
    normalized_word: str
    start: float
    end: float
    confidence: float | None = None
    speaker: str | None = None
    timing: WordTiming = WordTiming.ALIGNED
    #: Hints only (not emotion detection): ``uppercase``, ``long_duration``.
    emphasis_hints: tuple[str, ...] = ()

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def emphasis_hint(self) -> bool:
        return bool(self.emphasis_hints)

    def normalized(self, options: NormalizationOptions) -> str:
        """Re-derive the display text with other options; the canonical text is untouched."""
        return normalize_text(self.raw_word, options)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    id: int
    start: float
    end: float
    text: str
    words: tuple[TranscriptWord, ...]
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class TranscriptMetadata:
    """How and from what the transcript was produced (no per-word data)."""

    source_asset_id: str
    #: The prepared (mono PCM) audio asset the engine actually heard.
    audio_asset_id: str
    #: Authoritative duration of the source media, not the last word's end.
    duration: float
    language: str
    language_detected: bool
    transcription_engine: str
    engine_version: str
    model: str
    model_source: str | None
    alignment_engine: str | None
    alignment_model: str | None
    #: ``forced_alignment`` | ``whisper_segment`` | ``none``
    alignment_method: str
    processing_version: int
    created_at: datetime
    sample_rate: int
    channels: int
    device: str
    compute_type: str
    #: Seconds added to prepared-audio times to reach the source timeline.
    audio_offset: float = 0.0
    #: Validation findings, kept for downstream quality decisions.
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Spans:
    """Start-sorted spans with a monotone running max of ends, for O(log n) overlap lookups."""

    starts: tuple[float, ...]
    max_ends: tuple[float, ...]

    @classmethod
    def of(cls, starts: list[float], ends: list[float]) -> "_Spans":
        return cls(tuple(starts), tuple(accumulate(ends, max)))

    def overlapping(self, start: float, end: float) -> range:
        """Candidate indices; callers still test real overlap (ends are not monotone)."""
        return range(bisect_right(self.max_ends, start), bisect_left(self.starts, end))

    def containing(self, instant: float) -> range:
        return range(bisect_right(self.max_ends, instant), bisect_right(self.starts, instant))


@dataclass(frozen=True, slots=True)
class Transcript:
    """Metadata, segments and the flat word list. The API downstream modules use."""

    metadata: TranscriptMetadata
    segments: tuple[TranscriptSegment, ...]
    words: tuple[TranscriptWord, ...] = field(init=False)
    _word_spans: _Spans = field(init=False, repr=False, compare=False)
    _segment_spans: _Spans = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        words = tuple(word for segment in self.segments for word in segment.words)
        object.__setattr__(self, "words", words)
        object.__setattr__(
            self,
            "_word_spans",
            _Spans.of([w.start for w in words], [w.end for w in words]),
        )
        object.__setattr__(
            self,
            "_segment_spans",
            _Spans.of([s.start for s in self.segments], [s.end for s in self.segments]),
        )

    # --- basics -----------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        return self.metadata.duration

    @property
    def language(self) -> str:
        return self.metadata.language

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def first_word(self) -> TranscriptWord | None:
        return self.words[0] if self.words else None

    @property
    def last_word(self) -> TranscriptWord | None:
        return self.words[-1] if self.words else None

    # --- time queries ----------------------------------------------------------------------
    def word_at(self, timestamp: float) -> TranscriptWord | None:
        """The word being spoken at ``timestamp`` (``start <= t < end``), else ``None``."""
        for i in self._word_spans.containing(timestamp):
            word = self.words[i]
            if word.start <= timestamp < word.end:
                return word
        return None

    def words_between(self, start: float, end: float) -> tuple[TranscriptWord, ...]:
        """Words overlapping the half-open interval ``[start, end)``."""
        if end <= start:
            return ()
        return tuple(
            self.words[i]
            for i in self._word_spans.overlapping(start, end)
            if self.words[i].end > start and self.words[i].start < end
        )

    def segment_at(self, timestamp: float) -> TranscriptSegment | None:
        for i in self._segment_spans.containing(timestamp):
            segment = self.segments[i]
            if segment.start <= timestamp < segment.end:
                return segment
        return None

    def segments_between(self, start: float, end: float) -> tuple[TranscriptSegment, ...]:
        if end <= start:
            return ()
        return tuple(
            self.segments[i]
            for i in self._segment_spans.overlapping(start, end)
            if self.segments[i].end > start and self.segments[i].start < end
        )

    # --- structure -------------------------------------------------------------------------
    def words_for_segment(self, segment_id: int) -> tuple[TranscriptWord, ...]:
        for segment in self.segments:
            if segment.id == segment_id:
                return segment.words
        return ()

    def next_word(self, word: TranscriptWord) -> TranscriptWord | None:
        return self.words[word.index + 1] if word.index + 1 < len(self.words) else None

    def previous_word(self, word: TranscriptWord) -> TranscriptWord | None:
        return self.words[word.index - 1] if word.index > 0 else None

    # --- pauses ----------------------------------------------------------------------------
    def gap_before(self, word: TranscriptWord) -> float:
        """Silence before ``word``: since the previous word, or since 0 for the first word."""
        previous = self.previous_word(word)
        return max(0.0, word.start - (previous.end if previous else 0.0))

    def gap_after(self, word: TranscriptWord) -> float:
        """Silence after ``word``: until the next word, or until the media ends."""
        following = self.next_word(word)
        return max(0.0, (following.start if following else self.duration) - word.end)

    def pauses(self, min_duration: float = 0.0) -> tuple[tuple[float, float], ...]:
        """``(start, end)`` of each silence between words lasting >= ``min_duration``."""
        return tuple(
            (a.end, b.start)
            for a, b in zip(self.words, self.words[1:], strict=False)
            if b.start - a.end >= max(min_duration, 1e-9)
        )

    # --- frames ----------------------------------------------------------------------------
    @staticmethod
    def to_frame(
        timestamp: float,
        fps: Fps,
        rounding: FrameRounding = FrameRounding.FLOOR,
    ) -> int:
        """Frame index for ``timestamp``. FLOOR = the frame containing the instant."""
        return seconds_to_frame(timestamp, fps, rounding)

    @staticmethod
    def from_frame(frame: int, fps: Fps) -> float:
        return frame_to_seconds(frame, fps)
