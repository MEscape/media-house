"""The B-roll analysis: typed, immutable, traceable. JSON is only its storage form.

Rules every part obeys:

* A recommendation names an ORIGINAL library asset and a source range on that recording's
  common clock (``SourceInterval``, exact ``Rational`` seconds). The passage it serves is a
  separate interval on the TARGET recording's own clock (``SuggestedPlacement``). Neither is a
  timeline time: the editing stage maps them. Nothing here claims a final trim: only a shot
  boundary is frame-exact.
* Scores stay separate. ``Score.value`` is ``None`` (with the reason) when it could not be
  stated; it is never ``0`` for "unknown". ``Candidate.overall`` is the weighted mean of the
  scores that exist and says which.
* ``Support`` is the gate: ``SATISFIES`` means the shot meets the evidence the need requires.
  A recommendation whose candidates are all ``ILLUSTRATIVE_ONLY`` is ``missing_coverage`` and
  has a gap; the illustrative alternatives are listed as such, never promoted.
* ``reason_codes`` and ``evidence`` make every claim checkable: which observation, in which shot,
  produced it.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.broll_intelligence.domain.values import (
    SCHEMA_VERSION,
    AssetVia,
    Association,
    AudioBehavior,
    ClockBasis,
    EvidenceRole,
    GapKind,
    Identity,
    InputHow,
    NeedKind,
    Precision,
    Priority,
    Purpose,
    ResultState,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    Support,
    TargetOrigin,
    UseKind,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Score:
    name: ScoreName
    #: In [0, 1], higher is better; ``None`` = could not be stated (see ``reason``).
    value: float | None
    basis: ScoreBasis
    reason: str

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.reason:
                raise InvariantViolation(f"score {self.name.value} is unknown without a reason")
        elif not 0.0 <= self.value <= 1.0:
            raise InvariantViolation(f"score {self.name.value} must lie within [0, 1]")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRef:
    #: ``shot``, ``ocr_text``, ``description``, ``object_label``, ``metadata``,
    #: ``visual_embedding``,
    #: ``text_embedding``, ``event``, ``sound_action``, ``story_visual``, ``content_opportunity``,
    #: ``pacing``, ``reference``, ``relation``, ``lineage``.
    kind: str
    ref: str
    asset_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceInterval:
    """Half-open ``[start, end)`` seconds on a recording's common clock."""

    start: Rational
    end: Rational
    precision: Precision


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    candidate_id: str
    #: 1-based position among the ranked candidates of its recommendation; 0 = not ranked (refused).
    rank: int
    #: The original asset (the id to use), the stored version the evidence came from, and how.
    asset_id: str
    analyzed_asset_id: str
    via: AssetVia
    asset_name: str
    shot_id: str
    scene_id: str | None
    #: The whole shot, and the part of it recommended.
    shot_range: SourceInterval
    source: SourceInterval
    clock: ClockBasis
    role: EvidenceRole
    association: Association
    identity: Identity
    support: Support
    scores: tuple[Score, ...]
    overall: float | None
    #: Which scores made ``overall`` and how they were weighted (missing ones are left out).
    overall_basis: str
    insert_min_seconds: float
    insert_max_seconds: float
    use: UseKind
    audio: AudioBehavior
    audio_reason: str
    reason_codes: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    warnings: tuple[str, ...] = ()
    #: The better candidate this one repeats, when it was dropped as a duplicate.
    duplicate_of: str | None = None
    #: The same footage was already chosen for another passage.
    reused: bool = False

    def score(self, name: ScoreName) -> Score | None:
        return next((s for s in self.scores if s.name is name), None)

    def value(self, name: ScoreName) -> float | None:
        score = self.score(name)
        return None if score is None else score.value


@dataclass(frozen=True, slots=True, kw_only=True)
class SuggestedPlacement:
    """Where in the TARGET recording the footage would be used, and how (intent, not a timeline)."""

    use: UseKind
    #: Seconds on the target recording's common clock.
    target_start: Rational
    target_end: Rational
    insert_min_seconds: float
    insert_max_seconds: float
    #: The speaker stays on screen (picture-in-picture or alternating) rather than being replaced.
    keep_speaker_visible: bool | None
    context_before_seconds: float
    context_after_seconds: float
    audio: AudioBehavior
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Recommendation:
    recommendation_id: str
    target_id: str
    asset_id: str
    origin: TargetOrigin
    need: NeedKind
    purpose: Purpose
    priority: Priority
    concept: str
    passage: str
    #: What the need requires of a candidate to SATISFY it, in words.
    requires: str
    placement: SuggestedPlacement
    #: Best first. Candidates that satisfy the need come before illustrative ones.
    candidates: tuple[Candidate, ...]
    #: Considered and refused (a few, to explain why a passage is a gap).
    rejected: tuple[Candidate, ...]
    #: No candidate satisfies the need.
    missing_coverage: bool
    gap_id: str | None
    reasons: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    warnings: tuple[str, ...] = ()
    segment_id: str | None = None
    section_id: str | None = None
    beat_id: str | None = None
    story_visual_id: str | None = None

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


@dataclass(frozen=True, slots=True, kw_only=True)
class NoChange:
    """A passage where the main picture should stay, with the reason."""

    target_id: str
    asset_id: str
    origin: TargetOrigin
    start: Rational
    end: Rational
    reason_code: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Considered:
    asset_id: str
    shot_id: str
    role: EvidenceRole
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Searched:
    """How hard the library was searched for one passage."""

    assets: int
    shots: int
    #: Shots that survived retrieval and were assessed in detail.
    assessed: int
    #: Of those, how many were refused and how many were only illustrative.
    refused: int
    illustrative: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageGap:
    gap_id: str
    target_id: str
    recommendation_id: str
    asset_id: str
    kind: GapKind
    purpose: Purpose
    need: NeedKind
    concept: str
    start: Rational
    end: Rational
    reason: str
    searched: Searched
    considered: tuple[Considered, ...]
    importance: Priority
    urgency: Priority
    #: Illustrative footage exists that a person could choose to use despite the gap.
    repurposable: bool
    repurpose_candidates: tuple[str, ...]
    #: What would close the gap, and what to do meanwhile.
    resolution: str
    fallback: str
    evidence: tuple[EvidenceRef, ...]
    confidence: float | None = None
    section_id: str | None = None
    beat_id: str | None = None
    story_visual_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    kind: str
    how: InputHow
    asset_id: str | None = None
    identity: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    issue_id: str
    severity: Severity
    subject: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PoolRecord:
    """The footage that was searched."""

    assets: int
    with_shots: int
    shots: int
    shots_with_embedding: int
    #: Identity of the whole pool (every asset's analysis), part of the cache key.
    identity: str
    skipped: tuple[tuple[str, str], ...] = ()
    limits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryRef:
    story_plan_id: str
    plan_version: int
    stored_as: str | None
    target_format: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    analyzer_version: int
    schema_version: int
    vocabulary_version: int
    profile_name: str
    profile_version: int
    configuration_fingerprint: str
    visual_model: str | None
    text_model: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BrollAnalysis:
    broll_analysis_id: str
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    state: ResultState
    story: StoryRef | None
    profile: str
    #: The recordings whose passages were served, in story order.
    sources: tuple[str, ...]
    pool: PoolRecord
    recommendations: tuple[Recommendation, ...]
    gaps: tuple[CoverageGap, ...]
    no_change: tuple[NoChange, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    issues: tuple[Issue, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    #: What this result cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    schema_version: int = field(default=SCHEMA_VERSION)

    def recommendation(self, target_id: str) -> Recommendation | None:
        return next((r for r in self.recommendations if r.target_id == target_id), None)

    def for_story_visual(self, visual_id: str) -> Recommendation | None:
        return next((r for r in self.recommendations if r.story_visual_id == visual_id), None)

    def gap_for(self, target_id: str) -> CoverageGap | None:
        return next((g for g in self.gaps if g.target_id == target_id), None)

    def satisfied(self) -> tuple[Recommendation, ...]:
        return tuple(r for r in self.recommendations if not r.missing_coverage)

    def issues_of(self, severity: Severity) -> list[Issue]:
        return [i for i in self.issues if i.severity is severity]
