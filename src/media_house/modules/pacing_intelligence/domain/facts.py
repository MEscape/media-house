"""What the analyser reads: plain facts copied from the upstream results and the story plan.

The application layer maps each upstream result (through that module's public contract) into these
types, so the domain imports no other module. Everything here is an observation made upstream;
pacing adds measurements and judgement on top, never new facts. Times are float seconds on the
recording's own container timeline (the alignment's common clock), or ``None`` when upstream had
none. Times of different recordings are NEVER on one clock.
"""

from dataclasses import dataclass

from media_house.core.domain import Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFacts:
    asset_id: str
    name: str
    duration: float | None
    frame_rate: Rational | None
    #: ``False`` only when the inspection measured a constant rate; ``None`` = not known.
    variable_frame_rate: bool | None
    has_video: bool | None
    has_audio: bool | None
    identity: str


@dataclass(frozen=True, slots=True, kw_only=True)
class WordFact:
    index: int
    start: float
    end: float
    text: str
    confidence: float | None = None
    speaker: str | None = None
    #: Local speaking rate around the word (pauses excluded), as audio analysis measured it.
    wpm: float | None = None
    emphasis: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PauseFact:
    start: float
    end: float
    #: Share of the gap without speech activity; ``None`` = not measured.
    silence_ratio: float | None
    before_word: int | None
    after_word: int | None
    at_segment_boundary: bool

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True, kw_only=True)
class EventFact:
    kind: str
    start: float
    end: float
    strength: float


@dataclass(frozen=True, slots=True, kw_only=True)
class RhythmFact:
    start: float
    end: float
    tempo_bpm: float
    strength: float
    beats: tuple[float, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioFacts:
    asset_id: str
    duration: float
    #: ``forced_alignment`` (word times measured), ``whisper_segment`` (estimated), ``none``.
    alignment_method: str
    words: tuple[WordFact, ...]
    pauses: tuple[PauseFact, ...]
    events: tuple[EventFact, ...]
    rhythm: tuple[RhythmFact, ...]
    identity: str

    @property
    def word_level(self) -> bool:
        return self.alignment_method == "forced_alignment"


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitFact:
    unit_id: str
    segment_id: str
    start: float | None
    end: float | None
    time_basis: str
    text: str
    words: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentFact:
    segment_id: str
    unit_ids: tuple[str, ...]
    start: float | None
    end: float | None
    importance: float | None
    #: Content intelligence's heuristic information density; ``None`` = too short to estimate.
    density: float | None
    #: ``None`` unless the segment repeats earlier material; then what it adds
    #: (``nothing_new`` ...).
    repeats: str | None
    #: Sentences a reader needs before this passage stands alone.
    requires_context: int
    depends_on: tuple[str, ...]
    key_items: int
    topic_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentFacts:
    asset_id: str
    units: tuple[UnitFact, ...]
    segments: tuple[SegmentFact, ...]
    identity: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotFact:
    shot_id: str
    index: int
    start: float
    end: float
    start_frame: int
    end_frame: int
    #: ``static``, ``pan``, ... ; ``None`` = not measured.
    camera: str | None
    #: Share of measured frame pairs with neither camera nor picture motion; ``None`` = unmeasured.
    static_fraction: float | None
    motion_energy: float | None
    visual_interest: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoFacts:
    asset_id: str
    frame_rate: Rational | None
    shots: tuple[ShotFact, ...]
    identity: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechFact:
    start: float
    end: float
    #: ``speaking_face``, ``quiet_face``, ``no_face_detected`` ...
    visibility: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AlignmentFacts:
    asset_id: str
    speech: tuple[SpeechFact, ...]
    identity: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RefFact:
    """A passage by the identifiers the story plan uses."""

    asset_id: str
    unit_ids: tuple[str, ...] = ()
    start: float | None = None
    end: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionFact:
    section_id: str
    order: int
    label: str
    #: ``essential`` / ``supporting`` / ``optional`` / ``redundant_candidate``.
    priority: str
    sources: tuple[RefFact, ...]
    duration_seconds: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BeatFact:
    beat_id: str
    section_id: str
    function: str
    source: RefFact


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualFact:
    visual_id: str
    section_id: str
    purpose: str
    necessity: str
    concept: str
    source: RefFact


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryFacts:
    story_plan_id: str
    plan_version: int
    stored_as: str | None
    target_format: str
    asset_ids: tuple[str, ...]
    sections: tuple[SectionFact, ...]
    beats: tuple[BeatFact, ...]
    visuals: tuple[VisualFact, ...]

    def section(self, section_id: str) -> SectionFact | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def ordered_sections(self) -> tuple[SectionFact, ...]:
        return tuple(sorted(self.sections, key=lambda s: s.order))


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetEvidence:
    """Everything known about one recording. Only ``source`` is always present."""

    source: SourceFacts
    audio: AudioFacts | None = None
    content: ContentFacts | None = None
    video: VideoFacts | None = None
    alignment: AlignmentFacts | None = None
