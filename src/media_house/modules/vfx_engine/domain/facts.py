"""What the planner reads: the edit's shots (``VfxShots``, the module's own input DTO) and the
per-recording facts the application reads through the owners' catalogs.

Time domains are never mixed (contracts sec. 3): ``source_*`` are exact seconds on the recording's
container clock, ``timeline_*`` exact seconds from the start of the edit. Every time is an exact
``Rational``; the one mapping between them goes through a clip.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.vfx_engine.domain.values import ClipAudio, EffectType, RequestKind


def fr(value: Rational) -> Fraction:
    """A ``Rational`` as an exact ``Fraction`` (``Rational`` has no arithmetic)."""
    return Fraction(value.numerator, value.denominator)


def rat(value: Fraction) -> Rational:
    if value < 0:
        raise ValueError(f"a time cannot be negative ({value})")
    return Rational(value.numerator, value.denominator)


# --- the input DTO -------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    """The edit plan the effects were planned for (``None`` on the no-speech path)."""

    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceSpan:
    source_start: Rational
    source_end: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxClip:
    """One placed piece of footage at speed 1 (a placement on the edit's picture track)."""

    clip_id: str
    asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    #: 0 = main picture, 1+ = cover / overlay layers.
    layer: int = 0
    section_id: str | None = None
    beat_id: str | None = None
    audio: ClipAudio = ClipAudio.SOURCE_SYNC
    #: Source spans with speech in this clip (retiming them would break lip sync).
    speech: tuple[SourceSpan, ...] = ()

    @property
    def timeline_end(self) -> Rational:
        return rat(fr(self.timeline_start) + fr(self.source_end) - fr(self.source_start))


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxRequest:
    """An editorial or personal wish for an effect at a TIMELINE time (``EffectRequest``)."""

    request_id: str
    kind: RequestKind
    timeline_at: Rational
    #: ``None``: instantaneous, the profile decides the length.
    duration: Rational | None = None
    priority: float = 0.5
    reason: str = ""
    section_id: str | None = None
    #: For ``EXPLICIT``: the effect a person asked for.
    effect: EffectType | None = None
    #: For ``PRIVACY``: the tracks to conceal (empty = every track of the configured kinds).
    track_ids: tuple[str, ...] = ()
    decision_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxShots:
    """The VFX engine's own view of an edit (mapped by the pipeline from the edit plan, or from
    the inspection on the no-speech path: ``edit=None``, one clip per recording, timeline =
    source, no sections, beats or speech)."""

    edit: EditRef | None
    project_id: str | None
    frame_rate: Rational
    output_width: int
    output_height: int
    clips: tuple[VfxClip, ...]
    requests: tuple[VfxRequest, ...] = ()
    #: Story format or style profile name (selects the default effect profile).
    format: str | None = None
    #: The frame rate was assumed (VFR or unknown source): seconds stay authoritative.
    frame_rate_assumed: bool = False

    @classmethod
    def no_speech(
        cls,
        asset_ids_and_durations: tuple[tuple[str, Rational], ...],
        *,
        frame_rate: Rational,
        width: int,
        height: int,
        format_hint: str | None = None,
    ) -> "VfxShots":
        """The no-speech path (contracts sec. 2): every recording back to back at speed 1."""
        clips: list[VfxClip] = []
        at = Fraction(0)
        for asset_id, duration in asset_ids_and_durations:
            clips.append(
                VfxClip(
                    clip_id=f"clip:{asset_id}",
                    asset_id=asset_id,
                    source_start=Rational(0, 1),
                    source_end=duration,
                    timeline_start=rat(at),
                )
            )
            at += fr(duration)
        return cls(
            edit=None,
            project_id=None,
            frame_rate=frame_rate,
            output_width=width,
            output_height=height,
            clips=tuple(clips),
            format=format_hint,
        )


# --- facts read through the owners' catalogs -----------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Box:
    """A normalised, display-oriented box (0-1)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def contains(self, other: "Box", tolerance: float = 1e-6) -> bool:
        return (
            self.x0 <= other.x0 + tolerance
            and self.y0 <= other.y0 + tolerance
            and self.x1 >= other.x1 - tolerance
            and self.y1 >= other.y1 - tolerance
        )


FULL_FRAME = Box(x0=0.0, y0=0.0, x1=1.0, y1=1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackSample:
    source_time: Rational
    box: Box
    confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackFact:
    track_id: str
    kind: str  # person | face | animal | vehicle | object
    shot_id: str
    confidence: float
    samples: tuple[TrackSample, ...]
    occluded_frames: int = 0
    is_main_subject: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class FlashFact:
    """A visual flash (``EventKind.FLASH``): on fireworks footage, a burst."""

    event_id: str
    source_time: Rational
    strength: float
    shot_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BangFact:
    """An audio impulse (audio_intelligence ``impulse``: a bang), already on the picture's clock
    (audio seconds + the stored audio/video clock correction, when one exists)."""

    event_id: str
    source_time: Rational
    strength: float
    confidence: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotFact:
    shot_id: str
    source_start: Rational
    source_end: Rational
    luma_p50: float | None = None
    luma_p99: float | None = None
    clipped: float | None = None
    crushed: float | None = None
    noise_sigma: float | None = None
    sharpness: float | None = None
    camera_speed: float | None = None
    motion_energy: float | None = None
    main_subject_track_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CropFact:
    """Reframing's executable window over a span (normalised crop of the source picture)."""

    recommendation_id: str
    source_start: Rational
    source_end: Rational
    keyframes: tuple[tuple[Rational, Box], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFacts:
    """Everything known about one recording (``None`` fields: not available, never guessed)."""

    asset_id: str
    width: int
    height: int
    frame_rate: Rational | None
    variable_frame_rate: bool | None = None
    bit_depth: int | None = None
    pixel_format: str | None = None
    #: Picture analysis present (shots, tracks, flashes, quality)?
    analysed: bool = False
    analysis_id: str | None = None
    shots: tuple[ShotFact, ...] = ()
    tracks: tuple[TrackFact, ...] = ()
    flashes: tuple[FlashFact, ...] = ()
    #: Median luma (0-1) of the sampled frames (video_intelligence's ``luma_median`` curve).
    luma: tuple[tuple[Rational, float], ...] = ()
    #: Stored audio bangs (burst candidates when the picture analysis found no flash).
    bangs: tuple[BangFact, ...] = ()
    #: How audio times reached the picture clock: ``alignment`` (stored correction) or
    #: ``assumed`` (no stored alignment: both clocks taken as equal). Empty: no bangs read.
    bang_clock: str = ""
    #: Reframing analysis for the output aspect, if one is stored.
    reframing_id: str | None = None
    crops: tuple[CropFact, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleLimits:
    """What the channel style allows an effect (from ``ResolvedStyle``; never branding)."""

    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str
    intensity_ceiling: int
    motion_character: str
    #: Palette colours the profile may tint with (name -> ``#RRGGBB``).
    palette: tuple[tuple[str, str], ...] = field(default=())
