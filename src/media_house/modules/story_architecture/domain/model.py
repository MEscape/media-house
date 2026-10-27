"""The story plan: typed, immutable, traceable. JSON is only its storage form.

Rules every part obeys:

* Traceability: a beat, key statement or opening names the source passage (``SourceRef``: asset,
  segments, sentences, time) it rests on. A recommendation with no passage behind it says
  ``Basis.INFERRED``.
* Missing is not zero: a value that could not be computed is ``None`` and the reason is stated
  where it matters (``Criterion.reason``, ``DurationBasis.UNKNOWN``).
* Direct vs inferred: text is ``direct``, ``paraphrase`` or ``inference``; each carries its method.
* Scores and confidence are strength of evidence in [0, 1], never calibrated probabilities.
* The plan recommends an order, an opening and where visuals help. It never says "cut here":
  exact keep, cut and timing decisions belong to the editing stage.
"""

from dataclasses import dataclass
from datetime import datetime

from media_house.modules.story_architecture.domain.values import (
    SCHEMA_VERSION,
    Basis,
    BeatFunction,
    Certainty,
    ClaimKind,
    ConstraintKind,
    ContentMode,
    DurationBasis,
    IssueKind,
    LinkKind,
    LinkState,
    Method,
    Necessity,
    OpeningStrategy,
    Origin,
    PacingAction,
    PacingKind,
    Priority,
    ResultState,
    Severity,
    StageState,
    Strength,
    SupportRelation,
    Treatment,
    VisualPurpose,
    optional_unit_interval,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRef:
    """A passage of one source recording, by the identifiers the upstream analysis uses."""

    asset_id: str
    segment_ids: tuple[str, ...]
    unit_ids: tuple[str, ...] = ()
    #: Seconds on the original media timeline; ``None`` when upstream had no time.
    start: float | None = None
    end: float | None = None
    exact_time: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Statement:
    text: str
    origin: Origin
    method: Method
    unit_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetRecord:
    asset_id: str
    name: str
    duration: float | None
    language: str | None
    #: Upstream result state, passed through.
    content_state: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AudienceIntent:
    format: str
    #: What the viewer should understand or do, from the format profile.
    intent: str
    #: Supplied by the caller only; never inferred from the recording.
    audience: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DurationEstimate:
    seconds: float | None
    basis: DurationBasis
    #: Strength of the basis in [0, 1]: high for observed, low for a words-per-minute guess.
    confidence: float | None = None

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "duration confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class Beat:
    """One narrative step: a passage and the function it serves in the story."""

    beat_id: str
    section_id: str
    function: BeatFunction
    source: SourceRef
    basis: Basis
    method: Method
    summary: str | None = None
    confidence: float | None = None
    #: Sentences holding a qualification of this beat that must travel with it.
    qualified_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "beat confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingProfile:
    words: int
    #: Words per minute over the observed span, or ``None`` when the section is untimed.
    speech_rate_wpm: float | None
    density: float | None
    #: Share of sentences carrying an emphasis / surprise / direction-change marker, [0, 1].
    rhetorical_intensity: float
    mode: ContentMode


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    section_id: str
    #: Position in the recording (0-based) and in the recommended order.
    source_index: int
    order: int
    label: str
    purpose: Statement
    summary: Statement | None
    priority: Priority
    priority_reasons: tuple[str, ...]
    importance: float | None
    sources: tuple[SourceRef, ...]
    beat_ids: tuple[str, ...]
    #: Sections whose content this one needs the viewer to have seen first.
    requires: tuple[str, ...]
    before: str | None
    after: str | None
    #: ``None`` = the order may change; otherwise why it may not.
    reorder_locked: str | None
    issue_ids: tuple[str, ...]
    visual_ids: tuple[str, ...]
    duration: DurationEstimate
    pacing: PacingProfile

    def __post_init__(self) -> None:
        optional_unit_interval(self.importance, "section importance")


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyInformation:
    info_id: str
    section_id: str
    kind: ClaimKind
    text: str
    origin: Origin
    source: SourceRef
    #: Always ``unverified``: this module can say what was stated, never that it is true.
    verification: str
    priority: Priority
    qualified_by: tuple[str, ...] = ()
    support_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceLink:
    link_id: str
    info_id: str
    relation: SupportRelation
    support: SourceRef
    basis: Basis
    section_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderingConstraint:
    constraint_id: str
    kind: ConstraintKind
    first: str
    second: str
    strength: Strength
    reason: str
    #: The upstream relation (``depends_on`` ...) it comes from.
    relation: str
    basis: Basis


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextDependency:
    dependency_id: str
    section_id: str
    requires_section_id: str
    reason: str
    #: The sentences of the required section the viewer needs, when upstream named them.
    needed: SourceRef | None
    basis: Basis


@dataclass(frozen=True, slots=True, kw_only=True)
class SetupPayoffLink:
    link_id: str
    kind: LinkKind
    state: LinkState
    setup: SourceRef
    setup_section: str
    payoff: SourceRef | None
    payoff_section: str | None
    #: Sections between setup and payoff in the recommended order (0 = adjacent).
    distance: int | None
    basis: Basis


@dataclass(frozen=True, slots=True, kw_only=True)
class Criterion:
    name: str
    value: float | None
    reason: str = ""

    def __post_init__(self) -> None:
        optional_unit_interval(self.value, f"criterion {self.name}")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpeningCandidate:
    candidate_id: str
    strategy: OpeningStrategy
    source: SourceRef
    #: A bounded verbatim excerpt, so the candidate can be read without the recording.
    excerpt: str
    section_id: str
    criteria: tuple[Criterion, ...]
    #: Weighted mean of the criteria that exist, [0, 1]; ``None`` when none could be computed.
    score: float | None
    rank: int
    #: The passage that must stay in front of a viewer for this opening not to mislead.
    context_required: SourceRef | None
    #: Reasons using this passage out of order could mislead; empty = none found.
    risks: tuple[str, ...]
    #: Seconds from the start of the recorded order to the first statement of the premise.
    delay_to_premise_seconds: float | None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        optional_unit_interval(self.score, "opening score")


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualRef:
    asset_id: str
    shot_id: str
    start: float
    end: float
    #: Alignment's ordinal grade for the pairing; evidence, not a recommendation of a shot.
    grade: str
    rank: int


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualOpportunity:
    visual_id: str
    section_id: str
    purpose: VisualPurpose
    necessity: Necessity
    #: The verbatim phrase or key terms the visual should show. Never invented.
    concept: str
    source: SourceRef
    rationale: str
    strength: float
    #: Content intelligence's verdict that existing footage already shows it.
    already_covered: bool
    #: Shots alignment found for the passage, best first. Empty = none known.
    available: tuple[VisualRef, ...]
    origin_opportunity: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingNote:
    note_id: str
    section_id: str
    kind: PacingKind
    action: PacingAction
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletenessIssue:
    issue_id: str
    kind: IssueKind
    severity: Severity
    section_id: str | None
    detail: str
    source: SourceRef | None = None
    related: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderChange:
    """One section whose recommended place differs from where it was recorded."""

    section_id: str
    from_index: int
    to_index: int
    reason: str
    constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class NarrativePattern:
    #: The format's pattern id (reported, never forced onto the recording).
    pattern: str
    #: Functions the recording actually contains, in order of first appearance.
    observed: tuple[BeatFunction, ...]
    source_order: tuple[str, ...]
    recommended_order: tuple[str, ...]
    changes: tuple[OrderChange, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanDuration:
    #: Everything in recommended order.
    total: DurationEstimate
    #: Essential sections only.
    core: DurationEstimate
    target_low: float | None
    target_high: float | None
    #: ``None`` = no target or no estimate.
    within_target: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlternativePlan:
    alternative_id: str
    label: str
    #: What this variant changes against the recommended plan, in words.
    changes: tuple[str, ...]
    section_order: tuple[str, ...]
    opening_candidate: str | None
    #: Priorities kept in this variant; sections of other priorities are left out of it.
    includes: tuple[Priority, ...]
    omits: tuple[str, ...]
    emphasizes: tuple[str, ...]
    estimated_seconds: float | None
    criteria: tuple[Criterion, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Confidence:
    #: Mean of the components, [0, 1]. Strength of evidence, not a probability.
    value: float
    #: ``structure`` (upstream state), ``grounding`` (beats on direct passages), ``timing`` (share
    #: of sections with observed time), ``coverage`` (share of sentences in a section).
    components: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        optional_unit_interval(self.value, "plan confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    kind: str  # content_analysis | alignment
    #: supplied | reused | requested | not_available
    how: str
    asset_id: str | None = None
    fingerprint: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    planner_version: int
    vocabulary_version: int
    format: str
    format_version: int
    #: Models behind model-made values (name -> identity); empty when none ran.
    engines: tuple[tuple[str, str], ...]
    prompt_version: int | None
    configuration_fingerprint: str
    #: Counts of model calls and rejected model output.
    usage: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetRelation:
    """A relation between two recordings of the plan and what the plan did with it.

    Certainty is carried over, never raised: a claim that influenced the plan is still a claim.
    """

    relationship_id: str
    #: The relationship type as asset_relationships names it.
    relation: str
    source_asset: str
    target_asset: str
    certainty: Certainty
    grade: str
    contradicted: bool
    outdated: bool
    evidence: tuple[str, ...]
    treatment: Treatment
    #: Sections whose priority or flags this relation changed.
    affected_sections: tuple[str, ...]
    effect: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryPlan:
    """The best coherent story the available material supports, as a blueprint for editing."""

    story_plan_id: str
    #: 1 for the first plan of these sources and format; later plans count up (history is kept).
    plan_version: int
    #: Library id of the plan this one follows, or ``None`` for the first.
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    state: ResultState
    source_assets: tuple[AssetRecord, ...]
    target_format: str
    audience_intent: AudienceIntent
    narrative_pattern: NarrativePattern
    main_message: Statement | None
    premise: Statement | None
    candidate_openings: tuple[OpeningCandidate, ...]
    recommended_opening: str | None
    sections: tuple[Section, ...]
    beats: tuple[Beat, ...]
    key_information: tuple[KeyInformation, ...]
    supporting_evidence: tuple[EvidenceLink, ...]
    ordering_constraints: tuple[OrderingConstraint, ...]
    context_dependencies: tuple[ContextDependency, ...]
    setup_payoff_links: tuple[SetupPayoffLink, ...]
    visual_opportunities: tuple[VisualOpportunity, ...]
    pacing_notes: tuple[PacingNote, ...]
    estimated_duration: PlanDuration
    completeness_issues: tuple[CompletenessIssue, ...]
    alternative_structures: tuple[AlternativePlan, ...]
    confidence: Confidence
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    #: What this plan cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION
    #: Relations between the plan's recordings. Empty for one recording, or when none is known.
    asset_relations: tuple[AssetRelation, ...] = ()

    # --- lookups ---------------------------------------------------------------------------
    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def beat(self, beat_id: str) -> Beat | None:
        return next((b for b in self.beats if b.beat_id == beat_id), None)

    def opening(self, candidate_id: str) -> OpeningCandidate | None:
        return next((o for o in self.candidate_openings if o.candidate_id == candidate_id), None)

    def ordered_sections(self) -> tuple[Section, ...]:
        """The sections in the recommended order."""
        return tuple(sorted(self.sections, key=lambda s: s.order))

    def beats_of(self, section_id: str) -> tuple[Beat, ...]:
        return tuple(b for b in self.beats if b.section_id == section_id)

    def source_references(self) -> tuple[SourceRef, ...]:
        """Every passage the plan cites (sections, beats, openings, statements), de-duplicated."""
        refs: list[SourceRef] = []
        for section in self.sections:
            refs.extend(section.sources)
        refs.extend(b.source for b in self.beats)
        refs.extend(o.source for o in self.candidate_openings)
        refs.extend(k.source for k in self.key_information)
        return tuple(dict.fromkeys(refs))
