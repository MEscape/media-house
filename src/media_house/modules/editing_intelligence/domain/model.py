"""The edit plan: typed, immutable, backend-neutral and traceable. JSON is only its storage form.

Rules every part obeys:

* Source time and timeline time are separate. A ``SourceRange`` is exact seconds on a recording;
  a ``Placement`` puts a source range at an exact timeline position. Neither says which editing
  tool will compile it.
* Traceability: a decision names the story section/beat, the source range and the evidence
  (``EvidenceRef``) it rests on. No timestamp, event or score is invented: a value that could not
  be computed is ``None`` and the reason is stated.
* Scores are separate signals (``Signal``): none is combined into another silently, none is a
  probability.
* A decision is ``executable`` only when meaning and boundary safety are established; otherwise
  it is a suggestion (``needs_review``) or ``unresolved``.
* The plan never edits media and never names a timeline backend.
"""

from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.values import (
    SCHEMA_VERSION,
    Action,
    AudioBehavior,
    BridgeKind,
    CheckState,
    ConstraintKind,
    ConstraintState,
    DurationMode,
    EffectIntent,
    EffectKind,
    EvidenceKind,
    Executability,
    InputHow,
    IssueKind,
    Origin,
    PlacementRole,
    PlanState,
    Precision,
    Severity,
    SignalBasis,
    SignalName,
    StageState,
    StructuralKind,
    TrackKind,
    TrimKind,
    optional_unit_interval,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Signal:
    name: SignalName
    #: In [0, 1]; ``None`` = not computable from the evidence (see ``reason``). Never 0 by default.
    value: float | None
    basis: SignalBasis
    reason: str

    def __post_init__(self) -> None:
        optional_unit_interval(self.value, f"signal {self.name.value}")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRef:
    """Where a fact behind a decision comes from: an id in the owning module's result."""

    kind: EvidenceKind
    ref: str
    asset_id: str | None = None
    detail: str = ""
    value: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRange:
    """A half-open span ``[start, end)`` of one recording, in exact seconds on its container
    timeline. Frames and samples are DERIVED positions on the recording's own grid and exist only
    when that grid is known; ``precision`` says where each end comes from."""

    asset_id: str
    start: Rational
    end: Rational
    start_precision: Precision
    end_precision: Precision
    #: The recording's constant frame rate; ``None`` when unknown or variable (no frames offered).
    frame_rate: Rational | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    sample_rate: int | None = None
    start_sample: int | None = None
    end_sample: int | None = None

    def __post_init__(self) -> None:
        if Fraction(self.end.numerator, self.end.denominator) < Fraction(
            self.start.numerator, self.start.denominator
        ):
            raise InvariantViolation("A source range must not end before it starts")

    @property
    def seconds(self) -> float:
        return self.end.value - self.start.value


@dataclass(frozen=True, slots=True, kw_only=True)
class CutAlternative:
    time: Rational
    kind: str
    reason: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CutPoint:
    """A proposed boundary, how far it can be trusted and the other places that were considered."""

    time: Rational
    precision: Precision
    #: ``cut_point_confidence``: evidence that the boundary is safe. ``None`` = no evidence.
    confidence: float | None
    reason: str
    alternatives: tuple[CutAlternative, ...] = ()

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "cut point confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class Trim:
    """Something removed from inside kept speech."""

    kind: TrimKind
    range: SourceRange
    reason: str
    executability: Executability
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    decision_id: str
    action: Action
    origin: Origin
    asset_id: str
    section_id: str | None
    beat_id: str | None
    #: The source range the decision is about (for a reorder: the section's span).
    subject: SourceRange | None
    unit_ids: tuple[str, ...]
    signals: tuple[Signal, ...]
    executability: Executability
    rationale: tuple[str, ...]
    #: Hard constraints that applied to this decision (why it could not go another way).
    constraints: tuple[str, ...] = ()
    trims: tuple[Trim, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    warnings: tuple[str, ...] = ()
    #: For a manual decision: the person's own words.
    note: str | None = None

    def signal(self, name: SignalName) -> Signal | None:
        return next((s for s in self.signals if s.name is name), None)

    def value(self, name: SignalName) -> float | None:
        found = self.signal(name)
        return None if found is None else found.value


@dataclass(frozen=True, slots=True, kw_only=True)
class Placement:
    """One clip on one track of the timeline."""

    placement_id: str
    track: TrackKind
    #: 0 is the main layer of the track; cover footage sits above it.
    layer: int
    role: PlacementRole
    source: SourceRange
    timeline_start: Rational
    timeline_duration: Rational
    #: Whole timeline frames, rounded; the exact seconds above are the authority.
    start_frame: int
    duration_frames: int
    section_id: str | None
    beat_id: str | None
    decision_ids: tuple[str, ...]
    audio: AudioBehavior
    fade_in: Rational
    fade_out: Rational
    cut_in: CutPoint | None = None
    cut_out: CutPoint | None = None
    #: The clip of the other track made from the same run (picture <-> sound).
    linked: str | None = None
    #: This clip plays material that is also elsewhere in the edit (a cold open).
    reuse_of: str | None = None
    continuity_risk: Signal | None = None
    confidence: float | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "placement confidence")

    @property
    def timeline_end(self) -> Rational:
        end = Fraction(self.timeline_start.numerator, self.timeline_start.denominator) + Fraction(
            self.timeline_duration.numerator, self.timeline_duration.denominator
        )
        return Rational(end.numerator, end.denominator)


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioBridge:
    """Sound and picture change at different moments, with the overlap spelled out."""

    bridge_id: str
    kind: BridgeKind
    audio_changes_at: Rational
    picture_changes_at: Rational
    #: The video clips involved (the cover footage that carries the picture across).
    placement_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverCandidate:
    asset_id: str
    shot_id: str
    range: SourceRange
    #: Alignment's ordinal grade for the pairing; evidence, not a recommendation.
    grade: str
    rank: int
    executability: Executability
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageRequest:
    """A place where the picture would gain from other footage or a graphic."""

    request_id: str
    section_id: str
    #: ``demonstration``, ``evidence``, ``illustration`` ... as the story names the purpose.
    purpose: str
    necessity: str
    #: The verbatim phrase or key terms to show. Never invented.
    concept: str
    span: SourceRange
    need: Signal
    #: The footage chosen, if any candidate qualified.
    chosen: CoverCandidate | None
    alternatives: tuple[CoverCandidate, ...]
    placement_id: str | None
    executability: Executability
    notes: tuple[str, ...] = ()
    decision_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectRequest:
    """Something a later stage should add; the editor states intent and timing, nothing more."""

    request_id: str
    kind: EffectKind
    intent: EffectIntent
    at: Rational
    #: ``None`` = instantaneous or decided by the later stage.
    duration: Rational | None
    placement_id: str | None
    section_id: str | None
    priority: float
    reason: str
    decision_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Constraint:
    constraint_id: str
    kind: ConstraintKind
    name: str
    state: ConstraintState
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    issue_id: str
    kind: IssueKind
    severity: Severity
    detail: str
    #: The decision, placement or section it is about.
    subject: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Check:
    """One validation that was or was not possible. ``NOT_CHECKED`` is never a pass."""

    name: str
    state: CheckState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuralProposal:
    proposal_id: str
    kind: StructuralKind
    section_ids: tuple[str, ...]
    rationale: str
    #: Seconds the change would save (negative: add), when estimable.
    effect_seconds: float | None
    applied: bool
    #: Why it was or was not applied.
    status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingNote:
    note_id: str
    #: ``section`` or ``edit``.
    level: str
    subject: str
    detail: str
    seconds: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRecord:
    low: float | None
    high: float | None
    mode: DurationMode
    #: ``caller`` or ``story``.
    origin: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EditDuration:
    #: Sum of the main clips; transitions are assumed to add no length.
    estimated: Rational
    #: ``False``: nothing has compiled a timeline, so this is an estimate, not a measured length.
    validated: bool
    assumptions: tuple[str, ...]
    #: What the essential material alone would take.
    essential_seconds: float | None
    target: TargetRecord | None
    met: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Timebase:
    frame_rate: Rational
    #: ``True`` when the rate was chosen here (no recording fixed one, or none was configured).
    assumed: bool
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRecord:
    asset_id: str
    name: str
    duration: float | None
    frame_rate: Rational | None
    variable_frame_rate: bool | None
    has_video: bool | None
    has_audio: bool | None
    #: Which analyses informed the edit: content / audio / video / alignment.
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryRef:
    story_plan_id: str
    plan_version: int
    #: Library id of the stored story document, when it came from the library.
    stored_as: str | None
    target_format: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ManualOverride:
    """A person's decision about one decision. Protected from automatic replacement."""

    decision_id: str
    action: Action
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    #: story_plan, content_analysis, audio_analysis, video_analysis, alignment, inspection.
    kind: str
    how: InputHow
    asset_id: str | None = None
    identity: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Confidence:
    #: Mean of the components, [0, 1]. Strength of evidence, not a probability.
    value: float
    #: ``boundaries`` (mean cut-point confidence), ``time_basis`` (share of ranges with a
    #: measured basis), ``evidence`` (share of analyses present), ``executable`` (share of
    #: executable decisions).
    components: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        optional_unit_interval(self.value, "plan confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    editor_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    configuration_fingerprint: str
    #: Models behind model-made values; empty while none is used.
    engines: tuple[tuple[str, str], ...] = ()
    #: Optional advisors consulted (name -> identity); empty when none was.
    advisors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class EditPlan:
    """What to keep, cut, shorten, move and cover, as a plan a timeline compiler can validate."""

    edit_plan_id: str
    #: 1 for the first plan of these sources and profile; later plans count up (history is kept).
    plan_version: int
    #: Library id of the plan this one follows, or ``None`` for the first.
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    state: PlanState
    story: StoryRef
    profile: str
    target_format: str
    timebase: Timebase
    sources: tuple[SourceRecord, ...]
    decisions: tuple[Decision, ...]
    #: The timeline, all tracks, ordered by track then position.
    placements: tuple[Placement, ...]
    audio_bridges: tuple[AudioBridge, ...]
    coverage: tuple[CoverageRequest, ...]
    effect_requests: tuple[EffectRequest, ...]
    constraints: tuple[Constraint, ...]
    issues: tuple[Issue, ...]
    checks: tuple[Check, ...]
    structural_proposals: tuple[StructuralProposal, ...]
    pacing_notes: tuple[PacingNote, ...]
    duration: EditDuration
    confidence: Confidence
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    #: What this plan cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    overrides: tuple[ManualOverride, ...] = ()
    schema_version: int = SCHEMA_VERSION
    metadata: tuple[tuple[str, str], ...] = field(default=())

    # --- lookups ---------------------------------------------------------------------------
    def decision(self, decision_id: str) -> Decision | None:
        return next((d for d in self.decisions if d.decision_id == decision_id), None)

    def placement(self, placement_id: str) -> Placement | None:
        return next((p for p in self.placements if p.placement_id == placement_id), None)

    def track(self, track: TrackKind, layer: int = 0) -> tuple[Placement, ...]:
        return tuple(
            sorted(
                (p for p in self.placements if p.track is track and p.layer == layer),
                key=lambda p: (p.timeline_start.value, p.placement_id),
            )
        )

    def decisions_for(self, action: Action) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.action is action)

    def issues_of(self, severity: Severity) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is severity)

    def check(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    def source_ranges(self) -> tuple[SourceRange, ...]:
        """Every source range the plan plays or cuts, de-duplicated."""
        ranges = [p.source for p in self.placements]
        ranges.extend(d.subject for d in self.decisions if d.subject is not None)
        return tuple(dict.fromkeys(ranges))


__all__ = [
    "AudioBridge",
    "Check",
    "Confidence",
    "Constraint",
    "CoverCandidate",
    "CoverageRequest",
    "CutAlternative",
    "CutPoint",
    "Decision",
    "EditDuration",
    "EditPlan",
    "EffectRequest",
    "EvidenceRef",
    "InputRecord",
    "Issue",
    "ManualOverride",
    "PacingNote",
    "Placement",
    "Provenance",
    "Signal",
    "SourceRange",
    "SourceRecord",
    "StageReport",
    "StoryRef",
    "StructuralProposal",
    "TargetRecord",
    "Timebase",
    "Trim",
]
