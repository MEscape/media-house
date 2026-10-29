"""The continuity analysis: typed, immutable, traceable. JSON is only its storage form.

* An issue names the exact source spans and the timeline place of the cut; the two clocks are
  never converted into each other here.
* Scores stay separate (see ``values``): a missing one is ``None`` with a reason, never 0.
  ``level`` (severity), ``detectability_confidence``, ``intentional_style_likelihood`` and
  ``repair_difficulty`` answer different questions.
* A repair says whether anything was actually examined (``Availability``): a direction that is not
  backed by a checked candidate is ``unchecked`` and a repair whose module does not exist is
  ``unavailable``. Continuity executes nothing; ``owner`` says who would.
* An issue a person accepted or rejected keeps that decision through every reanalysis.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.continuity_intelligence.domain.values import (
    SCHEMA_VERSION,
    Availability,
    Decision,
    IdentityVerdict,
    InputHow,
    IssueType,
    Level,
    Owner,
    RepairKind,
    ResultState,
    Scope,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    TransitionKind,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Score:
    name: ScoreName
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
    #: ``video_edge``, ``embedding``, ``track``, ``face``, ``lighting``, ``audio_word``,
    #: ``audio_level``, ``audio_event``, ``alignment``, ``relationship``, ``edit_plan``,
    #: ``ocr_text``.
    kind: str
    ref: str
    asset_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceSpan:
    """``[start, end)`` of one recording, on its common clock, as the edit uses it."""

    asset_id: str
    start: Rational
    end: Rational
    clip_id: str
    #: The boundary moment inside the span (outgoing: its end; incoming: its start).
    boundary: Rational
    shot_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlternativeRef:
    asset_id: str
    shot_id: str | None
    start: Rational
    end: Rational
    #: Whether its time mapping to this edit was verified (sync evidence), or it is a direction.
    range_verified: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Repair:
    repair_id: str
    kind: RepairKind
    owner: Owner
    availability: Availability
    description: str
    #: 0 trivial .. 1 needs new footage; ``None`` when it cannot be judged.
    difficulty: float | None
    tradeoffs: tuple[str, ...]
    rank: int = 0
    #: A seconds value the repair names (a cleaner cut point), on the outgoing clip's source clock.
    at_source_seconds: Rational | None = None
    alternative: AlternativeRef | None = None
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    issue_id: str
    type: IssueType
    scope: Scope
    level: Level
    transition_id: str | None
    spans: tuple[SourceSpan, ...]
    #: Where the cut lies in the edit (exact seconds from its start).
    timeline_at: Rational | None
    section_ids: tuple[str, ...]
    scores: tuple[Score, ...]
    detectability_confidence: float | None
    intentional_style_likelihood: float | None
    style_reason: str
    explanation: str
    why_it_matters: str
    evidence: tuple[EvidenceRef, ...]
    repairs: tuple[Repair, ...]
    recommended: RepairKind | None
    related_assets: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    identity: IdentityVerdict | None = None
    #: The finding is worth showing the editing stage (not merely informational).
    surfaced: bool = True
    decision: Decision | None = None
    decision_note: str = ""

    def score(self, name: ScoreName) -> Score | None:
        return next((s for s in self.scores if s.name is name), None)

    @property
    def peak(self) -> float | None:
        """The strongest problem score of the issue (risk-direction), for ranking."""
        values = [
            1.0 - s.value if s.name is ScoreName.SPATIAL_CONTINUITY else s.value
            for s in self.scores
            if s.value is not None and s.name not in (ScoreName.ACTION_CONTINUITY,)
        ]
        action = self.score(ScoreName.ACTION_CONTINUITY)
        if action is not None and action.value is not None:
            values.append(1.0 - action.value)
        return max(values) if values else None


@dataclass(frozen=True, slots=True, kw_only=True)
class Unresolved:
    """A cut the evidence cannot decide, and what is missing."""

    transition_id: str
    question: str
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CompatiblePair:
    """Two shots judged compatible, with what the judgement rests on."""

    transition_id: str
    outgoing: SourceSpan
    incoming: SourceSpan
    #: 1 - the largest measured difference; only from evidence that was available.
    compatibility: float
    checked: tuple[str, ...]
    #: ``edit`` for the pair the edit uses; ``alternative`` for a supplied candidate.
    origin: str = "edit"
    alternative: AlternativeRef | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionRecord:
    transition_id: str
    fingerprint: str
    kind: TransitionKind
    outgoing_clip: str
    incoming_clip: str
    #: Where the cut leaves one recording and enters the next (source seconds, common clock).
    outgoing_asset: str
    outgoing_at: Rational
    incoming_asset: str
    incoming_at: Rational
    timeline_at: Rational
    #: What was judged from and what was missing (``video``, ``audio``, ``alignment`` ...).
    evidence_present: tuple[str, ...]
    evidence_missing: tuple[str, ...]
    #: ``True`` when the judgement was reused from the previous analysis (unchanged cut).
    reused: bool
    skipped_reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Metrics:
    transitions: int
    evaluated: int
    reused: int
    skipped: int
    issues: int
    unresolved: int
    #: Share of the judged transitions that had picture / sound evidence on both sides.
    picture_coverage: float
    sound_coverage: float
    #: Expensive model calls made by this module (it makes none: it reads stored results).
    model_calls: int = 0


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
class ValidationIssue:
    issue_id: str
    severity: Severity
    subject: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    analyzer_version: int
    schema_version: int
    vocabulary_version: int
    profile_name: str
    profile_version: int
    strictness: float
    configuration_fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class IssueDecision:
    """A person's verdict on a finding, carried through reanalysis."""

    issue_id: str
    decision: Decision
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuityAnalysis:
    continuity_analysis_id: str
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    state: ResultState
    edit_plan_id: str
    edit_plan_version: int
    profile: str
    strictness: float
    sources: tuple[str, ...]
    transitions: tuple[TransitionRecord, ...]
    issues: tuple[Issue, ...]
    unresolved: tuple[Unresolved, ...]
    compatible_pairs: tuple[CompatiblePair, ...]
    decisions: tuple[IssueDecision, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    metrics: Metrics
    validation: tuple[ValidationIssue, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int = field(default=SCHEMA_VERSION)

    def issue(self, issue_id: str) -> Issue | None:
        return next((i for i in self.issues if i.issue_id == issue_id), None)

    def issues_at(self, transition_id: str) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.transition_id == transition_id)

    def surfaced(self) -> tuple[Issue, ...]:
        """The findings the editing stage should weigh (not rejected, worth showing)."""
        return tuple(i for i in self.issues if i.surfaced and i.decision is not Decision.REJECTED)

    def risk_at(
        self,
        out_asset: str,
        leaves: float,
        in_asset: str,
        resumes: float,
        tolerance: float = 0.05,
    ) -> tuple[float | None, str] | None:
        """How risky the cut that leaves ``leaves`` of one recording and resumes at ``resumes`` of
        another (or the same) one is.

        ``None`` when this analysis judged no such cut. Otherwise the risk (from the strongest
        issue a person has not rejected: high 0.85, medium 0.6, low 0.35, informational 0.15;
        0.1 when the cut was judged clean; ``None`` when it could not be judged) and the reason."""
        found = next(
            (
                r
                for r in self.transitions
                if r.outgoing_asset == out_asset
                and r.incoming_asset == in_asset
                and abs(r.outgoing_at.numerator / r.outgoing_at.denominator - leaves) <= tolerance
                and abs(r.incoming_at.numerator / r.incoming_at.denominator - resumes) <= tolerance
            ),
            None,
        )
        if found is None:
            return None
        weight = {Level.HIGH: 0.85, Level.MEDIUM: 0.6, Level.LOW: 0.35, Level.INFO: 0.15}
        every = self.issues_at(found.transition_id)
        issues = [i for i in every if i.decision is not Decision.REJECTED]
        if issues:
            top = max(issues, key=lambda i: weight[i.level])
            return weight[top.level], f"continuity analysis: {top.explanation} ({top.level.value})"
        if every:
            return 0.1, "continuity analysis: the findings were rejected by a person"
        if "picture" not in found.evidence_present and not found.reused:
            return None, "continuity analysis: no picture evidence for this cut"
        return 0.1, "continuity analysis: no break found"

    def risk_between(
        self, asset_id: str, leaves: float, resumes: float, tolerance: float = 0.05
    ) -> tuple[float | None, str] | None:
        """``risk_at`` for a cut inside one recording."""
        return self.risk_at(asset_id, leaves, asset_id, resumes, tolerance)

    def record(self, transition_id: str) -> TransitionRecord | None:
        return next((t for t in self.transitions if t.transition_id == transition_id), None)

    def issues_of(self, severity: Severity) -> list[ValidationIssue]:
        return [v for v in self.validation if v.severity is severity]
