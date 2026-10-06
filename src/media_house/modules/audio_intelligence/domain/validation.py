"""Timeline quality checks. They REPORT; they never alter timestamps."""

import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from media_house.modules.audio_intelligence.domain.transcript import Transcript, WordTiming

#: Alignment and rounding noise below this is not an inconsistency.
DEFAULT_TOLERANCE = 0.001
LONG_GAP_SECONDS = 10.0
LONG_WORD_SECONDS = 5.0
MAX_REPORTED = 50


class Severity(StrEnum):
    ERROR = "error"  # impossible data: downstream must not trust the affected words
    WARNING = "warning"  # suspicious but possible


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    severity: Severity
    message: str
    #: Index of the first affected word (``None`` for transcript-level findings).
    word_index: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def validate_transcript(
    transcript: Transcript,
    tolerance: float = DEFAULT_TOLERANCE,
) -> tuple[ValidationIssue, ...]:
    """All findings, most severe kinds first within each check. Empty means clean."""
    issues: list[ValidationIssue] = []
    duration = transcript.duration
    words = transcript.words

    if not words:
        issues.append(
            ValidationIssue("no_words", Severity.WARNING, "the transcript contains no words")
        )

    for word in words:
        where = f"word {word.index} {word.raw_word!r}"
        if not (math.isfinite(word.start) and math.isfinite(word.end)):
            issues.append(
                ValidationIssue(
                    "non_finite_time", Severity.ERROR, f"{where} has a non-finite time", word.index
                )
            )
            continue
        if word.start < -tolerance:
            issues.append(
                ValidationIssue(
                    "negative_start",
                    Severity.ERROR,
                    f"{where} starts at {word.start:.3f}s",
                    word.index,
                )
            )
        if word.end < word.start - tolerance:
            issues.append(
                ValidationIssue(
                    "end_before_start", Severity.ERROR, f"{where} ends before it starts", word.index
                ),
            )
        if word.end > duration + tolerance:
            issues.append(
                ValidationIssue(
                    "past_duration",
                    Severity.WARNING,
                    f"{where} ends at {word.end:.3f}s, media is {duration:.3f}s",
                    word.index,
                ),
            )
        if word.duration > LONG_WORD_SECONDS:
            issues.append(
                ValidationIssue(
                    "long_word", Severity.WARNING, f"{where} lasts {word.duration:.1f}s", word.index
                ),
            )

    for previous, word in pairwise(words):
        if word.start < previous.start - tolerance:
            issues.append(
                ValidationIssue(
                    "words_out_of_order",
                    Severity.ERROR,
                    f"word {word.index} starts before word {previous.index}",
                    word.index,
                ),
            )
        elif word.start < previous.end - tolerance:
            issues.append(
                ValidationIssue(
                    "words_overlap",
                    Severity.WARNING,
                    f"word {word.index} overlaps word {previous.index} "
                    f"by {previous.end - word.start:.3f}s",
                    word.index,
                ),
            )
        elif word.start - previous.end > LONG_GAP_SECONDS:
            issues.append(
                ValidationIssue(
                    "long_gap",
                    Severity.WARNING,
                    f"{word.start - previous.end:.1f}s of silence before word {word.index}",
                    word.index,
                ),
            )

    for previous_segment, segment in zip(
        transcript.segments, transcript.segments[1:], strict=False
    ):
        if segment.start < previous_segment.start - tolerance:
            issues.append(
                ValidationIssue(
                    "segments_out_of_order",
                    Severity.ERROR,
                    f"segment {segment.id} starts before segment {previous_segment.id}",
                ),
            )

    for segment in transcript.segments:
        for word in segment.words:
            if word.start < segment.start - tolerance or word.end > segment.end + tolerance:
                issues.append(
                    ValidationIssue(
                        "word_outside_segment",
                        Severity.WARNING,
                        f"word {word.index} lies outside segment {segment.id}",
                        word.index,
                    ),
                )
                break

    for timing, code in (
        (WordTiming.INTERPOLATED, "interpolated_timing"),
        (WordTiming.SEGMENT_ESTIMATE, "estimated_timing"),
    ):
        affected = [w for w in words if w.timing is timing]
        if affected:
            issues.append(
                ValidationIssue(
                    code,
                    Severity.WARNING,
                    f"{len(affected)} word(s) have {timing.value} timing, not measured",
                    affected[0].index,
                ),
            )
    return tuple(issues)


def summarize(issues: tuple[ValidationIssue, ...]) -> tuple[str, ...]:
    """Short strings for persisted metadata, bounded in size."""
    lines = [str(issue) for issue in issues[:MAX_REPORTED]]
    if len(issues) > MAX_REPORTED:
        lines.append(f"... and {len(issues) - MAX_REPORTED} more findings")
    return tuple(lines)
