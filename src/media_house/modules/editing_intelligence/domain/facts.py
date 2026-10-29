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
    #: Part of the shot a source suggests (seconds on the recording's common clock). The editor
    #: still clamps it to the shot, its settling head and the length it needs.
    hint_start: float | None = None
    hint_end: float | None = None
    #: Cautions the source attached to this candidate (shown on the cover, never hidden).
    warnings: tuple[str, ...] = ()


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
class BrollPlace:
    """What the B-roll analysis found for one story visual (best first), reduced to choices."""

    visual_id: str
    choices: tuple[ShotChoice, ...]
    #: The passage the footage is for (seconds on the recording's clock). The story may give a
    #: whole section as a visual's source; B-roll narrows it to the statement the visual is about.
    start: float
    end: float
    #: No candidate satisfies what the story asked for (any choice listed is illustrative only).
    missing: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class BrollNote:
    """A satisfied B-roll recommendation for a place the story did not name as a visual."""

    asset_id: str
    start: float
    end: float
    need: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BrollFacts:
    analysis_id: str
    profile: str
    places: tuple[BrollPlace, ...]
    notes: tuple[BrollNote, ...]
    #: ``(original asset id, the asset whose analysis holds its shots)`` for every candidate.
    analysed: tuple[tuple[str, str], ...]
    identity: str

    def place(self, visual_id: str) -> BrollPlace | None:
        return next((p for p in self.places if p.visual_id == visual_id), None)

    def assets(self) -> tuple[tuple[str, str], ...]:
        """``(asset id, shot id)`` of every candidate offered."""
        return tuple(dict.fromkeys((c.asset_id, c.shot_id) for p in self.places for c in p.choices))


@dataclass(frozen=True, slots=True, kw_only=True)
class PausePacing:
    """What the pacing analysis made of one silence: evidence for the editor, not an order."""

    asset_id: str
    start: float
    end: float
    #: ``dead_air``, ``long``, ``meaningful`` or ``natural``.
    verdict: str
    #: What should remain of the silence if it is tightened; ``None`` = keep it.
    keep: float | None
    reasons: tuple[str, ...]
    confidence: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingAdvice:
    """One recommendation of the pacing analysis, reduced to what the editor can show or weigh."""

    recommendation_id: str
    kind: str
    asset_id: str
    section_id: str | None
    beat_id: str | None
    proposal: str
    delta_seconds: float | None
    #: For a hold: how long it should last in total.
    duration: float | None
    confidence: float | None
    priority: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingFacts:
    analysis_id: str
    profile: str
    strategy: str
    pauses: tuple[PausePacing, ...]
    advice: tuple[PacingAdvice, ...]
    identity: str

    def pause_at(self, asset_id: str, start: float, end: float) -> PausePacing | None:
        """The analysed silence that covers most of ``[start, end]``."""
        best: tuple[float, PausePacing] | None = None
        for p in self.pauses:
            overlap = min(end, p.end) - max(start, p.start)
            if p.asset_id == asset_id and overlap > 0 and (best is None or overlap > best[0]):
                best = (overlap, p)
        return None if best is None else best[1]

    def reveal_hold(self, beat_id: str | None) -> float | None:
        """How long the pacing analysis wants a reveal or payoff to be held, if it asked."""
        for a in self.advice:
            if a.kind == "hold_reveal" and a.beat_id == beat_id and a.duration is not None:
                return a.duration
        return None

    def tighten_sections(self) -> frozenset[str]:
        """Sections where it recommends shortening repetition or slow, empty delivery."""
        return frozenset(
            a.section_id
            for a in self.advice
            if a.section_id is not None and a.kind in ("reduce_repetition", "tighten_delivery")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetEvidence:
    """Everything known about one recording. Only ``source`` is always present."""

    source: SourceFacts
    content: ContentFacts | None = None
    audio: AudioFacts | None = None
    video: VideoFacts | None = None
    alignment: AlignmentFacts | None = None
