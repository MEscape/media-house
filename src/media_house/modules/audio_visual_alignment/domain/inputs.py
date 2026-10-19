"""What alignment reads from upstream, in the shape it needs.

The application layer fills these from the public results of Audio, Video and Content
Intelligence and Media Inspection. The domain never sees an upstream type, so an upstream change
touches one adapter, and every rule here runs on hand-made facts in tests.

Times named ``start``/``end``/``seconds`` are exactly what the upstream module stored (audio and
content: seconds on the container timeline; video: ``FrameTime`` seconds as the decoder reported
them). The CLOCK MODEL (``clock.py``) is the only place that moves one onto the other.
"""

import math
from dataclasses import dataclass, field

from media_house.core.domain import FrameTime, TimeRange

_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    segment_id: str
    start: float
    end: float
    text: str
    #: A diarization label, or ``None`` when upstream did not label speakers.
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class SpeechActivity:
    """Frame-synchronous speech/no-speech flags: frame ``i`` covers ``origin + i*hop``."""

    origin: float
    hop: float
    flags: tuple[float, ...]

    def fraction(self, start: float, end: float) -> float | None:
        """Share of ``[start, end)`` that is speech, or ``None`` outside the measured range."""
        first = max(0, math.floor((start - self.origin) / self.hop + _EPS))
        last = min(len(self.flags), math.ceil((end - self.origin) / self.hop - _EPS))
        if last <= first:
            return None
        window = self.flags[first:last]
        return sum(1.0 for f in window if f >= 0.5) / len(window)


@dataclass(frozen=True, slots=True)
class AudioEventFact:
    event_id: str
    kind: str
    start: float
    end: float
    strength: float


@dataclass(frozen=True, slots=True)
class AudioFacts:
    asset_id: str
    #: The stored analysis this was read from (its library asset id), or ``None`` when supplied.
    document_id: str | None
    duration: float
    language: str | None
    segments: tuple[SpeechSegment, ...]
    activity: SpeechActivity | None
    events: tuple[AudioEventFact, ...] = ()


@dataclass(frozen=True, slots=True)
class ShotFacts:
    shot_id: str
    range: TimeRange
    #: Faces were measured for this shot (the analyser ran), so zero faces means none found.
    faces_measured: bool
    face_frames: int
    people: int | None
    main_track_id: str | None
    identity_id: str | None
    screen_text: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    description: str | None = None
    scene_id: str | None = None


@dataclass(frozen=True, slots=True)
class MouthSample:
    """Jaw opening of the largest face on one sampled frame (upstream seconds)."""

    seconds: float
    jaw: float


@dataclass(frozen=True, slots=True)
class VideoEventFact:
    event_id: str
    kind: str
    shot_id: str
    time: FrameTime
    end: FrameTime | None = None
    strength: float | None = None


@dataclass(frozen=True, slots=True)
class VideoFacts:
    asset_id: str
    document_id: str | None
    duration: float | None
    #: Seconds of the first presented frame as the video analysis stored it.
    first_frame_seconds: float | None
    shots: tuple[ShotFacts, ...]
    mouth: tuple[MouthSample, ...] = ()
    events: tuple[VideoEventFact, ...] = ()


@dataclass(frozen=True, slots=True)
class Passage:
    passage_id: str
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class ContentOpportunityFact:
    opportunity_id: str
    kind: str
    concept: str
    passage_id: str
    start: float
    end: float
    strength: float
    already_covered: bool
    visual_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentFacts:
    document_id: str | None
    passages: tuple[Passage, ...]
    opportunities: tuple[ContentOpportunityFact, ...] = ()


@dataclass(frozen=True, slots=True)
class ClockFacts:
    """Stream timing from a stored inspection (all seconds on the file's own time origin)."""

    container_start: float | None
    #: First PRESENTED frame of the video stream (falls back to the stream's start time).
    video_first_pts: float | None
    audio_start: float | None = None


@dataclass(frozen=True, slots=True)
class Inputs:
    audio: AudioFacts
    video: VideoFacts
    content: ContentFacts | None = None
    clock: ClockFacts | None = None
    #: A caller-supplied shift of the audio onto the video's clock (dual-system recording).
    audio_shift_seconds: float | None = None
    #: Cosine similarity per (passage_id, shot_id) from an embedding engine, when one ran.
    similarities: dict[tuple[str, str], float] = field(default_factory=dict)
