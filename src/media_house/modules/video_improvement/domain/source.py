"""What is known about a source video, and how its source profile is decided.

A ``SourceProfile`` describes the CAPTURE: which colour encoding the camera writes and how
scene-referred (log/flat) footage is rendered for a display. It carries no creative look; that
is the processing profile's job (``profiles.py``). Adding a camera is one entry in ``_BUILTIN``,
never new processing code.

Which profile applies is decided deterministically, strongest evidence first:

1. the caller named it (explicit),
2. camera metadata identifies the camera (from a Media Inspection result or the file itself),
3. the file declares a colour space (same two origins),
4. a safe convention: untagged HD video is Rec.709 (what every player assumes),
5. the generic profile, whose colour space is ``unknown`` and is never guessed.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.video_improvement.domain.color import (
    ColorSpec,
    Primaries,
    Transfer,
)
from media_house.modules.video_improvement.domain.errors import InvalidProfile
from media_house.modules.video_improvement.domain.values import (
    FactsSource,
    FrameRate,
    ProfileOrigin,
)

_HD_MIN_HEIGHT = 720


@dataclass(frozen=True, slots=True)
class VideoFacts:
    """The technical facts the processing needs, from Media Inspection or a minimal probe."""

    width: int
    height: int
    frame_rate: FrameRate
    duration: float
    frame_count: int | None
    pixel_format: str | None
    bit_depth: int | None
    #: What the file declares (``unknown`` parts when untagged).
    color: ColorSpec
    #: Declared range (``tv``/``pc``) and matrix (e.g. ``bt709``); ``None`` when untagged.
    color_range: str | None
    color_matrix: str | None
    interlaced: bool
    #: Clockwise degrees a player turns the picture (display matrix); the stored size is unchanged.
    rotation: int
    variable_frame_rate: bool
    audio_stream_count: int
    timecode: str | None
    camera_make: str | None
    camera_model: str | None
    #: Container and stream tags, keys lower-cased (firmware, encoder, handler, ...).
    tags: Mapping[str, str]
    source: FactsSource

    @property
    def is_hd(self) -> bool:
        return self.height >= _HD_MIN_HEIGHT


@dataclass(frozen=True, slots=True)
class Rendering:
    """How scene-referred footage becomes a pleasing display image (a neutral base rendering).

    ``contrast`` bends the tone curve about mid-grey (18% reflectance); ``shoulder_knee`` is the
    display level above which highlights roll off smoothly towards white.
    """

    contrast: float = 1.0
    shoulder_knee: float = 0.80

    def __post_init__(self) -> None:
        if not 0.5 <= self.contrast <= 2.0 or not 0.5 <= self.shoulder_knee < 1.0:
            raise InvalidProfile("rendering contrast must be 0.5-2 and the knee 0.5-1")


@dataclass(frozen=True, slots=True)
class CameraMatch:
    """Metadata that identifies a camera. Any one rule is enough."""

    firmware_prefixes: tuple[str, ...] = ()
    make_contains: tuple[str, ...] = ()
    model_contains: tuple[str, ...] = ()

    def evidence(self, facts: VideoFacts) -> str | None:
        firmware = facts.tags.get("firmware", "")
        for prefix in self.firmware_prefixes:
            if firmware.startswith(prefix):
                return f"firmware {firmware}"
        make, model = (facts.camera_make or "").lower(), (facts.camera_model or "").lower()
        if self.make_contains and self.model_contains:
            makes = any(m in make for m in self.make_contains)
            models = any(m in model for m in self.model_contains)
            if makes and models:
                return f"camera {facts.camera_make} {facts.camera_model}"
        return None


@dataclass(frozen=True, slots=True)
class SourceProfile:
    name: str
    description: str
    #: Bump when the profile's values change meaning (it is part of the cache fingerprint).
    version: int = 1
    input_color: ColorSpec = field(default_factory=ColorSpec)
    #: Processing profile used when the caller names none.
    default_processing: str = "natural"
    rendering: Rendering = field(default_factory=Rendering)
    #: Identifies the camera from metadata; profiles without it are only chosen explicitly.
    match: CameraMatch | None = None

    def to_config(self) -> dict[str, str | int | float]:
        return {
            "name": self.name,
            "version": self.version,
            "input_transfer": self.input_color.transfer.value,
            "input_primaries": self.input_color.primaries.value,
            "default_processing": self.default_processing,
            "rendering_contrast": self.rendering.contrast,
            "rendering_knee": self.rendering.shoulder_knee,
        }


REC709 = ColorSpec(Transfer.BT709, Primaries.BT709)

_BUILTIN: dict[str, SourceProfile] = {
    p.name: p
    for p in (
        SourceProfile(
            "generic",
            "Nothing is known about the source: its colour space is not guessed, so the colour "
            "stage does not run (denoising and sharpening still can).",
        ),
        SourceProfile("rec709", "Rec.709 video from any camera or software.", input_color=REC709),
        SourceProfile(
            "studio_camera",
            "A studio camera recording Rec.709: controlled light, clean signal.",
            input_color=REC709,
            default_processing="studio",
        ),
        SourceProfile(
            "gopro_hero_9",
            "GoPro HERO9 Black in its standard colour mode (Rec.709, vivid and sharpened). "
            "Protune Flat is not detectable from the file: name 'gopro_flat' for it.",
            input_color=REC709,
            default_processing="outdoor",
            match=CameraMatch(firmware_prefixes=("HD9.",)),
        ),
        SourceProfile(
            "gopro_flat",
            "GoPro Protune Flat: base-113 log curve, treated as Rec.709 primaries.",
            input_color=ColorSpec(Transfer.GOPRO_PROTUNE, Primaries.BT709),
            default_processing="outdoor",
            rendering=Rendering(contrast=1.15, shoulder_knee=0.80),
        ),
        SourceProfile(
            "sony_slog3",
            "Sony S-Log3 with S-Gamut3.Cine primaries.",
            input_color=ColorSpec(Transfer.SLOG3, Primaries.SGAMUT3_CINE),
            rendering=Rendering(contrast=1.20, shoulder_knee=0.75),
        ),
        SourceProfile(
            "panasonic_vlog",
            "Panasonic V-Log with V-Gamut primaries.",
            input_color=ColorSpec(Transfer.VLOG, Primaries.VGAMUT),
            rendering=Rendering(contrast=1.20, shoulder_knee=0.75),
        ),
    )
}

DEFAULT_SOURCE_PROFILE = "generic"


def source_profile_names() -> tuple[str, ...]:
    return tuple(sorted(_BUILTIN))


def get_source_profile(name: str) -> SourceProfile:
    try:
        return _BUILTIN[name]
    except KeyError:
        raise InvalidProfile(
            f"unknown source profile {name!r}; choose one of {', '.join(source_profile_names())}"
        ) from None


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """The decided profile and why: the origin and the evidence behind it."""

    profile: SourceProfile
    origin: ProfileOrigin
    evidence: str


def _metadata_origin(facts: VideoFacts) -> ProfileOrigin:
    if facts.source is FactsSource.INSPECTION:
        return ProfileOrigin.INSPECTION
    return ProfileOrigin.EMBEDDED_METADATA


def resolve_source_profile(explicit: str | None, facts: VideoFacts) -> ResolvedSource:
    """The source profile for ``facts``; an explicit name always wins over any detection."""
    if explicit is not None:
        return ResolvedSource(get_source_profile(explicit), ProfileOrigin.EXPLICIT, explicit)
    origin = _metadata_origin(facts)
    for profile in _BUILTIN.values():
        if profile.match is not None and (found := profile.match.evidence(facts)):
            return ResolvedSource(profile, origin, found)
    if facts.color.known and facts.color == REC709:
        return ResolvedSource(_BUILTIN["rec709"], origin, "declared bt709 colour tags")
    untagged = (
        facts.color.transfer is Transfer.UNKNOWN and facts.color.primaries is Primaries.UNKNOWN
    )
    if untagged and facts.is_hd:
        return ResolvedSource(
            _BUILTIN["rec709"], ProfileOrigin.DETECTED, "untagged HD video is read as Rec.709"
        )
    return ResolvedSource(
        _BUILTIN[DEFAULT_SOURCE_PROFILE], ProfileOrigin.DEFAULT, "colour space not determinable"
    )
