"""Pauses: the gaps between spoken words, with their raw duration and acoustic confirmation."""

from dataclasses import dataclass
from itertools import pairwise

from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticTrack
from media_house.modules.audio_intelligence.domain.analysis.config import AnalysisConfig
from media_house.modules.audio_intelligence.domain.transcript import Transcript

SHORT = "short"
MEDIUM = "medium"
LONG = "long"


@dataclass(frozen=True, slots=True)
class Pause:
    """A silence between two words (or before the first / after the last).

    ``duration`` is the raw measured gap and is never rounded into ``kind``; ``kind`` is a
    convenience bucket from ``AnalysisConfig`` thresholds. ``silence_ratio`` is the share of
    acoustic frames in the gap without speech activity: a high value confirms real silence,
    a low one means something (breath, noise, a missed word) fills the gap; ``None`` = no frames.
    """

    start: float
    end: float
    before_word: int | None
    after_word: int | None
    before_segment: int | None
    after_segment: int | None
    kind: str
    silence_ratio: float | None
    at_segment_boundary: bool

    @property
    def duration(self) -> float:
        return self.end - self.start


def detect_pauses(
    transcript: Transcript,
    track: AcousticTrack | None,
    config: AnalysisConfig,
) -> tuple[Pause, ...]:
    words = transcript.words
    if not words:
        return ()
    pauses: list[Pause] = []

    def add(start: float, end: float, before: int | None, after: int | None) -> None:
        if end - start < config.min_pause:
            return
        before_word = words[before] if before is not None else None
        after_word = words[after] if after is not None else None
        before_segment = before_word.segment_id if before_word else None
        after_segment = after_word.segment_id if after_word else None
        pauses.append(
            Pause(
                start=start,
                end=end,
                before_word=before,
                after_word=after,
                before_segment=before_segment,
                after_segment=after_segment,
                kind=_kind(end - start, config),
                silence_ratio=_silence_ratio(track, start, end),
                at_segment_boundary=before_segment != after_segment,
            ),
        )

    add(0.0, words[0].start, None, 0)
    for a, b in pairwise(words):
        add(a.end, b.start, a.index, b.index)
    add(words[-1].end, transcript.duration, words[-1].index, None)
    return tuple(pauses)


def _kind(duration: float, config: AnalysisConfig) -> str:
    if duration < config.short_pause_max:
        return SHORT
    return MEDIUM if duration < config.long_pause_min else LONG


def _silence_ratio(track: AcousticTrack | None, start: float, end: float) -> float | None:
    if track is None:
        return None
    frames = track.span(start, end)
    if not frames:
        return None
    return sum(1 for i in frames if track.speech[i] < 0.5) / len(frames)
