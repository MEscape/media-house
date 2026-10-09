"""The result of analysing one video: typed, grounded, versioned. JSON is only its storage form.

Rules every section obeys (checked at construction, see ``Assessed``):

* Evidence grounding: an ``OK`` observation names the frames that support it.
* Confidence: an ``OK`` observation carries one in [0, 1]. It is the strength of the measurement
  (for example the sharpness of a correlation peak or the agreement of repeated estimates), not
  a calibrated probability.
* Explicit states: anything that could not be stated is ``unknown``, ``not_analyzed``,
  ``not_applicable``, ``not_available`` or ``failed`` WITH a reason. It is never left out.
* Raw metrics (``QualityMetrics``, ``Curve``, the signals) are kept apart from the descriptions
  derived from them.
* Position: every time is a ``FrameTime`` (frame index + pts + rational time base). Pictures
  are in display orientation; positions, when added, are normalised to 0-1 of that picture.

Nothing here recommends an action; names describe properties.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.core.domain import TimeRange
from media_house.core.domain.rational import Rational
from media_house.modules.video_intelligence.domain.observations import (
    BodyObservation,
    BoundaryObservation,
    CameraObservation,
    CompositionObservation,
    ContentTypeObservation,
    ContinuityObservation,
    CropSafeRegion,
    Embedding,
    EntitiesObservation,
    Event,
    FaceObservation,
    FramingObservation,
    GeometryObservation,
    HandlesObservation,
    IdentityCluster,
    IndicatorObservation,
    Keyframes,
    LightingObservation,
    MotionObservation,
    Overlay,
    QualityAssessment,
    RetakeGroup,
    Scene,
    Score,
    TextObservation,
    Track,
    VisualMeaningObservation,
)
from media_house.modules.video_intelligence.domain.source import (
    InputUse,
    ProcessingHistory,
    SourceInfo,
)
from media_house.modules.video_intelligence.domain.values import (
    SCHEMA_VERSION,
    AnalyzerId,
    AnalyzerState,
    CacheOutcome,
    CostTier,
    MeasuredOn,
    ScoreName,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, kw_only=True)
class Shot:
    shot_id: str
    index: int
    range: TimeRange
    boundary_in: BoundaryObservation
    boundary_out: BoundaryObservation
    handles: HandlesObservation
    keyframes: Keyframes
    camera: CameraObservation
    motion: MotionObservation
    quality: QualityAssessment
    #: Entities, faces, body, text (milestone 2).
    entities: EntitiesObservation
    faces: FaceObservation
    body: BodyObservation
    text: TextObservation
    #: Visual meaning and relations (milestone 3).
    scene_id: str | None
    content: ContentTypeObservation
    meaning: VisualMeaningObservation
    continuity: ContinuityObservation
    indicators: IndicatorObservation
    #: Cinematography and scores (milestone 4).
    framing: FramingObservation
    composition: CompositionObservation
    lighting: LightingObservation
    geometry: GeometryObservation
    crop_safe: tuple[CropSafeRegion, ...]
    scores: tuple[Score, ...]

    def score(self, name: ScoreName) -> Score | None:
        return next((s for s in self.scores if s.name is name), None)

    @property
    def frames(self) -> int:
        return self.range.frames

    @property
    def seconds(self) -> float:
        return self.range.seconds


@dataclass(frozen=True, slots=True, kw_only=True)
class Curve:
    """A raw measurement over time, for consumers that want the signal rather than the summary."""

    name: str
    unit: str
    source: AnalyzerId
    measured_on: MeasuredOn
    timebase: Rational
    frames: tuple[int, ...]
    pts: tuple[int, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not len(self.frames) == len(self.pts) == len(self.values):
            raise InvariantViolation(f"Curve {self.name}: columns differ in length")


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalyzerReport:
    """What happened to one analyzer in this run."""

    analyzer: AnalyzerId
    version: int
    cost: CostTier
    state: AnalyzerState
    cache: CacheOutcome
    depends_on: tuple[AnalyzerId, ...]
    #: Library asset holding the analyzer's stored signals, when it has one.
    asset_id: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state is not AnalyzerState.OK and not self.reason:
            raise InvariantViolation(
                f"Analyzer {self.analyzer.value} is {self.state.value} without reason"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Exactly how this result was produced."""

    schema_version: int
    processing_version: int
    profile_name: str
    profile_version: int
    analyzer_versions: Mapping[str, int]
    #: Version of every derivation that turns signals into descriptions.
    derivation_versions: Mapping[str, int]
    #: Tools that decide the measured values (name -> version).
    engines: Mapping[str, str]
    device_requested: str
    device_used: str
    #: Version of the score rubrics and of the zero-shot vocabulary the result was made with.
    rubric_version: int
    vocabulary_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoAnalysis:
    """Everything known about one video asset version."""

    asset_id: str
    #: Content hash of the analysed asset version (the version this result is addressed by).
    asset_checksum: str
    source: SourceInfo
    history: ProcessingHistory
    provenance: Provenance
    inputs_used: tuple[InputUse, ...]
    analyzers: tuple[AnalyzerReport, ...]
    shots: tuple[Shot, ...]
    curves: tuple[Curve, ...]
    warnings: tuple[str, ...]
    #: Scenes, tracks, anonymous identities, repeated takes, overlays and the unified event
    #: timeline. Empty when the analyzers behind them did not run (see ``analyzers``).
    scenes: tuple[Scene, ...] = ()
    tracks: tuple[Track, ...] = ()
    identities: tuple[IdentityCluster, ...] = ()
    retakes: tuple[RetakeGroup, ...] = ()
    overlays: tuple[Overlay, ...] = ()
    events: tuple[Event, ...] = ()
    #: Shot and scene embeddings other modules reuse instead of recomputing.
    embeddings: tuple[Embedding, ...] = ()
    schema_version: int = SCHEMA_VERSION

    @property
    def measured_on(self) -> MeasuredOn:
        return self.history.measured_on

    def shot_at(self, frame: int) -> Shot | None:
        """The shot containing ``frame``, if any."""
        return next((s for s in self.shots if s.range.contains_frame(frame)), None)

    def shot(self, shot_id: str) -> Shot | None:
        return next((s for s in self.shots if s.shot_id == shot_id), None)

    def scene(self, scene_id: str) -> Scene | None:
        return next((s for s in self.scenes if s.scene_id == scene_id), None)

    def track(self, track_id: str) -> Track | None:
        return next((s for s in self.tracks if s.track_id == track_id), None)

    def embedding(self, ref: str) -> Embedding | None:
        return next((e for e in self.embeddings if e.ref == ref), None)

    def curve(self, name: str) -> Curve | None:
        return next((c for c in self.curves if c.name == name), None)

    def analyzer(self, analyzer: AnalyzerId) -> AnalyzerReport | None:
        return next((a for a in self.analyzers if a.analyzer is analyzer), None)
