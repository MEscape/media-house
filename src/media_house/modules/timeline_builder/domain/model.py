"""The built timeline: the constructed, application-independent timeline of one edit.

It is NOT a copy of the specification. The specification lists items per kind; the built timeline
is what a person opens: tracks that hold their clips in order, one table of source media
(original, derivative, proxy) that every clip instance points at, each picture clip's processing
chain in a fixed order, transitions that join two named clips, mix instructions attached to the
audio clips they act on, markers, sections and groups, and a stable identity for every element.

Every element carries its provenance (who made it) and the digest of the specification facts it was
built from, so an update can tell what changed and a person's work is never overwritten silently.
Nothing here names a timeline application.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.timeline_builder.domain.timing import ONE, ZERO
from media_house.modules.timeline_builder.domain.values import (
    SCHEMA_VERSION,
    BlendMode,
    BuildMode,
    BuildStatus,
    ChainStage,
    CheckKind,
    CheckState,
    ClipKind,
    ClipState,
    DependencyKind,
    DependencyState,
    EditKind,
    Execution,
    FitMode,
    Provenance,
    Role,
    Severity,
    TrackKind,
    TransitionMode,
)

# --- settings and identity -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class EditReference:
    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorSettings:
    profile: str
    space: str
    primaries: str
    transfer: str
    range: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineSettings:
    width: int
    height: int
    #: The reduced display shape, ``(16, 9)``.
    aspect: tuple[int, int]
    frame_rate: Rational
    frame_rate_assumed: bool
    sample_rate: int
    audio_channels: str
    duration: Rational
    duration_frames: int
    color: ColorSettings
    fit_mode: FitMode


@dataclass(frozen=True, slots=True, kw_only=True)
class FontReference:
    key: str
    family: str
    weight: int
    sha256: str
    available: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneReference:
    name: str
    kind: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandAssetReference:
    slot: str
    required: bool
    asset_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityReference:
    """REFERENCES to the Layer 4 creator identity this timeline was compiled for. Nothing that
    ``visual_style_engine`` owns is copied: colours, type scale and motion tokens are reached
    through the style document and checked by its fingerprint."""

    identity_id: str
    identity_version: int
    style_document_id: str | None
    style_fingerprint: str
    format_profile: str
    sponsored: bool
    sound_identity: str | None
    sound_identity_version: int | None
    rules: tuple[str, ...] = ()
    fonts: tuple[FontReference, ...] = ()
    zones: tuple[ZoneReference, ...] = ()
    brand_assets: tuple[BrandAssetReference, ...] = ()
    #: Reusable design choices, ``(kind, name)``: ``("transition_preset", "dissolve_soft")``.
    presets: tuple[tuple[str, str], ...] = ()


# --- source media --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceMedia:
    """One file the timeline points at, once. Every clip instance of it refers to this entry.

    ``original_asset_id`` is the recording it traces to (itself for an original); ``relation``
    says how this file relates to it; ``proxy_asset_id`` is the proxy a person may edit with.
    """

    source_id: str
    original_asset_id: str
    relation: str
    media_type: str
    name: str
    duration: Rational | None
    frame_rate: Rational | None
    frame_rate_variable: bool
    sample_rate: int | None
    channels: int | None
    width: int | None
    height: int | None
    checksum: str | None
    color_space: str | None
    color_primaries: str | None
    color_transfer: str | None
    color_range: str | None
    available: bool
    proxy_asset_id: str | None = None
    used_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceUse:
    """The part of a source file one clip instance plays: in/out inside THAT file."""

    source_id: str
    original_asset_id: str
    source_in: Rational
    source_out: Rational
    source_in_frame: int | None
    #: The clip's first frame inside the file (a caption batch shares one rendered file).
    asset_start_frame: int = 0
    #: The range of the RECORDING on its own clock; differs from ``source_in`` / ``source_out``
    #: only when a derived file (a baked VFX clip) is placed.
    original_in: Rational | None = None
    original_out: Rational | None = None


# --- picture clips -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Transform:
    """Position, scale and rotation on top of the fit. Identity unless a person changes it."""

    fit: FitMode
    position_x: float = 0.0
    position_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSegment:
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    #: Source seconds per timeline second; 0 is a held frame.
    rate: Rational
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeMap:
    """Retiming of a clip: speed changes, ramps, freeze frames and reverse playback."""

    segments: tuple[TimeSegment, ...]
    interpolation: str
    #: ``True``: a timeline application performs it. ``False``: the file already shows the
    #: retimed picture (baked), so nothing is applied again.
    applied: bool
    audio: str

    @property
    def has_reverse(self) -> bool:
        return any(s.kind == "reverse" for s in self.segments)


@dataclass(frozen=True, slots=True, kw_only=True)
class CropKeyframe:
    source_time: Rational
    #: Where it falls on the timeline (``None``: the clip's time map does not reach it).
    timeline_time: Rational | None
    timeline_frame: int | None
    #: Normalised top-left window of the display-oriented source picture.
    x: float
    y: float
    w: float
    h: float
    interpolation: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BlurKeyframe:
    """A key of the picture blur (``size`` 0 = sharp); the curve is the one INTO the key."""

    source_time: Rational
    timeline_time: Rational | None
    timeline_frame: int | None
    size: float
    interpolation: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CropPlan:
    crop_id: str
    target: str
    recommendation_id: str
    analysis_id: str
    strategy: str
    keys: tuple[CropKeyframe, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MaskPlan:
    mask_id: str
    kind: str
    keyframes: tuple[tuple[Rational, float, float, float, float], ...]
    feather: float
    track_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectStep:
    effect_id: str
    effect_type: str
    preset: str
    mode: str
    parameters: tuple[tuple[str, float], ...]
    intensity: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    execution: Execution
    #: ``True``: a timeline application performs it. Baked: the file shows it already.
    #: Fallback / unavailable: the plan's own stand-in is used and nothing is applied.
    applied: bool
    fallback: str = ""
    masks: tuple[MaskPlan, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainStep:
    """One step of a picture clip's processing, in the order it must run. Nothing reorders it."""

    order: int
    stage: ChainStage
    ref: str = ""
    applied: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanMark:
    """A person's change to an element and the AI version it was made on."""

    base_digest: str
    fields: tuple[str, ...] = ()
    editor: str = ""
    note: str = ""
    #: ``review`` policy: the specification's newer version of the element, held back.
    pending_digest: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoClip:
    clip_id: str
    track_id: str
    kind: ClipKind
    name: str
    source: SourceUse | None
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    #: Source seconds per timeline second of the edit (1 = real time).
    speed: Rational = ONE
    time_map: TimeMap | None = None
    transform: Transform | None = None
    crops: tuple[CropPlan, ...] = ()
    blur: tuple[BlurKeyframe, ...] = ()
    effects: tuple[EffectStep, ...] = ()
    chain: tuple[ChainStep, ...] = ()
    opacity: float = 1.0
    blend: BlendMode = BlendMode.NORMAL
    transition_in: str | None = None
    transition_out: str | None = None
    #: The audio clip that belongs to this picture (kept in sync).
    audio_link: str | None = None
    section_id: str | None = None
    beat_id: str | None = None
    zone: str = ""
    #: ``(kind, name)`` design choices this clip carries out, for the identity check.
    identity: tuple[tuple[str, str], ...] = ()
    state: ClipState = ClipState.RESOLVED
    enabled: bool = True
    provenance: Provenance = Provenance.AI
    human: HumanMark | None = None
    #: The specification item this clip was built from (its trace) and that item's digest.
    origin_ref: str = ""
    source_digest: str = ""
    detail: str = ""

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioClip:
    clip_id: str
    track_id: str
    role: Role
    name: str
    source: SourceUse | None
    timeline_start: Rational
    timeline_end: Rational
    sample_rate: int
    start_sample: int
    end_sample: int
    gain_db: float = 0.0
    fade_in: Rational = ZERO
    fade_out: Rational = ZERO
    channels: str = "source"
    speed: Rational = ONE
    #: The picture clip this sound belongs to (kept in sync).
    video_link: str | None = None
    #: The mix instructions that act on this clip (the ``audio_mix_engine`` carries them out).
    mix_ids: tuple[str, ...] = ()
    section_id: str | None = None
    state: ClipState = ClipState.RESOLVED
    enabled: bool = True
    provenance: Provenance = Provenance.AI
    human: HumanMark | None = None
    origin_ref: str = ""
    source_digest: str = ""
    note: str = ""

    @property
    def samples(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True, slots=True, kw_only=True)
class MixInstruction:
    """An instruction for the final mix: ducking, a crossfade or a music tail. Intent, not a mix."""

    mix_id: str
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    clip_ids: tuple[str, ...]
    key: str = ""
    depth_db: float = 0.0
    attack_seconds: float = 0.0
    release_seconds: float = 0.0
    curve: str = ""
    timeline_at: Rational | None = None
    before: Rational | None = None
    after: Rational | None = None
    sample_rate: int | None = None
    origin_ref: str = ""
    source_digest: str = ""


# --- tracks --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltTrack:
    track_id: str
    kind: TrackKind
    role: Role
    #: Stacking position inside its kind, bottom to top (V2 is composited over V1).
    index: int
    lane: int
    name: str
    #: The group a person sees this track under (``Graphics``): all lanes of a role share one.
    group: str
    blend: BlendMode
    enabled: bool = True
    locked: bool = False
    video: tuple[VideoClip, ...] = ()
    audio: tuple[AudioClip, ...] = ()
    provenance: Provenance = Provenance.AI

    @property
    def clip_count(self) -> int:
        return len(self.video) + len(self.audio)


# --- transitions ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class HandleCheck:
    required_out: int
    required_in: int
    available_out: int | None
    available_in: int | None
    sufficient: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltTransition:
    """A transition joining two named clips. The clips keep their timeline positions: a dissolve
    reads ``tail_frames`` past the outgoing clip's out point and ``head_frames`` before the
    incoming clip's in point (the handles), so the edit's duration and rhythm do not change."""

    transition_id: str
    track_id: str
    kind: str
    mode: TransitionMode
    family: str
    preset_id: str
    alignment: str
    direction: str
    intensity: int
    cut_frame: int
    start_frame: int
    end_frame: int
    timeline_start: Rational
    timeline_end: Rational
    duration_frames: int
    from_clip: str | None
    to_clip: str | None
    #: Frames read beyond the outgoing out point / before the incoming in point.
    tail_frames: int
    head_frames: int
    handles: HandleCheck
    execution: Execution
    overlay_clip: str | None = None
    #: The crossfade or fade the sound of this cut carries (a mix instruction).
    mix_id: str | None = None
    wanted_kind: str | None = None
    adjusted_by_compiler: bool = False
    supported: bool = True
    reasons: tuple[str, ...] = ()
    provenance: Provenance = Provenance.AI
    origin_ref: str = ""
    source_digest: str = ""


# --- organisation --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltSection:
    section_id: str
    order: int
    name: str
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    clip_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Marker:
    marker_id: str
    kind: str  # section | review
    frame: int
    duration_frames: int
    name: str
    note: str = ""
    ref: str = ""
    severity: Severity | None = None
    provenance: Provenance = Provenance.SYSTEM


@dataclass(frozen=True, slots=True, kw_only=True)
class Group:
    """Elements a person moves or inspects together: a picture and its sound, a transition
    with its overlay and crossfade, a section."""

    group_id: str
    kind: str  # av_link | transition | section
    name: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubtitleCue:
    index: int
    start_frame: int
    end_frame: int
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SubtitleTrack:
    """The caption text as editable cues (the rendered caption clips carry the look)."""

    language: str | None
    cues: tuple[SubtitleCue, ...]
    srt: str
    vtt: str
    source_plan_id: str


# --- dependencies, findings, validation ----------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyRecord:
    dependency_id: str
    kind: DependencyKind
    reference: str
    state: DependencyState
    #: Clip / transition ids that cannot be carried out without it.
    required_by: tuple[str, ...] = ()
    detail: str = ""
    owner: str = ""
    request: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    code: str
    severity: Severity
    message: str
    element_id: str | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    evidence: str = ""
    #: ``specification`` (carried over from the compiler), ``builder``, ``external`` (a check).
    origin: str = "builder"
    resolution: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalCheck:
    """The result of a check that needs a rendered preview or a media analysis. The builder never
    makes these up: until one is supplied the kind stays ``not_run``."""

    kind: CheckKind
    state: CheckState
    tool: str = ""
    detail: str = ""
    start_frame: int | None = None
    end_frame: int | None = None
    element_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Validation:
    #: The structural checks that ran on the constructed timeline.
    checks_run: tuple[str, ...]
    passed: bool
    external: tuple[ExternalCheck, ...] = ()
    #: Check kinds nobody has run yet (they need a render or an analysis).
    not_run: tuple[CheckKind, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Counts:
    video_tracks: int
    audio_tracks: int
    video_clips: int
    audio_clips: int
    transitions: int
    effects: int
    markers: int
    sections: int
    sources: int


# --- people's work -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Tombstone:
    """An element a person deleted, and the AI version they deleted."""

    element_id: str
    base_digest: str
    editor: str = ""
    note: str = ""
    pending_digest: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRecord:
    sequence: int
    kind: EditKind
    element_id: str
    fields: tuple[str, ...]
    editor: str
    note: str
    at: datetime


# --- provenance of the build ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeSet:
    """What differs from the previous build, by element id."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    #: Elements whose specification changed under a person's edit (see ``conflict_policy``).
    conflicted: tuple[str, ...] = ()
    unchanged: int = 0
    #: Sections that contain a change.
    sections: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.conflicted)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildInfo:
    build_version: int
    supersedes: str | None
    builder_version: int
    vocabulary_version: int
    mode: BuildMode
    #: Why a full rebuild was needed (empty otherwise).
    reason: str
    spec_asset_id: str | None
    spec_hash: str
    spec_compilation_version: int
    config_hash: str
    #: sha256 of every input of the build except a person's work (the specification, the files,
    #: the configuration): if it is unchanged, the stored build already is the answer.
    input_hash: str
    #: sha256 of the timeline's content: no time stamp, no version, no run id.
    build_hash: str
    built_at: datetime
    elements_total: int
    elements_reused: int
    change: ChangeSet = field(default_factory=ChangeSet)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltTimeline:
    timeline_id: str
    project_id: str | None
    edit: EditReference | None
    anchor_asset_id: str
    settings: TimelineSettings
    identity: IdentityReference
    sources: tuple[SourceMedia, ...]
    tracks: tuple[BuiltTrack, ...]
    transitions: tuple[BuiltTransition, ...]
    mix: tuple[MixInstruction, ...]
    sections: tuple[BuiltSection, ...]
    markers: tuple[Marker, ...]
    groups: tuple[Group, ...]
    subtitles: SubtitleTrack | None
    credits: str
    dependencies: tuple[DependencyRecord, ...]
    issues: tuple[Issue, ...]
    validation: Validation
    counts: Counts
    build: BuildInfo
    status: BuildStatus
    removed_by_human: tuple[Tombstone, ...] = ()
    edit_log: tuple[EditRecord, ...] = ()
    limitations: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    # --- lookups ---
    def track(self, track_id: str) -> BuiltTrack | None:
        return next((t for t in self.tracks if t.track_id == track_id), None)

    def video_clips(self) -> tuple[VideoClip, ...]:
        return tuple(c for t in self.tracks for c in t.video)

    def audio_clips(self) -> tuple[AudioClip, ...]:
        return tuple(c for t in self.tracks for c in t.audio)

    def video_clip(self, clip_id: str) -> VideoClip | None:
        return next((c for c in self.video_clips() if c.clip_id == clip_id), None)

    def audio_clip(self, clip_id: str) -> AudioClip | None:
        return next((c for c in self.audio_clips() if c.clip_id == clip_id), None)

    def source(self, source_id: str) -> SourceMedia | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def transition(self, transition_id: str) -> BuiltTransition | None:
        return next((t for t in self.transitions if t.transition_id == transition_id), None)

    def element_ids(self) -> tuple[str, ...]:
        return (
            *(t.track_id for t in self.tracks),
            *(c.clip_id for c in self.video_clips()),
            *(c.clip_id for c in self.audio_clips()),
            *(t.transition_id for t in self.transitions),
            *(m.mix_id for m in self.mix),
            *(m.marker_id for m in self.markers),
            *(g.group_id for g in self.groups),
        )

    # --- findings ---
    def by_severity(self, severity: Severity) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is severity)

    def unresolved(self) -> tuple[DependencyRecord, ...]:
        return tuple(d for d in self.dependencies if d.state is not DependencyState.RESOLVED)
