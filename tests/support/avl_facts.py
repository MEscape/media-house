"""Hand-made upstream facts for audio-visual alignment tests (no media, no models)."""

import math
from collections.abc import Callable, Sequence

from media_house.core.domain import FrameTime, Rational, TimeRange
from media_house.modules.audio_visual_alignment.domain.inputs import (
    AudioEventFact,
    AudioFacts,
    ClockFacts,
    ContentFacts,
    ContentOpportunityFact,
    Inputs,
    MouthSample,
    Passage,
    ShotFacts,
    SpeechActivity,
    SpeechSegment,
    VideoEventFact,
    VideoFacts,
)

TIMEBASE = Rational(1, 1000)
HOP = 0.02


def ft(seconds: float, frame: int = 0) -> FrameTime:
    return FrameTime(frame=frame, pts=round(seconds * 1000), timebase=TIMEBASE)


def trange(start: float, end: float) -> TimeRange:
    return TimeRange(ft(start, round(start * 25)), ft(end, round(end * 25)))


def activity(
    seconds: float, speaking: Callable[[float], bool], origin: float = 0.0
) -> SpeechActivity:
    count = round(seconds / HOP)
    return SpeechActivity(
        origin, HOP, tuple(1.0 if speaking(origin + (i + 0.5) * HOP) else 0.0 for i in range(count))
    )


def shot(
    shot_id: str,
    start: float,
    end: float,
    *,
    measured: bool = True,
    faces: int = 10,
    people: int | None = 1,
    identity: str | None = "person_A",
    text: tuple[str, ...] = (),
    objects: tuple[str, ...] = (),
    description: str | None = None,
) -> ShotFacts:
    return ShotFacts(
        shot_id=shot_id,
        range=trange(start, end),
        faces_measured=measured,
        face_frames=faces if measured else 0,
        people=people,
        main_track_id=f"trk_{shot_id}" if identity else None,
        identity_id=identity,
        screen_text=text,
        objects=objects,
        description=description,
    )


def talking(start: float, end: float, rate: float = 3.0) -> list[MouthSample]:
    """A moving mouth: the jaw opening alternates, sampled ``rate`` times a second."""
    n = round((end - start) * rate)
    return [MouthSample(start + i / rate, 0.30 + (0.15 if i % 2 else -0.15)) for i in range(n)]


def still(start: float, end: float, rate: float = 3.0) -> list[MouthSample]:
    n = round((end - start) * rate)
    return [MouthSample(start + i / rate, 0.12 + 0.001 * (i % 2)) for i in range(n)]


def segment(
    seg_id: str, start: float, end: float, speaker: str | None = None, text: str = "x"
) -> SpeechSegment:
    return SpeechSegment(seg_id, start, end, text, speaker)


def audio(
    segments: Sequence[SpeechSegment],
    *,
    duration: float = 60.0,
    speaking: Callable[[float], bool] | None = None,
    events: Sequence[AudioEventFact] = (),
    language: str | None = "en",
    asset_id: str = "clip",
) -> AudioFacts:
    def default(t: float) -> bool:
        return any(s.start <= t < s.end for s in segments)

    return AudioFacts(
        asset_id=asset_id,
        document_id=None,
        duration=duration,
        language=language,
        segments=tuple(segments),
        activity=activity(duration, speaking or default),
        events=tuple(events),
    )


def video(
    shots: Sequence[ShotFacts],
    mouth: Sequence[MouthSample] = (),
    *,
    duration: float | None = 60.0,
    first_frame: float | None = 0.0,
    events: Sequence[VideoEventFact] = (),
    asset_id: str = "clip",
) -> VideoFacts:
    return VideoFacts(
        asset_id=asset_id,
        document_id=None,
        duration=duration,
        first_frame_seconds=first_frame,
        shots=tuple(shots),
        mouth=tuple(mouth),
        events=tuple(events),
    )


def content(
    passages: Sequence[tuple[str, float, float, str]],
    opportunities: Sequence[ContentOpportunityFact] = (),
) -> ContentFacts:
    return ContentFacts(None, tuple(Passage(*p) for p in passages), tuple(opportunities))


def inputs(
    audio_facts: AudioFacts,
    video_facts: VideoFacts,
    *,
    content_facts: ContentFacts | None = None,
    clock: ClockFacts | None = None,
    shift: float | None = None,
    similarities: dict[tuple[str, str], float] | None = None,
) -> Inputs:
    return Inputs(
        audio=audio_facts,
        video=video_facts,
        content=content_facts,
        clock=clock,
        audio_shift_seconds=shift,
        similarities=similarities or {},
    )


def blocks(total: float, seed: int = 7) -> list[tuple[float, float]]:
    """Deterministic, irregular speech blocks (start, end) covering about half of ``total``."""
    state, t = seed, 1.0
    out: list[tuple[float, float]] = []
    while t < total - 8:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        length = 2.0 + (state % 40) / 10.0
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        gap = 1.5 + (state % 30) / 10.0
        out.append((t, t + length))
        t += length + gap
    return out


def mouth_following(
    speech_blocks: Sequence[tuple[float, float]], total: float, lag: float, rate: float = 3.0
) -> list[MouthSample]:
    """A mouth that moves exactly while speech was heard ``lag`` seconds earlier."""
    samples: list[MouthSample] = []
    for i in range(int(total * rate)):
        t = i / rate
        speaking = any(a <= t - lag < b for a, b in speech_blocks)
        samples.append(MouthSample(t, 0.30 + ((0.15 if i % 2 else -0.15) if speaking else 0.0)))
    return samples


def finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)
