"""The stored transition plan (``transition_plan`` document, contracts sec. 5 envelope).

One ``Transition`` per boundary of the main picture layer (plus the start and the end of the
edit). A hard cut is an explicit decision (intensity 0, no duration, no asset). Times: exact
``Rational`` timeline seconds; frames are computed once at the edge (``round(t * fps)``, half to
even) and a duration in frames is ``end_frame - start_frame``.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.transition_engine.domain.facts import EditRef
from media_house.modules.transition_engine.domain.profile import TransitionDecision
from media_house.modules.transition_engine.domain.values import (
    SCHEMA_VERSION,
    Alignment,
    AudioKind,
    BoundaryKind,
    Context,
    Curve,
    DecisionStatus,
    Family,
    InputHow,
    QcCategory,
    RenderState,
    ResultState,
    Severity,
    StageState,
    TransitionKind,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue:
    code: str
    severity: Severity
    category: QcCategory
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Handles:
    """Frames a transition needs beyond the cut on each side, and what the sources hold.

    ``required_out``: frames of the outgoing recording after its out point; ``required_in``:
    frames of the incoming recording before its in point. ``None`` available = not known."""

    required_out: int
    required_in: int
    available_out: int | None
    available_in: int | None

    @property
    def sufficient(self) -> bool:
        return (self.required_out == 0 or (self.available_out or 0) >= self.required_out) and (
            self.required_in == 0 or (self.available_in or 0) >= self.required_in
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Rejection:
    """A candidate that was considered and not used, and why (compatibility results)."""

    kind: TransitionKind
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Fallback:
    """What was wanted and what was used instead."""

    wanted: TransitionKind
    used: TransitionKind
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Overlay:
    """A graphic transition: ``motion_graphics_engine`` renders a full-frame overlay that covers
    the cut at its midpoint (no source handles needed)."""

    request_id: str
    overlay: str
    timeline_start: Rational
    local_duration: Rational
    render_state: RenderState = RenderState.NOT_REQUESTED
    asset_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioTransition:
    """The sound at the boundary, planned independently of the picture.

    ``timeline_at`` is where the sound changes (differs from the picture cut for a J/L cut);
    the fade runs ``before`` seconds before it and ``after`` seconds after it (asymmetric when a
    word had to be protected). Samples are at ``sample_rate``."""

    kind: AudioKind
    curve: Curve
    timeline_at: Rational
    before: Rational
    after: Rational
    samples_before: int
    samples_after: int
    sample_rate: int
    duration_ms: float
    #: Word edges that shortened the fade (``out:12.340`` / ``in:3.100`` source seconds).
    protected: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Transition:
    transition_id: str
    boundary: BoundaryKind
    outgoing_clip_id: str | None
    incoming_clip_id: str | None
    section_id: str | None
    #: The cut on the timeline (frame-snapped) and its frame.
    cut_at: Rational
    cut_frame: int
    contexts: tuple[Context, ...]
    status: DecisionStatus
    required: bool
    kind: TransitionKind
    family: Family
    intensity: int
    preset_id: str
    alignment: Alignment
    duration_frames: int
    duration: Rational
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    #: ``left``/``right`` (slide, push, wipe, whip pan) or ``in``/``out`` (zoom); else empty.
    direction: str
    handles: Handles
    audio: AudioTransition
    reasons: tuple[str, ...]
    confidence: float
    evidence: tuple[str, ...] = ()
    rejected: tuple[Rejection, ...] = ()
    fallback: Fallback | None = None
    overlay: Overlay | None = None
    decision_note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionEvent:
    """A transition music_and_sound_design may sync to (``SyncEvent`` through the pipeline)."""

    transition_id: str
    kind: TransitionKind
    timeline_at: Rational
    intensity: int

    @property
    def strength(self) -> float:
        return self.intensity / 4


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleBinding:
    """The style a plan used (``StyleRef``; ``document_id`` ``None`` for an unstored style)."""

    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str


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
    planner_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    configuration_fingerprint: str
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Metrics:
    boundaries: int
    transitions: int
    hard_cuts: int
    fallbacks: int
    overlays: int
    #: Count per intensity 0..4.
    by_intensity: tuple[int, ...]
    per_minute: float


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionPlan:
    plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    edit: EditRef | None
    style: StyleBinding | None
    created_at: datetime
    state: ResultState
    profile: str
    frame_rate: Rational
    frame_rate_assumed: bool
    transitions: tuple[Transition, ...]
    events: tuple[TransitionEvent, ...]
    metrics: Metrics
    #: The person decisions this plan honoured (carried into the next plan of the edit).
    decisions: tuple[TransitionDecision, ...]
    checks: tuple[QcIssue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int = field(default=SCHEMA_VERSION)

    def transition(self, transition_id: str) -> Transition | None:
        return next((t for t in self.transitions if t.transition_id == transition_id), None)

    def between(self, outgoing_clip_id: str, incoming_clip_id: str) -> Transition | None:
        return next(
            (
                t
                for t in self.transitions
                if t.outgoing_clip_id == outgoing_clip_id and t.incoming_clip_id == incoming_clip_id
            ),
            None,
        )
