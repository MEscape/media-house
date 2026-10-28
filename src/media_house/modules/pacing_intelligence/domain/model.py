"""The pacing analysis: typed, immutable, backend-neutral and traceable. JSON is its storage form.

Rules every part obeys:

* Missing is not zero: a measure that could not be computed is ``None`` with a reason.
* Every measure names its ``Basis`` (measured / derived / heuristic); none is a probability.
* A recommendation names the passage, the evidence it rests on, how sure it is and why that is not
  more, what it would gain and what it would cost. It never says which source material to remove or
  edits anything: which operation to apply is the editing stage's decision.
* Times are exact ``Rational`` seconds on ONE recording's timeline; the story-wide runtime is a
  sum of durations, never a shared clock.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.pacing_intelligence.domain.values import (
    SCHEMA_VERSION,
    Basis,
    EvidenceKind,
    InputHow,
    IssueKind,
    Level,
    PaceClass,
    PauseVerdict,
    Precision,
    ReasonCode,
    RecommendationType,
    ResultState,
    Safety,
    Severity,
    StageState,
    Strategy,
    optional_unit_interval,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Measure:
    """One named score with its scale and where it comes from. ``value`` is ``None`` if unknown."""

    value: float | None
    basis: Basis
    #: Why it is unknown, or what it rests on.
    note: str = ""

    def __post_init__(self) -> None:
        optional_unit_interval(self.value, "measure")


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSpan:
    """``[start, end)`` of one recording, in exact seconds, with where the times come from."""

    asset_id: str
    start: Rational
    end: Rational
    precision: Precision
    #: Frame indices (and the rate) only for a span that is a whole shot of a known rate.
    start_frame: int | None = None
    end_frame: int | None = None
    frame_rate: Rational | None = None

    @property
    def seconds(self) -> float:
        return self.end.value - self.start.value


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRef:
    kind: EvidenceKind
    ref: str
    asset_id: str | None = None
    detail: str = ""
    value: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PassageAnalysis:
    """A stretch of speech: a content segment, or a run of speech between long gaps."""

    passage_id: str
    span: TimeSpan
    section_id: str | None
    beat_id: str | None
    words: int
    elapsed_seconds: float
    speech_seconds: float
    #: Words per minute of speech-active time and of elapsed time (see ``values``).
    speech_rate_wpm: float | None
    elapsed_rate_wpm: float | None
    pause_seconds: float
    pause_ratio: float | None
    longest_pause: float
    pace: PaceClass
    information_density: Measure
    cognitive_load: Measure
    repetition: Measure
    visual_activity: Measure
    shot_count: int
    cuts_per_minute: float | None
    mean_shot_seconds: float | None
    speaker: str | None
    confidence: float | None
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "passage confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class PauseAnalysis:
    pause_id: str
    span: TimeSpan
    duration: float
    silence_ratio: float | None
    verdict: PauseVerdict
    #: What remains of the pause if tightened; ``None`` when it should be kept as is.
    recommended_keep: float | None
    section_id: str | None
    reason_codes: tuple[ReasonCode, ...]
    evidence: tuple[EvidenceRef, ...]
    confidence: float | None

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "pause confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotAnalysis:
    shot_id: str
    span: TimeSpan
    duration: float
    #: This shot's length over the median of the shots around it (1 = typical).
    relative_duration: float | None
    visual_activity: Measure
    camera: str | None
    reason_codes: tuple[ReasonCode, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionAnalysis:
    section_id: str
    label: str
    order: int
    priority: str
    seconds: float | None
    words: int
    speech_rate_wpm: float | None
    information_density: Measure
    cognitive_load: Measure
    #: Share of the story's runtime.
    share: float | None
    #: Seconds from the start of the story to the first main-point beat inside this section.
    first_main_point_at: float | None
    reason_codes: tuple[ReasonCode, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AttentionBin:
    start: Rational
    end: Rational
    #: How many attention-relevant changes fall here, scaled to [0, 1]. A PREDICTION from the
    #: picture, the delivery and the content; no audience was measured.
    estimate: float
    changes: tuple[str, ...]

    def __post_init__(self) -> None:
        optional_unit_interval(self.estimate, "attention estimate")


@dataclass(frozen=True, slots=True, kw_only=True)
class RhythmFinding:
    span: TimeSpan
    tempo_bpm: float
    strength: float
    beat_count: int
    #: Share of the picture's cuts inside this window that land within tolerance of a beat.
    cuts_on_beat: float | None
    cut_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineAnalysis:
    """Whole-recording rhythm. One per recording: recordings share no clock."""

    asset_id: str
    seconds: float | None
    speech_seconds: float
    mean_speech_rate_wpm: float | None
    #: Spread of the passages' speech rate (coefficient of variation) and of the shots' lengths.
    rate_variation: float | None
    shot_duration_variation: float | None
    cuts_per_minute: float | None
    pacing_consistency: Measure
    attention: tuple[AttentionBin, ...]
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Recommendation:
    recommendation_id: str
    type: RecommendationType
    level: Level
    target: TimeSpan
    section_id: str | None
    beat_id: str | None
    observed: str
    proposal: str
    #: Change in runtime if applied, seconds (negative shortens). ``None`` = not estimable.
    delta_seconds: float | None
    recommended_duration: float | None
    reason_codes: tuple[ReasonCode, ...]
    evidence: tuple[EvidenceRef, ...]
    confidence: float | None
    confidence_limits: tuple[str, ...]
    benefit: str
    tradeoffs: tuple[str, ...]
    safety: Safety
    #: 1 = first. Ordered by the stated ``rank_basis``; ranking is not certainty.
    priority: int
    rank_basis: str
    speed_up_score: Measure
    slow_down_score: Measure
    cut_tightening_score: Measure
    #: Strategies under which this recommendation applies.
    strategies: tuple[Strategy, ...]
    prerequisites: tuple[str, ...] = ()
    contributes_to_target: bool = False

    def __post_init__(self) -> None:
        optional_unit_interval(self.confidence, "recommendation confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetCheck:
    minimum: float | None
    maximum: float | None
    preferred_low: float | None
    preferred_high: float | None
    #: ``within``, ``over``, ``under`` (relative to the proposed runtime).
    status: str
    #: Seconds still missing/exceeding after every safe recommendation, 0 when met.
    shortfall_seconds: float
    note: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DurationEstimate:
    #: Sum of the recordings' lengths (separate clocks, added).
    source_seconds: float | None
    #: Runtime of the material as planned (the story's section lengths, else speech plus pauses).
    current_seconds: float | None
    current_basis: str
    #: Current runtime with the selected strategy's recommendations applied.
    proposed_seconds: float | None
    contraction_seconds: float
    expansion_seconds: float
    assumptions: tuple[str, ...]
    target: TargetCheck | None


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyProjection:
    strategy: Strategy
    recommendations: int
    contraction_seconds: float
    expansion_seconds: float
    proposed_seconds: float | None
    #: ``None`` = no target or no estimate.
    meets_target: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    issue_id: str
    kind: IssueKind
    severity: Severity
    detail: str
    subject: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    #: story_plan, audio_analysis, content_analysis, video_analysis, alignment, inspection.
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
class SourceRecord:
    asset_id: str
    name: str
    duration: float | None
    frame_rate: Rational | None
    #: Which analyses informed the result: audio / content / video / alignment.
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryRef:
    story_plan_id: str
    plan_version: int
    stored_as: str | None
    target_format: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    analyzer_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    strategy: Strategy
    configuration_fingerprint: str
    #: Identity of every upstream result used (kind -> identity), so a consumer can tell what the
    #: analysis was based on.
    upstream: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingAnalysis:
    """How the rhythm of the material helps or hurts it, and what would help, with evidence."""

    pacing_analysis_id: str
    #: 1 for the first analysis of these recordings and profile; later ones count up (history kept).
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    state: ResultState
    profile: str
    strategy: Strategy
    story: StoryRef | None
    sources: tuple[SourceRecord, ...]
    passages: tuple[PassageAnalysis, ...]
    pauses: tuple[PauseAnalysis, ...]
    shots: tuple[ShotAnalysis, ...]
    sections: tuple[SectionAnalysis, ...]
    timeline: tuple[TimelineAnalysis, ...]
    rhythm: tuple[RhythmFinding, ...]
    recommendations: tuple[Recommendation, ...]
    duration: DurationEstimate
    strategy_projections: tuple[StrategyProjection, ...]
    summary: str
    issues: tuple[Issue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    #: What this analysis cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION
    metadata: tuple[tuple[str, str], ...] = field(default=())

    # --- lookups -----------------------------------------------------------------------------
    def recommendation(self, recommendation_id: str) -> Recommendation | None:
        return next(
            (r for r in self.recommendations if r.recommendation_id == recommendation_id), None
        )

    def recommendations_of(self, kind: RecommendationType) -> tuple[Recommendation, ...]:
        return tuple(r for r in self.recommendations if r.type is kind)

    def passage(self, passage_id: str) -> PassageAnalysis | None:
        return next((p for p in self.passages if p.passage_id == passage_id), None)

    def pauses_of(self, verdict: PauseVerdict) -> tuple[PauseAnalysis, ...]:
        return tuple(p for p in self.pauses if p.verdict is verdict)

    def issues_of(self, severity: Severity) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is severity)

    def section(self, section_id: str) -> SectionAnalysis | None:
        return next((s for s in self.sections if s.section_id == section_id), None)
