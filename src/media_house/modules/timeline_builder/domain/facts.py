"""The compiled specification, as the builder's OWN narrow types (the domain imports no module).

``application/views.py`` fills these from the compiler's ``TimelineSpecification``. Only what the
builder reads is here; vocabulary words stay plain strings and are checked by the builder against
its own enums, so a word it does not know is reported instead of being guessed.

Times are exact ``Rational`` seconds, frames and samples as the compiler computed them once.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.core.domain import Rational
from media_house.modules.timeline_builder.domain.timing import ONE, ZERO


@dataclass(frozen=True, slots=True, kw_only=True)
class EditFact:
    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorFact:
    space: str | None = None
    primaries: str | None = None
    transfer: str | None = None
    range: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorManagementFact:
    profile: str
    space: str
    primaries: str
    transfer: str
    range: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TimebaseFact:
    frame_rate: Rational
    frame_rate_assumed: bool
    sample_rate: int
    width: int
    height: int
    duration: Rational
    duration_frames: int
    color: ColorManagementFact


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackFact:
    track_id: str
    kind: str
    role: str
    index: int
    lane: int
    composite: str
    label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetFact:
    asset_id: str
    original_asset_id: str
    relation: str
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
    color: ColorFact | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FontFact:
    key: str
    family: str
    weight: int
    sha256: str
    available: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneFact:
    name: str
    kind: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandAssetFact:
    slot: str
    required: bool
    asset_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityFact:
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
    rules: tuple[str, ...] = ()
    fonts: tuple[FontFact, ...] = ()
    zones: tuple[ZoneFact, ...] = ()
    brand_assets: tuple[BrandAssetFact, ...] = ()
    presets: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class RetimeSegmentFact:
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    rate: Rational
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RetimeFact:
    segments: tuple[RetimeSegmentFact, ...]
    interpolation: str
    baked: bool
    audio: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CropKeyFact:
    source_time: Rational
    x: float
    y: float
    w: float
    h: float
    interpolation: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReframeFact:
    target: str
    recommendation_id: str
    analysis_id: str
    strategy: str
    keys: tuple[CropKeyFact, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoFact:
    item_id: str
    clip_id: str
    track_id: str
    origin: str
    asset_id: str
    original_asset_id: str
    source_start: Rational
    source_end: Rational
    source_start_frame: int | None
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    speed: Rational
    layer: int
    section_id: str | None
    beat_id: str | None
    retime: RetimeFact | None = None
    reframe: tuple[ReframeFact, ...] = ()
    effect_ids: tuple[str, ...] = ()
    transition_in: str | None = None
    transition_out: str | None = None
    source_frame_rate: Rational | None = None
    source_variable_rate: bool = False
    original_start: Rational | None = None
    original_end: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayFact:
    item_id: str
    origin: str
    source_ref: str
    kind: str
    track_id: str
    asset_id: str | None
    asset_start_frame: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    composite: str
    execution: str
    zone: str = ""
    section_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class MaskFact:
    mask_id: str
    kind: str
    keyframes: tuple[tuple[Rational, float, float, float, float], ...]
    feather: float
    track_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectFact:
    effect_id: str
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
    execution: str
    fallback: str
    masks: tuple[MaskFact, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class HandlesFact:
    required_out: int
    required_in: int
    available_out: int | None
    available_in: int | None
    sufficient: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionFact:
    transition_id: str
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
    handles: HandlesFact
    execution: str
    overlay_item_id: str | None = None
    wanted_kind: str | None = None
    adjusted_by_compiler: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioFact:
    item_id: str
    origin: str
    role: str
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
    channels: str = "source"
    speed: Rational = ONE
    linked_item_id: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AutomationFact:
    automation_id: str
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
class SubtitleFact:
    language: str | None
    srt: str
    vtt: str
    source_plan_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionFact:
    section_id: str
    order: int
    label: str
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyFact:
    dependency_id: str
    kind: str
    reference: str
    state: str
    required_by: tuple[str, ...] = ()
    detail: str = ""
    owner: str = ""
    request: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class IssueFact:
    code: str
    klass: str
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""
    resolution: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class CompilationFact:
    compilation_version: int
    compiler_version: int
    vocabulary_version: int
    config_hash: str
    spec_hash: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SpecFacts:
    """Everything of one specification the builder reads."""

    timeline_id: str
    project_id: str | None
    edit: EditFact | None
    anchor_asset_id: str
    schema_version: int
    state: str
    creator: IdentityFact
    timebase: TimebaseFact
    tracks: tuple[TrackFact, ...]
    assets: tuple[AssetFact, ...]
    sections: tuple[SectionFact, ...]
    video: tuple[VideoFact, ...]
    overlays: tuple[OverlayFact, ...]
    effects: tuple[EffectFact, ...]
    transitions: tuple[TransitionFact, ...]
    audio: tuple[AudioFact, ...]
    automation: tuple[AutomationFact, ...]
    subtitles: SubtitleFact | None
    credits: str
    dependencies: tuple[DependencyFact, ...]
    issues: tuple[IssueFact, ...]
    compilation: CompilationFact
    limitations: tuple[str, ...] = ()

    def asset(self, asset_id: str | None) -> AssetFact | None:
        return next((a for a in self.assets if a.asset_id == asset_id), None)


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveSource:
    """What the Media Library says NOW about a file the specification points at (the
    specification may be older than the library)."""

    asset_id: str
    exists: bool
    available: bool
    checksum: str | None = None
    name: str = ""
    #: The proxy a person edits with (``proxy_manager``); the source stays the identity.
    proxy_asset_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveSources:
    found: Mapping[str, LiveSource] = field(default_factory=dict)

    def get(self, asset_id: str | None) -> LiveSource | None:
        return None if asset_id is None else self.found.get(asset_id)
