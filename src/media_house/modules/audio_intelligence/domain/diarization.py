"""Who speaks when: speaker turns, and how they label an existing transcript.

Diarization is a separate, optional stage with its own stored document. It never changes the
words, their times or the transcript's identity: it only fills ``speaker`` on words and segments
when the timeline is fused. Without it every ``speaker`` stays ``None``, exactly as before.

Turns are in PREPARED-audio time (like the measurements) and may overlap (simultaneous speech).
"""

import json
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import accumulate

from media_house.modules.audio_intelligence.domain.errors import InvalidTranscript
from media_house.modules.audio_intelligence.domain.transcript import (
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from media_house.modules.audio_intelligence.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation

DIARIZATION_OPERATION = "diarization"
#: Bump when how turns are produced, stored or assigned to words changes.
DIARIZATION_VERSION = 1
DIARIZATION_DOCUMENT = "audio_intelligence.diarization"
DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
#: A word with no overlapping turn takes the nearest turn at most this many seconds away.
NEAREST_TURN_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class DiarizationConfig:
    """What shapes the turns. ``device`` and ``allow_model_download`` only tune speed and access,
    so they are not part of a result's identity (see ``to_config``)."""

    model: str = DEFAULT_DIARIZATION_MODEL
    #: Exact number of speakers when known; otherwise bounds (or neither) let the model decide.
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    device: str = "auto"
    allow_model_download: bool = False

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise InvariantViolation("The diarization model must not be empty")
        counts = (self.num_speakers, self.min_speakers, self.max_speakers)
        if any(c is not None and c < 1 for c in counts):
            raise InvariantViolation("Speaker counts must be at least 1")
        if self.num_speakers is not None and (self.min_speakers or self.max_speakers):
            raise InvariantViolation("Give either an exact speaker count or bounds, not both")
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise InvariantViolation("min_speakers must not exceed max_speakers")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise InvariantViolation("Device must be one of auto, cpu, cuda")

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "model": self.model,
            "num_speakers": self.num_speakers,
            "min_speakers": self.min_speakers,
            "max_speakers": self.max_speakers,
        }


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str

    def __post_init__(self) -> None:
        if not (math.isfinite(self.start) and math.isfinite(self.end)) or self.end < self.start:
            raise InvariantViolation("A speaker turn needs finite times and end >= start")
        if not self.speaker:
            raise InvariantViolation("A speaker turn needs a speaker label")


@dataclass(frozen=True, slots=True)
class Diarization:
    turns: tuple[SpeakerTurn, ...]
    #: ``{name: version}`` of the engine and model that produced the turns.
    engine: dict[str, str]

    @property
    def speakers(self) -> tuple[str, ...]:
        return tuple(sorted({t.speaker for t in self.turns}))


# --- assigning turns to words ---------------------------------------------------------------------
def assign_speakers(
    transcript: Transcript, diarization: Diarization, audio_offset: float
) -> Transcript:
    """The same transcript with ``speaker`` filled on every word and segment that has a turn.

    A word takes the speaker whose turns overlap it most (ties: the label that sorts first); a
    word no turn overlaps takes the nearest turn within ``NEAREST_TURN_SECONDS``, else stays
    ``None``. A segment takes the speaker of most of its word seconds. Times, text, indices and
    metadata are untouched.
    """
    turns = sorted(
        (
            replace(t, start=t.start + audio_offset, end=t.end + audio_offset)
            for t in diarization.turns
        ),
        key=lambda t: (t.start, t.end, t.speaker),
    )
    starts = [t.start for t in turns]
    max_ends = list(accumulate((t.end for t in turns), max))

    def candidates(start: float, end: float) -> range:
        return range(bisect_right(max_ends, start), bisect_left(starts, end))

    def speaker_of(word: TranscriptWord) -> str | None:
        shared: dict[str, float] = defaultdict(float)
        for i in candidates(word.start, word.end):
            t = turns[i]
            overlap = min(word.end, t.end) - max(word.start, t.start)
            if overlap > 0:
                shared[t.speaker] += overlap
        if shared:
            return min(shared, key=lambda s: (-shared[s], s))
        return _nearest(turns, word.start, word.end)

    segments: list[TranscriptSegment] = []
    for segment in transcript.segments:
        words = tuple(replace(w, speaker=speaker_of(w)) for w in segment.words)
        weight: dict[str, float] = defaultdict(float)
        for w in words:
            if w.speaker is not None:
                weight[w.speaker] += max(w.duration, 1e-6)
        chosen = min(weight, key=lambda s: (-weight[s], s)) if weight else None
        segments.append(replace(segment, words=words, speaker=chosen))
    return Transcript(transcript.metadata, tuple(segments))


def _nearest(turns: list[SpeakerTurn], start: float, end: float) -> str | None:
    best: tuple[float, str] | None = None
    for t in turns:
        gap = max(t.start - end, start - t.end, 0.0)
        if gap <= NEAREST_TURN_SECONDS and (best is None or (gap, t.speaker) < best):
            best = (gap, t.speaker)
    return None if best is None else best[1]


# --- stored form ---------------------------------------------------------------------------
def diarization_to_json(diarization: Diarization) -> str:
    return json.dumps(
        {
            "document_type": DIARIZATION_DOCUMENT,
            "schema_version": 1,
            "version": DIARIZATION_VERSION,
            "engine": dict(sorted(diarization.engine.items())),
            "turns": [[t.start, t.end, t.speaker] for t in diarization.turns],
        },
        separators=(",", ":"),
        allow_nan=False,
    )


def diarization_from_json(text: str) -> Diarization:
    try:
        raw = json.loads(text)
        if raw["document_type"] != DIARIZATION_DOCUMENT or raw["schema_version"] != 1:
            raise InvalidTranscript("not a diarization document of a known version")
        engine = {str(k): str(v) for k, v in raw["engine"].items()}
        turns = tuple(SpeakerTurn(float(a), float(b), str(s)) for a, b, s in raw["turns"])
    except (KeyError, TypeError, ValueError, InvariantViolation) as exc:
        raise InvalidTranscript(f"unreadable diarization document: {exc}") from exc
    return Diarization(turns, engine)
