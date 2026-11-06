"""The ``timeline_specification``: the executable, application-independent timeline.

Every field is a fact the timeline builder needs, with no creative interpretation left:
exact ``Rational`` seconds AND frames (computed once, ``frames = end_frame - start_frame``),
asset ids (the selected file AND the recording it traces to), tracks with explicit stacking and
compositing, and per item the plan it came from. Nothing names a timeline application.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.timing import ONE, ZERO
from media_house.modules.edit_plan_compiler.domain.values import (
    COLOR_TAGS,
    SCHEMA_VERSION,
    AssetRelation,
    ColorProfile,
    Composite,
    DependencyKind,
    DependencyState,
    Execution,
    InputHow,
    IssueClass,
    ItemOrigin,
    ResultState,
    TrackKind,
    TrackRole,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    edit_plan_id: str
    plan_version: int


# --- creator identity ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FontRef:
    key: str
    family: str
    weight: int
    sha256: str
    available: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneRef:
    name: str
    kind: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandAssetRef:
    slot: str
    required: bool
    asset_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreatorIdentity:
    """REFERENCES to the Layer 4 identity this timeline was compiled for. Nothing is copied
    that ``visual_style_engine`` owns: colours, type scale and motion tokens are reached through
    the style document and verified by its fingerprint."""

    identity_id: str
    identity_version: int
    style_document_id: str | None
    style_fingerprint: str
    format_profile: str
    output_width: int
    output_height: int
    sponsored: bool
    sound_identity: str | None
    sound_identity_version: int | None
    #: Names of the channel's mandatory rules the plans were validated against.
    rules: tuple[str, ...] = ()
    fonts: tuple[FontRef, ...] = ()
    zones: tuple[ZoneRef, ...] = ()
    brand_assets: tuple[BrandAssetRef, ...] = ()
    #: Reusable design choices this timeline used: ``(kind, name)`` such as
    #: ``("transition_preset", "dissolve_soft")`` or ``("graphic_kind", "lower_third")``.
    presets: tuple[tuple[str, str], ...] = ()


# --- time base and tracks ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorTag:
    """Colour as DECLARED by a file's inspection; ``None`` = the file does not say."""

    space: str | None = None
    primaries: str | None = None
    transfer: str | None = None
    range: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorManagement:
    """The timeline's working colour (``CompilerConfig.color_profile``); nothing is converted
    here: a source tagged otherwise is reported and the timeline application converts it."""

    profile: str
    space: str
    primaries: str
    transfer: str
    range: str

    @classmethod
    def of(cls, profile: ColorProfile) -> "ColorManagement":
        space, primaries, transfer, rng = COLOR_TAGS[profile]
        return cls(
            profile=profile.value, space=space, primaries=primaries, transfer=transfer, range=rng
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Timebase:
    frame_rate: Rational
    frame_rate_assumed: bool
    sample_rate: int
    width: int
    height: int
    duration: Rational
    duration_frames: int
    color: ColorManagement = field(default_factory=lambda: ColorManagement.of(ColorProfile.REC709))


@dataclass(frozen=True, slots=True, kw_only=True)
class Track:
    track_id: str
    kind: TrackKind
    role: TrackRole
    #: 1-based position inside its kind, bottom to top (V2 is composited over V1).
    index: int
    lane: int
    composite: Composite
    label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetUse:
    """One file the timeline points at and how it traces back to its recording."""

    asset_id: str
    original_asset_id: str
    relation: AssetRelation
    media_type: str
    duration: Rational | None
    frame_rate: Rational | None
    frame_rate_variable: bool
    sample_rate: int | None
    channels: int | None
    width: int | None
    height: int | None
    checksum: str | None
    available: bool
    used_by: tuple[str, ...] = ()
    color: ColorTag | None = None


# --- items ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RetimeSegment:
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    #: Source seconds per timeline second (0 = a held frame).
    rate: Rational
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Retime:
    segments: tuple[RetimeSegment, ...]
    interpolation: str
    #: ``True``: the derived clip already shows the retimed picture (the builder applies nothing).
    baked: bool
    audio: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CropKey:
    source_time: Rational
    x: float
    y: float
    w: float
    h: float
    interpolation: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Reframe:
    target: str
    recommendation_id: str
    analysis_id: str
    strategy: str
    keys: tuple[CropKey, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoItem:
    item_id: str
    unit_id: str
    clip_id: str
    track_id: str
    origin: ItemOrigin
    #: The file placed on the track (a derived clip when VFX were baked in).
    asset_id: str
    #: The recording it traces to.
    original_asset_id: str
    source_start: Rational
    source_end: Rational
    source_start_frame: int | None
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    #: Source seconds per timeline second of the edit (1 = real time).
    speed: Rational
    layer: int
    section_id: str | None
    beat_id: str | None
    retime: Retime | None = None
    reframe: tuple[Reframe, ...] = ()
    effect_ids: tuple[str, ...] = ()
    transition_in: str | None = None
    transition_out: str | None = None
    #: The rate the source is stored at (``None`` for variable-rate media: seconds rule).
    source_frame_rate: Rational | None = None
    source_variable_rate: bool = False
    fingerprint: str = ""
    #: The range of the RECORDING this clip shows, on the recording's own clock. It differs from
    #: ``source_start`` / ``source_end`` only when a derived file (a baked VFX clip) is placed:
    #: those are positions inside that file.
    original_start: Rational | None = None
    original_end: Rational | None = None

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayItem:
    """A rendered graphic, caption group or transition overlay placed over the picture."""

    item_id: str
    unit_id: str
    origin: ItemOrigin
    #: The plan item it carries out (graphic id, caption segment id, transition id).
    source_ref: str
    kind: str
    track_id: str
    asset_id: str | None
    #: The item's first frame inside the file (a caption batch shares one file).
    asset_start_frame: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    composite: Composite
    execution: Execution
    zone: str = ""
    section_id: str | None = None
    render_fingerprint: str = ""
    detail: str = ""
    fingerprint: str = ""

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectMask:
    mask_id: str
    kind: str
    keyframes: tuple[tuple[Rational, float, float, float, float], ...]
    feather: float
    track_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectSpec:
    effect_id: str
    unit_id: str
    effect_type: str
    clip_id: str
    item_id: str
    preset: str
    mode: str
    parameters: tuple[tuple[str, float], ...]
    intensity: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    execution: Execution
    #: What the plan itself names as the safe stand-in (``original footage``).
    fallback: str
    masks: tuple[EffectMask, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Handles:
    required_out: int
    required_in: int
    available_out: int | None
    available_in: int | None
    sufficient: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionSpec:
    transition_id: str
    unit_id: str
    boundary: str
    kind: str
    family: str
    preset_id: str
    alignment: str
    direction: str
    intensity: int
    cut_at: Rational
    cut_frame: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    duration_frames: int
    outgoing_item_id: str | None
    incoming_item_id: str | None
    handles: Handles
    execution: Execution
    overlay_item_id: str | None = None
    #: The kind the plan wanted when it already fell back, or the compiler did.
    wanted_kind: str | None = None
    #: ``True``: the compiler (not the transition plan) replaced the kind, deterministically.
    adjusted_by_compiler: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioItem:
    item_id: str
    unit_id: str
    origin: ItemOrigin
    role: TrackRole
    track_id: str
    source_ref: str
    asset_id: str
    original_asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    timeline_end: Rational
    sample_rate: int
    start_sample: int
    end_sample: int
    gain_db: float = 0.0
    fade_in: Rational = ZERO
    fade_out: Rational = ZERO
    #: ``source`` keeps the file's own channel layout; the mix engine decides the rest.
    channels: str = "source"
    #: Source seconds per timeline second (1 = real time; pitch kept by the mix engine).
    speed: Rational = ONE
    linked_item_id: str | None = None
    note: str = ""
    fingerprint: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioAutomation:
    """Instructions the ``audio_mix_engine`` carries out. ``kind``: ``duck`` | ``crossfade`` |
    ``music_tail``. Levels are intent, not a mix."""

    automation_id: str
    unit_id: str
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    target_item_ids: tuple[str, ...]
    key: str = ""
    depth_db: float = 0.0
    attack_seconds: float = 0.0
    release_seconds: float = 0.0
    curve: str = ""
    timeline_at: Rational | None = None
    before: Rational | None = None
    after: Rational | None = None
    sample_rate: int | None = None
    source_ref: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubtitleSidecar:
    language: str | None
    srt: str
    vtt: str
    source_plan_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionSpec:
    section_id: str
    order: int
    label: str
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int


# --- dependencies, findings, provenance ---------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyEntry:
    dependency_id: str
    kind: DependencyKind
    reference: str
    state: DependencyState
    required_by: tuple[str, ...] = ()
    detail: str = ""
    #: The module that can produce it, and the pipeline task kind to ask for it.
    owner: str = ""
    request: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    code: str
    klass: IssueClass
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""
    #: For ``AUTO_RESOLVED``: exactly what was changed.
    resolution: str = ""
    #: The compile unit that raised it (so a reused unit brings its findings along).
    unit_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRef:
    kind: str
    how: InputHow
    plan_id: str | None = None
    plan_version: int | None = None
    asset_id: str | None = None
    fingerprint: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeSummary:
    """What differs from the previous compilation of the same timeline (ids of items)."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unchanged: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class Compilation:
    compilation_version: int
    supersedes: str | None
    compiler_version: int
    vocabulary_version: int
    config_hash: str
    dependency_hash: str
    input_hash: str
    #: sha256 of the specification's content: no time stamp, no run id.
    spec_hash: str
    compiled_at: datetime
    units_total: int
    units_reused: int
    #: ``(unit id, digest)``: what the next compilation compares to reuse a unchanged unit.
    unit_digests: tuple[tuple[str, str], ...] = ()
    change: ChangeSummary = field(default_factory=ChangeSummary)


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineSpecification:
    timeline_id: str
    project_id: str | None
    edit: EditRef | None
    anchor_asset_id: str
    creator: CreatorIdentity
    timebase: Timebase
    tracks: tuple[Track, ...]
    assets: tuple[AssetUse, ...]
    sections: tuple[SectionSpec, ...]
    video: tuple[VideoItem, ...]
    overlays: tuple[OverlayItem, ...]
    effects: tuple[EffectSpec, ...]
    transitions: tuple[TransitionSpec, ...]
    audio: tuple[AudioItem, ...]
    automation: tuple[AudioAutomation, ...]
    subtitles: SubtitleSidecar | None
    credits: str
    dependencies: tuple[DependencyEntry, ...]
    #: Every finding that is not CRITICAL (a CRITICAL one blocks the specification).
    issues: tuple[Issue, ...]
    inputs: tuple[InputRef, ...]
    compilation: Compilation
    state: ResultState
    limitations: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def track(self, track_id: str) -> Track | None:
        return next((t for t in self.tracks if t.track_id == track_id), None)

    def conflicts(self) -> tuple[Issue, ...]:
        """Creative conflicts left open: the instructions are kept, a person decides."""
        return tuple(i for i in self.issues if i.klass is IssueClass.CREATIVE_CONFLICT)

    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.klass is IssueClass.WARNING)

    def auto_resolved(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.klass is IssueClass.AUTO_RESOLVED)

    def item_ids(self) -> tuple[str, ...]:
        return (
            *(v.item_id for v in self.video),
            *(o.item_id for o in self.overlays),
            *(a.item_id for a in self.audio),
            *(t.transition_id for t in self.transitions),
            *(e.effect_id for e in self.effects),
            *(x.automation_id for x in self.automation),
        )
