"""The narrow facts the adapter reads from a built timeline.

The domain may import no other module, so ``application/views.py`` maps the builder's
``BuiltTimeline`` into these. Only what executing a timeline in a timeline application needs is
here: positions in frames, the part of the file each clip plays, what must be applied, and the
references to the creator identity. The adapter decides nothing creative from them.
"""

from dataclasses import dataclass

from media_house.core.domain import Rational


@dataclass(frozen=True, slots=True)
class SettingsFact:
    width: int
    height: int
    frame_rate: Rational
    sample_rate: int
    audio_channels: str
    duration_frames: int
    color_profile: str
    fit: str  # contain | cover | stretch | none


@dataclass(frozen=True, slots=True)
class MediaFact:
    """One file the timeline plays (a library asset)."""

    asset_id: str
    name: str
    media_type: str  # video | audio | image
    relation: str
    original_asset_id: str
    frame_rate: Rational | None
    width: int | None
    height: int | None
    proxy_asset_id: str | None
    checksum: str | None
    available: bool
    #: The track roles of the clips that play it (graphics, captions, music ...).
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrackFact:
    track_id: str
    kind: str  # video | audio
    role: str
    index: int
    lane: int
    name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ZoomKey:
    """One key of a moving window, as a zoom about a fixed point of the picture.

    ``frame`` counts from the clip's first frame; ``pivot_*`` is the point of the source picture
    (normalised, top-left) that stays where it is while the picture grows by ``size``. ``curve``
    is how the move INTO this key runs: ``hold`` (jumps), ``linear``, ``ease_in`` (starts slow,
    ends fast), ``ease_out`` (starts fast, settles slowly) or ``ease_in_out``.
    """

    frame: int
    size: float
    pivot_x: float
    pivot_y: float
    curve: str


@dataclass(frozen=True, slots=True)
class BlurKey:
    """One key of a picture blur; ``frame`` counts from the clip's first frame, ``size`` 0 is
    sharp, ``curve`` is how the change INTO this key runs (as ``ZoomKey.curve``)."""

    frame: int
    size: float
    curve: str


@dataclass(frozen=True, slots=True)
class CropFact:
    """A reframing window of the display-oriented source picture (normalised, top-left)."""

    x: float
    y: float
    w: float
    h: float
    #: The window changes over the clip: no clip property can carry it.
    moves: bool
    keys: int
    #: The moving window as zoom keys (empty when it does not move).
    motion: tuple[ZoomKey, ...] = ()
    #: A screen recording floated as a card: the page window (x, y, w, h) shown whole on a dark
    #: ground; ``motion`` then scales the fitted card (1 = fitted).
    card: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class RetimeFact:
    kind: str  # constant | ramp | freeze | reverse
    #: The timeline application must perform it (not already baked into the file).
    applied_by_application: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ClipFact:
    clip_id: str
    track_id: str
    kind: str  # video | audio
    clip_kind: (
        str  # primary | vfx | cover | graphic | caption | transition_overlay | custom | sound
    )
    name: str
    asset_id: str | None
    #: Timeline frames (the start, and the frame after the last one).
    start_frame: int
    end_frame: int
    #: First frame played, in frames of the FILE (its own rate), and how many it plays at 1x.
    source_in_frame: int
    source_frames: int
    opacity: float
    enabled: bool
    fit: str
    crop: CropFact | None
    retime: RetimeFact | None
    #: Effects the application must apply (those a baked or rendered file already shows are not).
    native_effects: tuple[str, ...]
    #: A gain or fades the mix must apply (``audio_mix_engine``), not this adapter.
    mix_work: bool
    link: str | None
    provenance: str
    digest: str
    resolved: bool
    #: The picture blurs while a focus graphic is shown over it (empty = sharp).
    blur: tuple[BlurKey, ...] = ()
    #: The static volume (dB) the mix gives this sound clip; ``None``: no mix level known.
    gain_db: float | None = None

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(frozen=True, slots=True)
class TransitionFact:
    transition_id: str
    track_id: str
    kind: str
    mode: str  # cut | dissolve | fade | dip | overlay | unsupported
    cut_frame: int
    start_frame: int
    end_frame: int
    duration_frames: int
    from_clip: str | None
    to_clip: str | None
    tail_frames: int
    head_frames: int
    alignment: str
    preset_id: str
    supported: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarkerFact:
    marker_id: str
    kind: str  # section | review
    frame: int
    duration_frames: int
    name: str
    note: str
    severity: str | None


@dataclass(frozen=True, slots=True)
class CueFact:
    index: int
    start_frame: int
    end_frame: int
    text: str


@dataclass(frozen=True, slots=True)
class SubtitleFact:
    language: str | None
    cues: tuple[CueFact, ...]
    srt: str


@dataclass(frozen=True, slots=True)
class FontFact:
    family: str
    available: bool


@dataclass(frozen=True, slots=True)
class IdentityFact:
    """REFERENCES to the creator identity. Nothing of the design is copied."""

    identity_id: str
    identity_version: int
    style_fingerprint: str
    format_profile: str
    fonts: tuple[FontFact, ...]
    presets: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class TimelineFacts:
    timeline_id: str
    anchor_asset_id: str
    project_id: str | None
    name: str
    schema_version: int
    build_version: int
    build_hash: str
    settings: SettingsFact
    identity: IdentityFact
    media: tuple[MediaFact, ...]
    tracks: tuple[TrackFact, ...]
    clips: tuple[ClipFact, ...]
    transitions: tuple[TransitionFact, ...]
    markers: tuple[MarkerFact, ...]
    subtitles: SubtitleFact | None
    #: Required files that are missing or changed, found by the builder (it still stored a build).
    unresolved: tuple[str, ...]

    def clip(self, clip_id: str) -> ClipFact | None:
        return next((c for c in self.clips if c.clip_id == clip_id), None)

    def media_of(self, asset_id: str) -> MediaFact | None:
        return next((m for m in self.media if m.asset_id == asset_id), None)
