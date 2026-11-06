"""Everything the compiler reads, as its OWN types (the domain imports no other module).

Three groups, all filled by the application / the pipeline:

* ``TimelineSource``: the edit, narrow and neutral (the pipeline maps the edit plan into it;
  no edit on the no-speech path means one clip of the whole recording).
* ``*Facts``: the stored Layer 4 plans, reduced to what a timeline needs (``application/views``).
* ``AssetFacts`` / ``FontFact`` / ``ReframeFacts``: what the Media Library, the inspection, the
  derivative lookup and the reframing analysis say about the files and crops the plans name.

Time: ``Rational`` seconds in the three domains the system keeps apart (``source_*`` seconds of
the recording, ``timeline_*`` seconds from the start of the edit, ``local_*`` inside one item).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.errors import InvalidCompileSource
from media_house.modules.edit_plan_compiler.domain.model import ColorTag
from media_house.modules.edit_plan_compiler.domain.timing import ZERO, fr


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    edit_plan_id: str
    plan_version: int


# --- the edit ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FramingRef:
    """A crop recommendation covering part of a clip's source range (fetched by id)."""

    recommendation_id: str
    analysis_id: str
    target: str
    strategy: str
    executable: bool
    source_start: Rational
    source_end: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceClip:
    """One placement of the edit at speed ``source length / timeline length``."""

    clip_id: str
    asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    timeline_duration: Rational
    #: ``True``: a picture-track placement. ``False``: an audio-track placement.
    picture: bool = True
    #: 0 = main layer, 1+ = cover.
    layer: int = 0
    #: ``source_sync`` | ``voiceover`` | ``muted`` (what the edit does with the clip's own sound).
    audio: str = "source_sync"
    section_id: str | None = None
    beat_id: str | None = None
    start_frame: int | None = None
    duration_frames: int | None = None
    fade_in: Rational = ZERO
    fade_out: Rational = ZERO
    linked: str | None = None
    reuse_of: str | None = None
    framing: tuple[FramingRef, ...] = ()

    @property
    def timeline_end(self) -> Rational:
        total = fr(self.timeline_start) + fr(self.timeline_duration)
        return Rational(total.numerator, total.denominator)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceSection:
    section_id: str
    order: int
    label: str
    timeline_start: Rational
    timeline_end: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBridge:
    """A J/L cut the edit declared: sound and picture change at different moments."""

    bridge_id: str
    kind: str
    audio_at: Rational
    picture_at: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineSource:
    edit: EditRef | None
    project_id: str | None
    anchor_asset_id: str
    frame_rate: Rational
    frame_rate_assumed: bool
    duration: Rational
    story_format: str | None
    clips: tuple[SourceClip, ...]
    sections: tuple[SourceSection, ...] = ()
    bridges: tuple[SourceBridge, ...] = ()

    def picture(self) -> tuple[SourceClip, ...]:
        return tuple(c for c in self.clips if c.picture)

    def sound(self) -> tuple[SourceClip, ...]:
        return tuple(c for c in self.clips if not c.picture)

    def check(self) -> None:
        """Malformed input is the caller's mapping error, not an editorial finding."""
        seen: set[str] = set()
        for clip in self.clips:
            if clip.clip_id in seen:
                raise InvalidCompileSource(f"clip id {clip.clip_id!r} appears twice")
            seen.add(clip.clip_id)
            if fr(clip.source_end) <= fr(clip.source_start):
                raise InvalidCompileSource(f"clip {clip.clip_id!r} has an empty source range")
            if fr(clip.timeline_duration) <= 0:
                raise InvalidCompileSource(f"clip {clip.clip_id!r} has no timeline duration")
        if fr(self.frame_rate) <= 0:
            raise InvalidCompileSource("the frame rate must be positive")


# --- the channel style ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FontFact:
    key: str
    family: str
    weight: int
    sha256: str
    available: bool
    detail: str = ""


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
    purpose: str
    required: bool
    asset_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DriftFact:
    """One finding of ``visual_style_engine``'s drift validator (``severity``: error | warning |
    info). The compiler reports it; it never judges a token itself."""

    severity: str
    code: str
    message: str
    item_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleFacts:
    """The resolved channel identity as the compiler binds it (never copied: referenced)."""

    document_id: str | None
    fingerprint: str
    identity_id: str
    identity_version: int
    profile: str
    width: int
    height: int
    sponsored: bool
    zones: tuple[ZoneFact, ...] = ()
    fonts: tuple[FontFact, ...] = ()
    brand_assets: tuple[BrandAssetFact, ...] = ()
    #: Names of the mandatory channel rules (``token_rules`` / ``brand_rules``).
    rules: tuple[str, ...] = ()
    #: Findings of the style's own drift validator on what the plans placed.
    drift: tuple[DriftFact, ...] = ()
    intensity_ceiling: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanStamp:
    """Which stored plan a block of facts came from, and the style that plan was made with."""

    plan_id: str
    plan_version: int
    document_id: str | None
    style_fingerprint: str | None
    state: str
    #: The edit the plan was made for (``None``: no-speech path or an unstated edit).
    edit: EditRef | None = None


# --- Layer 4 plans -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicFact:
    graphic_id: str
    kind: str
    status: str  # ok | fallback | not_available | rejected
    timeline_start: Rational | None
    duration: Rational
    start_frame: int | None
    end_frame: int | None
    asset_id: str | None
    asset_start_frame: int
    has_alpha: bool
    width: int | None
    height: int | None
    zone: str
    section_id: str | None
    origin: str
    render_fingerprint: str
    fingerprint: str
    image_asset_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicsFacts:
    stamp: PlanStamp
    graphics: tuple[GraphicFact, ...]
    #: Where the timeline ends once the appended end screen is included.
    timeline_end: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionFact:
    segment_id: str
    clip_ids: tuple[str, ...]
    section_id: str | None
    text: str
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    render_status: str  # rendered | reused | not_requested | not_available | failed
    asset_id: str | None
    asset_start_frame: int
    zone: str
    look: str
    word_count: int
    emphasised: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionsFacts:
    stamp: PlanStamp
    mode: str
    segments: tuple[CaptionFact, ...]
    language: str | None = None
    srt: str = ""
    vtt: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionFact:
    transition_id: str
    boundary: str  # cut | sequence_start | sequence_end
    outgoing_clip_id: str | None
    incoming_clip_id: str | None
    kind: str
    family: str
    preset_id: str
    alignment: str
    direction: str
    intensity: int
    status: str
    cut_at: Rational
    cut_frame: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    duration_frames: int
    required_out: int
    required_in: int
    available_out: int | None
    available_in: int | None
    fallback_wanted: str | None
    fallback_used: str | None
    fallback_reason: str
    overlay_asset_id: str | None
    overlay_state: str
    overlay_start: Rational | None
    overlay_duration: Rational | None
    audio_kind: str
    audio_curve: str
    audio_at: Rational
    audio_before: Rational
    audio_after: Rational
    audio_sample_rate: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionsFacts:
    stamp: PlanStamp
    transitions: tuple[TransitionFact, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MaskFact:
    mask_id: str
    kind: str
    #: ``(source_time, x0, y0, x1, y1)`` normalised.
    keyframes: tuple[tuple[Rational, float, float, float, float], ...]
    feather: float = 0.0
    track_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectFact:
    effect_id: str
    effect_type: str
    status: str  # planned | rendered | reused | bypassed | rejected | not_available | error
    preset: str
    mode: str
    parameters: tuple[tuple[str, float], ...]
    intensity: int
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    backend: str
    fallback: str
    masks: tuple[MaskFact, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSegmentFact:
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    rate: Rational
    start_frame: int
    end_frame: int
    audio: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TreatmentFact:
    treatment_id: str
    clip_id: str
    status: str  # original | planned | rendered | reused | error
    effects: tuple[EffectFact, ...]
    segments: tuple[TimeSegmentFact, ...]
    interpolation: str
    audio: str
    output_asset_id: str | None
    output_frames: int
    render_fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxFacts:
    stamp: PlanStamp
    treatments: tuple[TreatmentFact, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicSegmentFact:
    cue_id: str
    segment_id: str
    asset_id: str
    title: str
    role: str
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    gain_db: float
    fade_in: Rational
    fade_out: Rational
    licence_state: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SfxFact:
    item_id: str
    asset_id: str
    title: str
    purpose: str
    timeline_start: Rational
    timeline_end: Rational
    source_start: Rational
    source_end: Rational
    gain_db: float
    licence_state: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DuckFact:
    duck_id: str
    cue_id: str
    key: str
    timeline_start: Rational
    timeline_end: Rational
    depth_db: float
    attack_seconds: float
    release_seconds: float


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossfadeFact:
    crossfade_id: str
    kind: str
    timeline_at: Rational
    length: Rational
    outgoing_cue_id: str
    incoming_cue_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OriginalAudioFact:
    clip_id: str
    asset_id: str
    timeline_start: Rational
    timeline_end: Rational
    mode: str  # dominant | under_music | speech | muted_by_edit | voiceover
    music_over_it: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class MissingSoundFact:
    recommendation_id: str
    kind: str
    purpose: str
    priority: str
    timeline_start: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundFacts:
    stamp: PlanStamp
    identity: str
    identity_version: int
    music: tuple[MusicSegmentFact, ...] = ()
    sfx: tuple[SfxFact, ...] = ()
    ducking: tuple[DuckFact, ...] = ()
    crossfades: tuple[CrossfadeFact, ...] = ()
    original_audio: tuple[OriginalAudioFact, ...] = ()
    missing: tuple[MissingSoundFact, ...] = ()
    credits: str = ""
    plan_hash: str = ""


# --- the files and crops ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DerivativeFact:
    asset_id: str
    operation: str  # audio_improvement | video_improvement | proxy
    duration: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetFacts:
    """What the Media Library and the stored inspection say about one asset."""

    asset_id: str
    exists: bool
    file_available: bool = False
    media_type: str = ""  # audio | video | image | other
    #: ``None`` for an original; the recording a derived asset was made from.
    original_asset_id: str | None = None
    operation: str | None = None
    checksum: str | None = None
    duration: Rational | None = None
    frame_rate: Rational | None = None
    frame_rate_variable: bool = False
    has_video: bool = False
    has_audio: bool = False
    sample_rate: int | None = None
    channels: int | None = None
    width: int | None = None
    height: int | None = None
    derivatives: tuple[DerivativeFact, ...] = ()
    #: Whether a usable inspection was stored (the compiler never probes).
    inspected: bool = False
    #: The colour the stored inspection declares for the picture (``None``: not inspected).
    color: ColorTag | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CropFact:
    time: Rational  # source seconds
    x: float
    y: float
    w: float
    h: float
    interpolation: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReframeFacts:
    recommendation_id: str
    analysis_id: str
    target: str
    strategy: str
    executable: bool
    keyframes: tuple[CropFact, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CompileInputs:
    """One compilation's whole evidence. ``None`` for a Layer 4 block = that plan is not
    available (recorded as an input, never invented)."""

    source: TimelineSource
    style: StyleFacts
    graphics: GraphicsFacts | None = None
    captions: CaptionsFacts | None = None
    transitions: TransitionsFacts | None = None
    vfx: VfxFacts | None = None
    sound: SoundFacts | None = None
    assets: Mapping[str, AssetFacts] = field(default_factory=dict)
    reframing: Mapping[str, ReframeFacts] = field(default_factory=dict)
    #: Layer 4 plans that were REQUESTED in this run but did not deliver: (kind, why).
    undelivered: tuple[tuple[str, str], ...] = ()
