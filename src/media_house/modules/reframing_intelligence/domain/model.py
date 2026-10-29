"""The reframing analysis: typed, versioned, grounded. JSON is only its storage form.

Rules every part obeys:

* A recommendation names the source range it affects (exact ``Rational`` seconds on the recording's
  common clock). Timeline places never appear: the editing stage places a range, a timeline
  builder maps source time to timeline time.
* A crop is carried by keyframes. The first keyframe is at the start of the range. Between two
  keyframes the first one's ``interpolation`` applies: ``hold`` keeps its crop until the next
  keyframe, ``linear`` moves x, y, w and h linearly (the aspect stays exact). After the last
  keyframe its crop holds to the end of the range. No keyframe lies outside ``[start, end]`` and
  no crop movement crosses a cut: every shot has its own keyframes.
* A recommendation that cannot be carried by a crop says so (``unreframeable``) and lists what else
  could serve, each alternative with what executing it would need.
* Observed facts, geometric results, quality estimates and aesthetic preferences are separate
  (``Score.basis``); nothing is merged into one number.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.reframing_intelligence.domain.geometry import (
    Crop,
    TargetFormat,
    interpolate,
)
from media_house.modules.reframing_intelligence.domain.profile import SafeArea
from media_house.modules.reframing_intelligence.domain.values import (
    SCHEMA_VERSION,
    CameraMotion,
    Decision,
    Feasibility,
    Importance,
    InputHow,
    Interpolation,
    IntervalState,
    IssueKind,
    ResultState,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    Strategy,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Score:
    name: ScoreName
    #: ``None`` = could not be stated (never read as good or bad).
    value: float | None
    basis: ScoreBasis
    reason: str = ""

    def __post_init__(self) -> None:
        if self.value is not None and not 0.0 <= self.value <= 1.0:
            raise InvariantViolation(f"Score {self.name.value} lies within [0, 1]")
        if self.value is None and not self.reason:
            raise InvariantViolation(f"Score {self.name.value} is unstated without a reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class CropKeyframe:
    time: Rational
    crop: Crop
    interpolation: Interpolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Element:
    """One thing the crop is judged against."""

    track_id: str
    kind: str
    label: str
    importance: Importance
    #: A required element must stay in the picture; the others are context.
    required: bool
    reason: str
    #: Share of the shot during which the element was tracked (0-1).
    coverage: float
    #: Mean tracker confidence over its points.
    confidence: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackingInterval:
    start: Rational
    end: Rational
    state: IntervalState
    track_id: str | None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Alternative:
    """Something else that could serve the shot, and what it would take."""

    strategy: Strategy
    summary: str
    #: ``None`` when the alternative was named but not evaluated.
    feasibility: Feasibility | None
    #: Whether a downstream stage can carry it out as it stands.
    executable: bool
    #: What is missing for it to run (a capability, evidence, a decision).
    requires: str | None = None
    asset_id: str | None = None
    shot_id: str | None = None
    start: Rational | None = None
    end: Rational | None = None
    #: One crop per element it frames separately (alternating crops), or the crop it would use.
    crops: tuple[Crop, ...] = ()
    #: Required elements it would leave out.
    omits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    kind: IssueKind
    severity: Severity
    detail: str
    subject: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    kind: str
    ref: str
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Confidence:
    """How well the evidence covers the shot (ordinal, not a probability). ``None``: no subject
    evidence at all."""

    value: float | None
    components: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Resolution:
    """Output pixels per stored sample (>1 enlarges) over the keyframes; ``None``: not stated."""

    scale_min: float | None
    scale_max: float | None
    #: ``False``: only the display size was known, so samples were assumed to equal it.
    exact: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Recommendation:
    recommendation_id: str
    #: ``asset_id:shot_id``: what a person's decision follows through reanalysis.
    segment_key: str
    #: Identity of everything this recommendation rests on; unchanged = carried over.
    fingerprint: str
    asset_id: str
    shot_id: str
    scene_id: str | None
    start: Rational
    end: Rational
    section_ids: tuple[str, ...]
    beat_ids: tuple[str, ...]
    strategy: Strategy
    camera_motion: CameraMotion
    feasibility: Feasibility
    #: A downstream stage may apply the crop as it stands (valid, evidence-backed).
    executable: bool
    keyframes: tuple[CropKeyframe, ...]
    elements: tuple[Element, ...]
    intervals: tuple[TrackingInterval, ...]
    scores: tuple[Score, ...]
    resolution: Resolution
    confidence: Confidence
    #: What should change and why, which beat it serves.
    reasons: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    #: Why this strategy over the feasible ones it was preferred to.
    preferred_over: tuple[str, ...]
    alternatives: tuple[Alternative, ...]
    issues: tuple[Issue, ...]
    evidence: tuple[Evidence, ...]
    #: The main subject's height, centre x and top across the output at the start and the end of
    #: the range (what a neighbouring shot's framing is compared with), and its anonymous cluster.
    subject_start: tuple[float, float, float] | None = None
    subject_end: tuple[float, float, float] | None = None
    subject_cluster: str | None = None
    decision: Decision | None = None
    decision_note: str = ""

    def __post_init__(self) -> None:
        if self.strategy.has_crop and not self.keyframes:
            raise InvariantViolation("A crop strategy needs keyframes")
        if not self.strategy.has_crop and self.keyframes:
            raise InvariantViolation(f"{self.strategy.value} carries no crop")
        if self.keyframes and self.keyframes[0].time != self.start:
            raise InvariantViolation("The first keyframe lies at the start of the range")

    def score(self, name: ScoreName) -> float | None:
        found = next((s for s in self.scores if s.name is name), None)
        return None if found is None else found.value

    @property
    def recommended_crop(self) -> Crop | None:
        """The window at the start of the range (the whole answer for a still crop)."""
        return self.keyframes[0].crop if self.keyframes else None

    def crop_at(self, seconds: float) -> Crop | None:
        """The window at a source time inside the range (``None`` when no crop carries it)."""
        return crop_at(self.keyframes, seconds) if self.keyframes else None


@dataclass(frozen=True, slots=True, kw_only=True)
class PairCheck:
    """How the framing of two shots that may meet in a cut sits together."""

    first_id: str
    second_id: str
    #: Larger over smaller subject height in the output (1 = the same size).
    scale_ratio: float | None
    #: Difference of the subject's place across the output (0-1).
    shift: float | None
    headroom_delta: float | None
    #: Where the pair comes from: a cut the stored continuity analysis judged, or recording order.
    origin: str
    #: Risk of a jump cut the continuity analysis stated for this cut, if any.
    jump_cut_risk: float | None
    #: ``hides``, ``does_not_change`` or ``unknown``: what the crops do to a known jump cut.
    jump_cut_effect: str
    score: float | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRecord:
    asset_id: str
    display_width: int
    display_height: int
    sample_width: int
    sample_height: int
    rotation: int
    geometry_exact: bool
    clock_correction_seconds: float
    clock_corrected: bool
    shots: int
    #: Identity of the picture analysis it was read from.
    analysis_identity: str


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
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    analyzer_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    configuration_fingerprint: str
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Metrics:
    shots: int
    evaluated: int
    reused: int
    original: int
    static: int
    dynamic: int
    unreframeable: int
    uncertain: int
    #: Track samples read; no model is run by this module.
    samples: int
    model_calls: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ReframingAnalysis:
    reframing_analysis_id: str
    #: 1 for the first analysis of these recordings, target and profile; later ones count up.
    analysis_version: int
    supersedes: str | None
    project_id: str | None
    created_at: datetime
    state: ResultState
    target: TargetFormat
    profile: str
    safe_areas: tuple[SafeArea, ...]
    sources: tuple[SourceRecord, ...]
    story_plan_id: str | None
    story_plan_version: int | None
    recommendations: tuple[Recommendation, ...]
    pairs: tuple[PairCheck, ...]
    metrics: Metrics
    issues: tuple[Issue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    #: What this result cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION
    metadata: tuple[tuple[str, str], ...] = field(default=())

    def recommendation(self, recommendation_id: str) -> Recommendation | None:
        return next(
            (r for r in self.recommendations if r.recommendation_id == recommendation_id), None
        )

    def covering(self, asset_id: str, start: Rational, end: Rational) -> list[Recommendation]:
        """The recommendations of a recording overlapping ``[start, end)``, in time order."""
        lo, hi = _frac(start), _frac(end)
        found = [
            r
            for r in self.recommendations
            if r.asset_id == asset_id and _frac(r.start) < hi and _frac(r.end) > lo
        ]
        return sorted(found, key=lambda r: (_frac(r.start), r.recommendation_id))

    def unreframeable(self) -> list[Recommendation]:
        return [r for r in self.recommendations if r.strategy is Strategy.UNREFRAMEABLE]

    def issues_of(self, severity: Severity) -> list[Issue]:
        found = [i for i in self.issues if i.severity is severity]
        for r in self.recommendations:
            found += [i for i in r.issues if i.severity is severity]
        return found


def crop_at(keyframes: Sequence[CropKeyframe], seconds: float) -> Crop:
    """The window at ``seconds`` by the keyframe semantics in the module docstring: before the
    first keyframe its crop, after the last keyframe its crop, between two the first one's
    interpolation."""
    previous = keyframes[0]
    for following in keyframes[1:]:
        if following.time.value > seconds:
            break
        previous = following
    else:
        return previous.crop
    if previous.interpolation is Interpolation.HOLD:
        return previous.crop
    span = following.time.value - previous.time.value
    fraction = (seconds - previous.time.value) / span if span > 0 else 1.0
    return interpolate(previous.crop, following.crop, fraction)


def _frac(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)
