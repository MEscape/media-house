"""The transcript this module analyses, as a small input contract of its own.

Callers (and the application layer's adapter for the Audio Intelligence transcript) build a
``SourceTranscript``. Times are optional: a plain-text transcript is valid and simply yields
results without time ranges. The original text is never rewritten; normalisation for analysis
happens on copies.
"""

import hashlib
import json
import math
from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.errors import InvalidTranscriptInput
from media_house.modules.content_intelligence.domain.values import SpeechTiming


@dataclass(frozen=True, slots=True)
class SourceWord:
    text: str
    start: float
    end: float
    timing: SpeechTiming = SpeechTiming.ALIGNED
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class SourceSegment:
    id: int
    text: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    words: tuple[SourceWord, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceTranscript:
    segments: tuple[SourceSegment, ...]
    #: Language tag of the speech (``en``, ``de``, ``pt-BR``); ``None`` if unknown.
    language: str | None = None
    #: Authoritative length of the media in seconds, when known.
    duration: float | None = None
    #: Where the transcript came from: ``supplied`` or ``audio_intelligence``.
    origin: str = "supplied"
    #: The library asset the transcript describes, and the asset that stores it, when known.
    asset_id: str | None = None
    transcript_asset_id: str | None = None

    @classmethod
    def from_text(
        cls, text: str, *, language: str | None = None, asset_id: str | None = None
    ) -> "SourceTranscript":
        """A transcript without times: one segment per non-empty line."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return cls(
            tuple(SourceSegment(i, line) for i, line in enumerate(lines)),
            language=language,
            asset_id=asset_id,
        )

    def fingerprint(self) -> str:
        """Identity of the content this analysis depends on (text, times, speakers, language)."""
        body = {
            "language": self.language,
            "duration": None if self.duration is None else round(self.duration, 3),
            "segments": [
                [
                    s.id,
                    s.text,
                    None if s.start is None else round(s.start, 3),
                    None if s.end is None else round(s.end, 3),
                    s.speaker,
                    [[w.text, round(w.start, 3), round(w.end, 3), w.timing.value] for w in s.words],
                ]
                for s in self.segments
            ],
        }
        canonical = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_source(source: SourceTranscript) -> tuple[str, ...]:
    """Raise ``InvalidTranscriptInput`` for broken structure; return warnings for odd content."""
    warnings: list[str] = []
    seen: set[int] = set()
    previous_start: float | None = None
    out_of_order = beyond = untimed = 0
    for segment in source.segments:
        if segment.id in seen:
            raise InvalidTranscriptInput(f"segment id {segment.id} appears twice")
        seen.add(segment.id)
        for label, value in (("start", segment.start), ("end", segment.end)):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise InvalidTranscriptInput(f"segment {segment.id}: {label} is not a valid time")
        if (segment.start is None) != (segment.end is None):
            raise InvalidTranscriptInput(f"segment {segment.id}: start and end must come together")
        if segment.start is not None and segment.end is not None:
            if segment.end < segment.start:
                raise InvalidTranscriptInput(f"segment {segment.id} ends before it starts")
            if previous_start is not None and segment.start < previous_start - 1e-6:
                out_of_order += 1
            previous_start = segment.start
            if source.duration is not None and segment.end > source.duration + 0.5:
                beyond += 1
        else:
            untimed += 1
        for word in segment.words:
            if (
                not math.isfinite(word.start)
                or not math.isfinite(word.end)
                or word.end < word.start
            ):
                raise InvalidTranscriptInput(f"segment {segment.id}: a word has an invalid time")
    if out_of_order:
        warnings.append(f"{out_of_order} segment(s) start before the previous one; order kept")
    if beyond:
        warnings.append(f"{beyond} segment(s) end after the media duration; times kept as given")
    if untimed and untimed != len(source.segments):
        warnings.append(f"{untimed} segment(s) have no times; their results carry no time range")
    if untimed == len(source.segments) and source.segments:
        warnings.append("the transcript has no times; results carry no time ranges")
    if source.language is None:
        warnings.append("the language is unknown; language-neutral signals only")
    return tuple(warnings)
