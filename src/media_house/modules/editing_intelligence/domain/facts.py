"""What the editor reads: plain facts copied from the upstream results and the story plan.

The application layer maps each upstream result (through that module's public contract) into these
types, so the domain imports no other module. Everything here is an observation made upstream;
the editor adds decisions on top, never new facts. Times are float seconds on the recording's
container timeline (the alignment's common clock), or ``None`` when upstream had none.
"""

from dataclasses import dataclass

from media_house.core.domain import Rational


# --- the story plan ------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RefFact:
    """A passage by the identifiers the story plan uses."""

    asset_id: str
    segment_ids: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    start: float | None = None
    end: float | None = None
    exact_time: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionFact:
    section_id: str
    order: int
    label: str
    #: ``essential`` / ``supporting`` / ``optional`` / ``redundant_candidate``.
    priority: str
    importance: float | None
    sources: tuple[RefFact, ...]
    requires: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BeatFact:
    beat_id: str
    section_id: str
    function: str
    source: RefFact
    qualified_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyInfoFact:
    info_id: str
    section_id: str
    kind: str
    text: str
    priority: str
    source: RefFact
    qualified_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstraintFact:
    constraint_id: str
    #: ``must_precede`` or ``keep_adjacent``.
    kind: str
    first: str
    second: str
    #: ``hard`` or ``soft``.
    strength: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextFact:
    section_id: str
    requires_section_id: str
    reason: str
    needed: RefFact | None


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkFact:
    """A question and its answer, or a premise and its payoff."""

    link_id: str
    kind: str
    setup: RefFact
    payoff: RefFact | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotChoice:
    """A shot the alignment ranked for a passage: evidence, not a decision."""

    asset_id: str
    shot_id: str
    grade: str
    rank: int


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualFact:
    visual_id: str
    section_id: str
    #: ``demonstration``, ``evidence``, ``comparison``, ``explainer_graphic``, ``illustration``,
    #: ``text_on_screen``.
    purpose: str
    #: ``essential`` or ``optional``.
    necessity: str
    #: Verbatim phrase or key terms the visual should show.
    concept: str
    source: RefFact
    strength: float
    already_covered: bool
    shots: tuple[ShotChoice, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpeningFact:
    candidate_id: str
    strategy: str
    source: RefFact
    excerpt: str
    section_id: str
    risks: tuple[str, ...]
    needs_context: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderChangeFact:
    section_id: str
    from_index: int
    to_index: int
    reason: str
    constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationFact:
    certainty: str
    treatment: str
    affected_sections: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryFacts:
    story_plan_id: str
    plan_version: int
    #: Library id of the stored story document, when it came from the library.
    stored_as: str | None
    target_format: str
    #: Library ids of the recordings, in story order.
    asset_ids: tuple[str, ...]
    sections: tuple[SectionFact, ...]
    beats: tuple[BeatFact, ...]
    key_info: tuple[KeyInfoFact, ...]
    constraints: tuple[ConstraintFact, ...]
    contexts: tuple[ContextFact, ...]
    links: tuple[LinkFact, ...]
    visuals: tuple[VisualFact, ...]
    openings: tuple[OpeningFact, ...]
    recommended_opening: str | None
    order_changes: tuple[OrderChangeFact, ...]
    relations: tuple[RelationFact, ...]
    target_low: float | None
    target_high: float | None
    warnings: tuple[str, ...] = ()

    def section(self, section_id: str) -> SectionFact | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def ordered_sections(self) -> tuple[SectionFact, ...]:
        return tuple(sorted(self.sections, key=lambda s: s.order))

    def beats_of(self, section_id: str) -> tuple[BeatFact, ...]:
        return tuple(b for b in self.beats if b.section_id == section_id)


# --- the recordings --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFacts:
    """Technical facts of one recording, from its stored inspection (nothing was probed here)."""

    asset_id: str
    name: str
    duration: float | None
    frame_rate: Rational | None
    #: ``False`` only when the inspection measured a constant rate; ``None`` = not known.
    variable_frame_rate: bool | None
    has_video: bool | None
    has_audio: bool | None
    sample_rate: int | None
    #: The inspection's verdict that the media can be used; ``None`` = no inspection.
    usable: bool | None
    identity: str
    notes: tuple[str, ...] = ()

    @property
    def constant_frame_rate(self) -> Rational | None:
        """The frame rate frames may be derived from, else ``None``."""
        return self.frame_rate if self.variable_frame_rate is False else None


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitFact:
    """One sentence-like statement of a recording."""

    unit_id: str
    segment_id: str
    start: float | None
    end: float | None
    #: ``words``, ``source`` (measured) vs ``upstream_estimate`` / ``interpolated`` / ``missing``.
    time_basis: str
    text: str
    words: int
    starts_clean: bool
    ends_clean: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentFact:
    segment_id: str
    unit_ids: tuple[str, ...]
    start: float | None
    end: float | None
    importance: float | None
    removal: float | None
    highlight: float | None
    hook: float | None
    #: Sentences a reader needs before this passage stands alone.
    requires_context: tuple[str, ...]
    #: Segments this one depends on, references or continues.
    depends_on: tuple[str, ...]
    #: ``None`` unless the segment repeats another; then what it adds (``nothing_new`` = nothing).
    repeats: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentFacts:
    asset_id: str
    units: tuple[UnitFact, ...]
    segments: tuple[SegmentFact, ...]
    identity: str

    def unit(self, unit_id: str) -> UnitFact | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def segment(self, segment_id: str) -> SegmentFact | None:
        return next((s for s in self.segments if s.segment_id == segment_id), None)


@dataclass(frozen=True, slots=True, kw_only=True)
class WordFact:
    index: int
    start: float
    end: float
    text: str
    confidence: float | None = None
    emphasis: float | None = None
    expressiveness: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PauseFact:
    start: float
    end: float
    #: Share of the gap without speech activity, [0, 1]; ``None`` = not measured.
    silence_ratio: float | None

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
class AudioFacts:
    asset_id: str
    duration: float
    #: ``forced_alignment`` (word times measured), ``whisper_segment`` (estimated), ``none``.
    alignment_method: str
    words: tuple[WordFact, ...]
    pauses: tuple[PauseFact, ...]
    events: tuple[EventFact, ...]
    identity: str

    @property
    def word_level(self) -> bool:
        return self.alignment_method == "forced_alignment"


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotFact:
    """A shot on the common clock, with the measurements an edit may consult."""

    shot_id: str
    index: int
    start: float
    end: float
    black_in: bool
    #: ``static``, ``pan``, ... ; ``None`` = not measured.
    camera: str | None
    usability: float | None
    previous_shot_id: str | None
    jump_cut_likelihood: float | None
    luma_delta: float | None
    color_delta: float | None
    #: Junk indicators upstream flagged (``black_picture``, ``slate`` ...).
    indicators: tuple[str, ...] = ()
    #: Seconds of stable picture at the head and tail of the shot, when measured.
    head_seconds: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoFacts:
    asset_id: str
    shots: tuple[ShotFact, ...]
    identity: str

    def shot(self, shot_id: str) -> ShotFact | None:
        return next((s for s in self.shots if s.shot_id == shot_id), None)

    def shot_at(self, moment: float) -> ShotFact | None:
        return next((s for s in self.shots if s.start <= moment < s.end), None)


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechFact:
    """What the picture shows while speech is heard (alignment, per transcript segment)."""

    start: float
    end: float
    #: ``speaking_face``, ``quiet_face``, ``no_face_detected``, ``ambiguous``, ...
    visibility: str
    shot_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AlignmentFacts:
    asset_id: str
    speech: tuple[SpeechFact, ...]
    identity: str

    def visibility_at(self, start: float, end: float) -> str | None:
        """The visibility holding most of ``[start, end)``, or ``None`` when nothing covers it."""
        best: tuple[float, str] | None = None
        for item in self.speech:
            overlap = min(end, item.end) - max(start, item.start)
            if overlap > 0 and (best is None or overlap > best[0]):
                best = (overlap, item.visibility)
        return None if best is None else best[1]

    def speaking_shots(self) -> frozenset[str]:
        """Shots in which a visible face is speaking: unsuitable as silent cover."""
        return frozenset(
            sid
            for item in self.speech
            if item.visibility == "speaking_face"
            for sid in item.shot_ids
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetEvidence:
    """Everything known about one recording. Only ``source`` is always present."""

    source: SourceFacts
    content: ContentFacts | None = None
    audio: AudioFacts | None = None
    video: VideoFacts | None = None
    alignment: AlignmentFacts | None = None
