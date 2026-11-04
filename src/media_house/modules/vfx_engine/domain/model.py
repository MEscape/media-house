"""The stored ``vfx_plan``: one treatment per clip, each an ordered effect stack with its reasons,
masks, time map, render output and render QC (frozen dataclasses, strict codec)."""

from dataclasses import dataclass
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.vfx_engine.domain.facts import Box, EditRef
from media_house.modules.vfx_engine.domain.values import (
    SCHEMA_VERSION,
    AudioIntent,
    Backend,
    Decision,
    EffectStatus,
    EffectType,
    InputHow,
    Interpolation,
    MaskKind,
    QcCategory,
    ResultState,
    SegmentKind,
    Severity,
    StageState,
    TreatmentStatus,
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
class Param:
    name: str
    value: float


@dataclass(frozen=True, slots=True, kw_only=True)
class MaskKeyframe:
    source_time: Rational
    box: Box


@dataclass(frozen=True, slots=True, kw_only=True)
class Mask:
    """Where an effect applies. Keyframes are SOURCE times (linear in between, held at the ends);
    one keyframe = a static shape. ``feather`` is a share of the picture's short side."""

    mask_id: str
    kind: MaskKind
    keyframes: tuple[MaskKeyframe, ...]
    feather: float = 0.0
    #: The tracks this mask follows (``<analysis id>/<track id>``).
    track_refs: tuple[str, ...] = ()
    confidence: float | None = None
    #: Source spans where the track was lost and the fallback (full frame) covers the picture.
    fallback_spans: tuple[tuple[Rational, Rational], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSegment:
    """One piece of a time map: ``timeline`` span <-> ``source`` span at ``rate`` (source
    seconds per timeline second; 0 = freeze, the source span is the one held frame)."""

    kind: SegmentKind
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    rate: Rational
    start_frame: int
    end_frame: int
    audio: AudioIntent


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeMap:
    segments: tuple[TimeSegment, ...]
    interpolation: Interpolation
    #: The clip keeps its timeline span; this is the source it now shows (the rest is unused).
    source_used_start: Rational
    source_used_end: Rational
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Effect:
    effect_id: str
    #: ``clip_id:effect_type`` (what a person's decision follows through replans).
    effect_key: str
    effect_type: EffectType
    status: EffectStatus
    preset: str
    mode: str = ""
    parameters: tuple[Param, ...] = ()
    intensity: int = 0
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    masks: tuple[Mask, ...] = ()
    request_id: str | None = None
    backend: Backend = Backend.FFMPEG
    #: Style tokens that bounded this effect (``motion.intensity_ceiling=2``, ``palette.x``).
    style_bounds: tuple[str, ...] = ()
    confidence: float = 1.0
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    #: What is used when the effect is not applied or fails.
    fallback: str = "original footage"
    decision: Decision | None = None
    decision_note: str = ""

    def param(self, name: str, default: float = 0.0) -> float:
        return next((p.value for p in self.parameters if p.name == name), default)


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderQc:
    """Facts measured on the produced file (and the source) - never the intent."""

    width: int
    height: int
    pix_fmt: str
    bit_depth: int
    frame_rate: Rational
    frames_expected: int
    frames_actual: int
    #: Formats FFmpeg negotiated inside the graph that drop below 10 bits (should be empty).
    low_precision_steps: tuple[str, ...] = ()
    new_spikes: int = 0
    clipped_source: float | None = None
    clipped_output: float | None = None
    #: Smallest share of detail removed inside a concealed region (privacy), if measured.
    concealment: float | None = None
    #: Share of track samples inside the mask (geometric coverage), if a mask follows tracks.
    coverage: float | None = None
    source_unchanged: bool = True
    render_seconds: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputRef:
    asset_id: str
    created: bool
    processing_fingerprint: str
    width: int
    height: int
    frames: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Treatment:
    """Everything done to one clip; rendered as ONE derived clip (or the original is used)."""

    treatment_id: str
    clip_id: str
    asset_id: str
    layer: int
    section_id: str | None
    beat_id: str | None
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    status: TreatmentStatus
    effects: tuple[Effect, ...]
    time_map: TimeMap | None = None
    audio: AudioIntent = AudioIntent.SOURCE_SYNC
    #: Clip ids of other layers composited into this one.
    overlay_clip_ids: tuple[str, ...] = ()
    #: Digest of everything that changes the rendered pixels (the render cache key).
    render_fingerprint: str = ""
    output: OutputRef | None = None
    qc: RenderQc | None = None
    checks: tuple[QcIssue, ...] = ()

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    def active(self) -> tuple[Effect, ...]:
        """The render stack: effects that are applied, in order."""
        return tuple(
            e
            for e in self.effects
            if e.status in {EffectStatus.PLANNED, EffectStatus.RENDERED, EffectStatus.REUSED}
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleBinding:
    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str
    intensity_ceiling: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Capability:
    effect: EffectType
    backend: Backend
    available: bool
    detail: str = ""


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
    render_version: int
    configuration_fingerprint: str
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxPlan:
    plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    edit: EditRef | None
    style: StyleBinding | None
    created_at: datetime
    state: ResultState
    anchor_asset_id: str
    frame_rate: Rational
    output_width: int
    output_height: int
    rendered: bool
    treatments: tuple[Treatment, ...]
    capabilities: tuple[Capability, ...]
    checks: tuple[QcIssue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def treatment(self, clip_id: str) -> Treatment | None:
        return next((t for t in self.treatments if t.clip_id == clip_id), None)

    def effects(self) -> tuple[Effect, ...]:
        return tuple(e for t in self.treatments for e in t.effects)
