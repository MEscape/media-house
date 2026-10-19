"""The content analysis: typed, immutable, grounded. JSON is only its storage form.

Rules every part obeys:

* Traceability: every derived value names the sentences (``unit_ids``) it rests on; sentences
  name their upstream segment and character range. Times say how much to trust them (``Span``).
* Missing is not zero: a score that could not be computed has ``value=None`` AND a reason.
* Direct vs inferred: text is ``direct`` (verbatim), ``paraphrase`` (rewritten, checked against
  its sources) or ``inference``; every value names the ``Method`` that produced it.
* Confidence is the strength of the evidence behind a value in [0, 1], computed from the
  weights of the cues and measurements that fired. It is NOT a calibrated probability. A language
  model's self-reported confidence is never used; model output carries ``None`` unless a
  deterministic check agrees with it.
* Scores are advisory evidence for a later editor. Nothing here says "cut", "keep" or "move".
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from media_house.modules.content_intelligence.domain.span import Span
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.values import (
    SCHEMA_VERSION,
    Adds,
    CacheOutcome,
    KeyKind,
    Method,
    OpportunityKind,
    Origin,
    RefKind,
    RelationKind,
    ResultState,
    RhetoricalKind,
    Role,
    ScoreName,
    SegmentLevel,
    StageState,
)
from media_house.shared.errors import InvariantViolation

EXCERPT_LIMIT = 160


def unit_interval(value: float, label: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise InvariantViolation(f"{label} must be within [0, 1], got {value}")
    return value


def excerpt(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """A bounded verbatim excerpt (whitespace collapsed; cut with an ellipsis)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    """One reason behind a value: a cue that fired, a measurement, an upstream observation."""

    kind: str
    method: Method
    unit_ids: tuple[str, ...] = ()
    #: Bounded verbatim text the evidence points at.
    excerpt: str = ""
    #: Strength of this piece of evidence in [0, 1] (a design weight, not a probability).
    weight: float | None = None
    detail: str = ""
    #: Id of an upstream observation (a shot, a scene, a text item) when it is the evidence.
    observation: str | None = None

    def __post_init__(self) -> None:
        if self.weight is not None:
            unit_interval(self.weight, "evidence weight")


@dataclass(frozen=True, slots=True, kw_only=True)
class Score:
    name: ScoreName
    #: In [0, 1], higher = more of the named property. ``None`` = not computed (see ``reason``).
    value: float | None
    method: Method
    reason: str = ""
    evidence: tuple[Evidence, ...] = ()
    #: Reasons the value should be doubted or the passage handled with care.
    counter_evidence: tuple[Evidence, ...] = ()
    #: The named parts the value was built from.
    components: Mapping[str, float] = field(default_factory=dict)
    #: Sentences a reader needs to interpret the passage (standalone, hook, highlight).
    requires_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.reason:
                raise InvariantViolation(f"Score {self.name.value} is missing without a reason")
        else:
            unit_interval(self.value, f"score {self.name.value}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Summary:
    text: str
    origin: Origin
    method: Method
    #: The sentences the text rests on; a ``paraphrase`` is checked against exactly these.
    unit_ids: tuple[str, ...]
    #: Sentences holding a qualification of the summarised ones that the text keeps.
    qualified_by: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleLabel:
    role: Role
    method: Method
    confidence: float | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class RhetoricalMarker:
    """A function a statement serves in the text. It says nothing about the speaker's feelings."""

    kind: RhetoricalKind
    unit_ids: tuple[str, ...]
    method: Method
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyItem:
    item_id: str
    kind: KeyKind
    text: str
    origin: Origin
    method: Method
    unit_ids: tuple[str, ...]
    segment_id: str | None
    span: Span
    evidence: tuple[Evidence, ...] = ()
    confidence: float | None = None
    #: Sentences holding a caveat that changes how this item must be read.
    qualified_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Ref:
    kind: RefKind
    id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Relationship:
    """``source`` stands in ``kind`` to ``target`` ("source answers target")."""

    rel_id: str
    kind: RelationKind
    source: Ref
    target: Ref
    method: Method
    confidence: float | None = None
    evidence: tuple[Evidence, ...] = ()
    #: For repeats and overlaps: what the LATER passage adds to the earlier one.
    adds: Adds | None = None
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Opportunity:
    """A place where supplementary visuals or on-screen text might help. Never an instruction."""

    opportunity_id: str
    kind: OpportunityKind
    #: ``VisualCategory`` value for b-roll, ``GraphicKind`` value for graphics.
    category: str
    #: The verbatim phrase (or key terms) to illustrate. Never invented.
    concept: str
    segment_id: str
    unit_ids: tuple[str, ...]
    span: Span
    rationale: str
    #: How strongly the passage invites this, in [0, 1] (a heuristic weight).
    strength: float
    method: Method
    #: Shots whose existing content already shows the concept (b-roll) or the text (graphics).
    visual_evidence: tuple[str, ...] = ()
    already_covered: bool = False
    evidence: tuple[Evidence, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unit_interval(self.strength, "opportunity strength")


@dataclass(frozen=True, slots=True, kw_only=True)
class Boundary:
    """Why a segment or topic starts where it does."""

    before_unit: str
    #: Combined evidence strength in [0, 1] (not a probability).
    strength: float
    evidence: tuple[Evidence, ...] = ()
    #: ``True`` when the evidence is thin: treat the position as uncertain.
    weak: bool = False

    def __post_init__(self) -> None:
        unit_interval(self.strength, "boundary strength")


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticSegment:
    segment_id: str
    unit_ids: tuple[str, ...]
    span: Span
    topic_ids: tuple[str, ...]
    boundary: Boundary | None
    summary: Summary | None = None
    key_item_ids: tuple[str, ...] = ()
    roles: tuple[RoleLabel, ...] = ()
    rhetoric: tuple[RhetoricalMarker, ...] = ()
    scores: tuple[Score, ...] = ()
    broll: tuple[Opportunity, ...] = ()
    graphics: tuple[Opportunity, ...] = ()
    #: Relationships where this segment depends on, references or continues something.
    dependency_ids: tuple[str, ...] = ()
    #: Relationships where this segment repeats or overlaps another.
    redundancy_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    confidence: float | None = None
    warnings: tuple[str, ...] = ()

    def score(self, name: ScoreName) -> Score | None:
        return next((s for s in self.scores if s.name is name), None)

    def value(self, name: ScoreName) -> float | None:
        score = self.score(name)
        return None if score is None else score.value


@dataclass(frozen=True, slots=True, kw_only=True)
class Topic:
    topic_id: str
    level: SegmentLevel
    label: str
    label_terms: tuple[str, ...]
    segment_ids: tuple[str, ...]
    span: Span
    method: Method
    parent_id: str | None = None
    description: str | None = None
    summary: Summary | None = None
    key_item_ids: tuple[str, ...] = ()
    #: Topics that share vocabulary with this one without being its parent or child.
    related_topic_ids: tuple[str, ...] = ()
    boundary: Boundary | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation:
    """A recording-level finding that is not tied to one segment."""

    kind: str
    text: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    """How one input was obtained: nothing is probed or recomputed that an owner provides."""

    kind: str  # transcript | audio_timeline | video_analysis
    #: supplied | reused | requested | not_used | not_available
    how: str
    asset_id: str | None = None
    fingerprint: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    cache: CacheOutcome = CacheOutcome.NOT_USED
    seconds: float = 0.0
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state in {StageState.NOT_AVAILABLE, StageState.FAILED} and not self.detail:
            raise InvariantViolation(f"Stage {self.stage} is {self.state.value} without a reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    processing_version: int
    vocabulary_version: int
    profile: str
    #: ``lexical-tfidf-v1`` or the embedding model that measured sentence similarity.
    similarity: str
    similarity_kind: str
    #: Models and versions behind model-based values (empty when none ran).
    engines: Mapping[str, str]
    prompt_version: int | None
    configuration_fingerprint: str
    device: str
    #: Resource use: model calls, tokens, seconds, cache hits.
    usage: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRecord:
    origin: str
    transcript_asset_id: str | None
    transcript_fingerprint: str
    language: str | None
    duration: float | None
    upstream_segments: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentAnalysis:
    """Everything understood about what one recording communicates."""

    analysis_id: str
    asset_id: str | None
    state: ResultState
    analysis_timestamp: datetime
    source: SourceRecord
    inputs: tuple[InputRecord, ...]
    provenance: Provenance
    stages: tuple[StageReport, ...]
    units: tuple[Unit, ...]
    segments: tuple[SemanticSegment, ...]
    topics: tuple[Topic, ...]
    key_items: tuple[KeyItem, ...]
    relationships: tuple[Relationship, ...]
    summary: Summary | None
    key_takeaways: tuple[Summary, ...]
    global_observations: tuple[Observation, ...]
    warnings: tuple[str, ...]
    #: What this result cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    errors: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    # --- lookups ---------------------------------------------------------------------------
    def unit(self, unit_id: str) -> Unit | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def segment(self, segment_id: str) -> SemanticSegment | None:
        return next((s for s in self.segments if s.segment_id == segment_id), None)

    def topic(self, topic_id: str) -> Topic | None:
        return next((t for t in self.topics if t.topic_id == topic_id), None)

    def key_item(self, item_id: str) -> KeyItem | None:
        return next((k for k in self.key_items if k.item_id == item_id), None)

    def relationship(self, rel_id: str) -> Relationship | None:
        return next((r for r in self.relationships if r.rel_id == rel_id), None)
