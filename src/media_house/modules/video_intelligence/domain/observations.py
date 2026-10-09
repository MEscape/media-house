"""The observations and assessments a result is made of.

Every one of them is an ``Assessed``: a state, and for ``ok`` a confidence in [0, 1] and the
frames that support it. Anything that could not be stated is ``unknown``, ``not_analyzed``,
``not_applicable``, ``not_available`` or ``failed`` WITH a reason; it is never left out.

Confidence is the strength of the measurement (the sharpness of a correlation peak, the agreement
of repeated estimates, the number of samples), not a calibrated probability. Positions are
normalised 0-1 of the display-oriented picture (see ``geometry``). Nothing here recommends an
action; names describe properties.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.core.domain import FrameTime, TimeRange
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerState,
    BoundaryKind,
    CameraMovement,
    EntityKind,
    EventKind,
    FramingType,
    GestureKind,
    MeasuredOn,
    OverlayKind,
    RelationKind,
    ScoreName,
    Stabilization,
    unit_interval,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    """The frames a statement rests on and the raw metric that shows it."""

    frames: tuple[FrameTime, ...]
    metric: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Assessed:
    """Base of every observation and assessment: a state, and for ``OK`` confidence + evidence."""

    state: AnalyzerState
    confidence: float | None = None
    evidence: tuple[Evidence, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state is AnalyzerState.OK:
            if self.confidence is None:
                raise InvariantViolation("An observation that is ok needs a confidence")
            unit_interval(self.confidence, "confidence")
            if not any(item.frames for item in self.evidence):
                raise InvariantViolation("An observation that is ok needs supporting frames")
        else:
            if self.confidence is not None:
                raise InvariantViolation(f"A {self.state.value} observation has no confidence")
            if not self.reasons:
                raise InvariantViolation(f"A {self.state.value} observation needs a reason")

    @property
    def ok(self) -> bool:
        return self.state is AnalyzerState.OK


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundaryObservation(Assessed):
    """How a shot begins or ends. The boundary frame of a gradual transition is its centre."""

    kind: BoundaryKind | None = None
    #: The frames the transition spans (gradual transitions only).
    transition: TimeRange | None = None
    #: The picture next to the boundary is black.
    black_adjacent: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class HandlesObservation(Assessed):
    """Visually stable frames at the start and end of a shot (no cut, no strong change)."""

    head_frames: int | None = None
    tail_frames: int | None = None
    head_seconds: float | None = None
    tail_seconds: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Keyframes(Assessed):
    """Frames that represent the shot: one in the middle of the steady part, the sharpest one."""

    representative: FrameTime | None = None
    sharpest: FrameTime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CameraObservation(Assessed):
    """How the camera moved, measured on ``measured_on`` footage.

    ``direction_degrees`` is the direction the CAMERA moved in the display picture: 0 right,
    90 up, counter-clockwise. ``speed`` is a fraction of the frame width per second.
    """

    movement: CameraMovement | None = None
    #: 0 (none) to 1 (full scale); see ``MotionSettings.full_scale_speed``.
    intensity: float | None = None
    direction_degrees: float | None = None
    speed: float | None = None
    #: Rate of the natural logarithm of the picture scale (positive: content grows).
    zoom_rate: float | None = None
    #: Rms deviation of the camera path from its smoothed course (fraction of frame width).
    shake_residual: float | None = None
    measured_on: MeasuredOn = MeasuredOn.UNKNOWN
    stabilized: Stabilization = Stabilization.UNKNOWN
    pairs: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionObservation(Assessed):
    """Motion left in the picture after the camera's own motion is removed (subject motion)."""

    mean_energy: float | None = None
    peak_energy: float | None = None
    peak_time: FrameTime | None = None
    #: Share of measured pairs with neither camera nor content motion.
    static_fraction: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityMetrics:
    """Raw picture metrics of one shot (medians over its sampled frames). ``None``: not measured."""

    sharpness: float | None = None
    noise_sigma: float | None = None
    luma_p1: float | None = None
    luma_p50: float | None = None
    luma_p99: float | None = None
    tonal_spread: float | None = None
    clipped: float | None = None
    crushed: float | None = None
    #: Rms frame-to-frame luma flutter over every frame of the shot.
    flicker: float | None = None
    sampled_frames: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityAssessment(Assessed):
    """Descriptive reasons (``soft_focus``, ``noisy``, ...) derived from ``metrics``."""

    metrics: QualityMetrics
    measured_on: MeasuredOn = MeasuredOn.UNKNOWN


# --- entities (milestone 2) --------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class TrackPoint:
    time: FrameTime
    box: BBox
    confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Track(Assessed):
    """One entity followed through continuous footage (never across a cut)."""

    track_id: str
    kind: EntityKind
    label: str
    shot_id: str
    first: FrameTime
    last: FrameTime
    points: tuple[TrackPoint, ...]
    #: Analysed frames between ``first`` and ``last`` where the entity was not found (occlusion).
    occluded_frames: int
    #: Anonymous identity cluster (``person_A``), when appearance clustering ran.
    identity_cluster: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityCluster(Assessed):
    """Tracks that look like the same person. Anonymous: nobody is identified, nothing is named."""

    cluster_id: str
    track_ids: tuple[str, ...]
    method: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EntitiesObservation(Assessed):
    """What was found in a shot: its tracks and, among them, the main subject."""

    track_ids: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    main_subject_track_id: str | None = None
    analysed_frames: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class FaceObservation(Assessed):
    """Visible face cues of a shot (cues of what is visible, never inner emotion).

    Fractions are shares of the analysed frames in which a usable face was found.
    """

    face_frames: int = 0
    mean_face_height: float | None = None
    head_yaw: float | None = None
    head_pitch: float | None = None
    eye_contact: float | None = None
    eyes_closed: float | None = None
    smile_cue: float | None = None
    possibly_occluded: float | None = None
    face_sharpness: float | None = None
    #: Probability, from mouth movement alone, that the main face is speaking. Not who speaks.
    visual_speaking: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Gesture:
    kind: GestureKind
    first: FrameTime
    last: FrameTime
    confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class BodyObservation(Assessed):
    pose_fraction: float | None = None
    gestures: tuple[Gesture, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class TextItem:
    text: str
    first: FrameTime
    last: FrameTime
    box: BBox
    confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class TextObservation(Assessed):
    """On-screen text read in a shot; ``screen_kind`` says what kind of screen it looks like."""

    items: tuple[TextItem, ...] = ()
    #: ``code``, ``document`` or ``interface`` when the text suggests a screen, else ``None``.
    screen_kind: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Overlay(Assessed):
    kind: OverlayKind
    first: FrameTime
    last: FrameTime
    box: BBox | None = None
    text: str = ""


# --- meaning and relations (milestone 3) -------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class ContentTypeObservation(Assessed):
    """What kind of footage this looks like (zero-shot), and the score profile it selects."""

    label: str | None = None
    #: The conditioning profile used for scores of this footage.
    content_profile: str = "generic"
    scores: Mapping[str, float] = field(default_factory=dict)
    margin: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AttentionPoint:
    x: float
    y: float
    strength: float


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualMeaningObservation(Assessed):
    """What is visible: environment, objects, where the eye goes and, optionally, a description."""

    indoor_outdoor: str | None = None
    environment: str | None = None
    environment_scores: Mapping[str, float] = field(default_factory=dict)
    objects: tuple[str, ...] = ()
    main_attention: AttentionPoint | None = None
    attention_peaks: tuple[FrameTime, ...] = ()
    description: str | None = None
    embedding_ref: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuityObservation(Assessed):
    """How this shot relates to the one before it. Measurements only; what to do is not decided."""

    previous_shot_id: str | None = None
    embedding_similarity: float | None = None
    framing_similarity: float | None = None
    subject_position_delta: float | None = None
    luma_delta: float | None = None
    color_balance_delta: float | None = None
    same_subject: bool | None = None
    #: Likelihood, from the measurements above, that the cut reads as a jump cut: the same subject
    #: in a nearly identical frame.
    jump_cut_likelihood: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene(Assessed):
    scene_id: str
    shot_ids: tuple[str, ...]
    range: TimeRange
    embedding_ref: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RetakeGroup(Assessed):
    group_id: str
    kind: RelationKind
    shot_ids: tuple[str, ...]
    similarity: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class Embedding:
    """A shot or scene embedding other modules reuse instead of recomputing."""

    ref: str
    scope: str
    model: str
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class IndicatorObservation(Assessed):
    """Signs that footage may be unusable material (covered lens, slate, pocket). Flags only."""

    flags: tuple[str, ...] = ()


# --- cinematography and scores (milestone 4) ---------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class FramingObservation(Assessed):
    framing: FramingType | None = None
    subject_kind: str | None = None
    #: Height of the main subject (face when there is one) as a fraction of the picture height.
    subject_height: float | None = None
    cut_by_frame_edge: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CompositionObservation(Assessed):
    subject_x: float | None = None
    subject_y: float | None = None
    #: Gap above the subject and the free space in the direction it faces, as picture fractions.
    headroom: float | None = None
    lead_room: float | None = None
    #: Distance of the subject centre from the nearest vertical third line.
    thirds_offset: float | None = None
    centered: bool | None = None
    #: Share of the picture outside every detected entity.
    empty_space: float | None = None
    #: Edge density outside the subject (0-1): how busy the background is.
    background_clutter: float | None = None
    #: Sharpness of the subject against its surroundings (how well it separates).
    subject_separation: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LightingObservation(Assessed):
    #: Subject luma over surround luma (below 1: the subject is darker than its surroundings).
    subject_to_surround: float | None = None
    shadow_contrast: float | None = None
    #: Natural log of mean red over mean blue: the colour balance of the shot.
    color_balance: float | None = None
    backlit: bool | None = None
    harsh_shadows: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GeometryObservation(Assessed):
    """Tilt of the horizon and of vertical lines in degrees (0 = level / plumb)."""

    horizon_tilt: float | None = None
    vertical_tilt: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CropSafeRegion(Assessed):
    """The largest window of ``aspect`` that keeps the subject inside, as a picture box.

    Analysis only: nothing is cropped here.
    """

    aspect: str
    window: BBox | None = None
    #: Distance from the subject to the window edge, as a fraction of the window.
    margin: float | None = None
    subject_inside: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Score(Assessed):
    """A rubric-based judgment of a property, derived from stored raw metrics."""

    name: ScoreName
    value: float | None = None
    rubric_version: int = 1
    content_profile: str = "generic"
    #: For ambiguous cases (deliberate handheld, blur, low light): how likely it is intended.
    intentional_likelihood: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Event(Assessed):
    event_id: str
    kind: EventKind
    shot_id: str
    time: FrameTime
    end: FrameTime | None = None
    strength: float | None = None
    detail: str = ""
