"""Versions and vocabulary of the timeline specification.

``DOCUMENT_TYPE`` / ``RESULT_OPERATION`` name the stored document and the library fingerprint
operation. A vocabulary addition bumps ``VOCABULARY_VERSION`` and is additive; a change of shape
bumps ``SCHEMA_VERSION`` (older documents are then refused, never reinterpreted).
"""

from enum import StrEnum

SCHEMA_VERSION = 1
COMPILER_VERSION = 2
VOCABULARY_VERSION = 1
PROFILE_VERSION = 1
DOCUMENT_TYPE = "timeline_specification"
RESULT_OPERATION = "timeline_specification"
#: The sample rate audio positions are stated in unless the config says otherwise.
DEFAULT_SAMPLE_RATE = 48_000


class ColorProfile(StrEnum):
    """The working colour of the timeline. The edit never says, so the config does."""

    REC709 = "rec709"  # SDR video, the YouTube default
    SRGB = "srgb"
    REC2020_HLG = "rec2020_hlg"
    REC2020_PQ = "rec2020_pq"


#: ``(matrix, primaries, transfer, range)`` as the inspection names them (ffprobe vocabulary).
COLOR_TAGS: dict[ColorProfile, tuple[str, str, str, str]] = {
    ColorProfile.REC709: ("bt709", "bt709", "bt709", "tv"),
    ColorProfile.SRGB: ("bt709", "bt709", "iec61966-2-1", "pc"),
    ColorProfile.REC2020_HLG: ("bt2020nc", "bt2020", "arib-std-b67", "tv"),
    ColorProfile.REC2020_PQ: ("bt2020nc", "bt2020", "smpte2084", "tv"),
}


class TrackKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class TrackRole(StrEnum):
    """What a track carries. Order inside a kind is the stacking order, bottom to top."""

    PRIMARY = "primary"  # V: the main layer of the edit
    COVER = "cover"  # V: B-roll / cover footage, picture in picture
    GRAPHICS = "graphics"  # V: rendered graphics (titles, lower thirds, ...)
    CAPTIONS = "captions"  # V: rendered caption groups
    EFFECTS = "effects"  # V: transition overlays and other composited elements
    DIALOGUE = "dialogue"  # A: the voice / the recording's sound
    PRODUCTION = "production"  # A: sound of cover footage
    MUSIC = "music"  # A
    AMBIENCE = "ambience"  # A
    SFX = "sfx"  # A


VIDEO_ROLE_ORDER = (
    TrackRole.PRIMARY,
    TrackRole.COVER,
    TrackRole.GRAPHICS,
    TrackRole.CAPTIONS,
    TrackRole.EFFECTS,
)
AUDIO_ROLE_ORDER = (
    TrackRole.DIALOGUE,
    TrackRole.PRODUCTION,
    TrackRole.MUSIC,
    TrackRole.AMBIENCE,
    TrackRole.SFX,
)


class Composite(StrEnum):
    """How a track's picture meets the tracks below it (application independent)."""

    NORMAL = "normal"  # opaque picture replaces what is below
    ALPHA_OVER = "alpha_over"  # straight alpha over what is below (ProRes 4444 graphics)


class AssetRelation(StrEnum):
    """How the asset a clip uses relates to the recording it came from."""

    ORIGINAL = "original"
    IMPROVED_AUDIO = "improved_audio"
    IMPROVED_VIDEO = "improved_video"
    PROXY = "proxy"
    RENDERED = "rendered"  # made by a Layer 4 module (graphic, VFX clip, stem)
    LIBRARY = "library"  # a sound-library or brand asset


class DerivativePolicy(StrEnum):
    """Which version of a recording a clip points at. The edit never says, so the config does."""

    ORIGINAL = "original"  # always the recording itself (default)
    PREFER_IMPROVED = "prefer_improved"  # the stored improved audio / video where one exists


class ItemOrigin(StrEnum):
    EDIT = "edit"
    VFX = "vfx"
    GRAPHICS = "graphics"
    CAPTIONS = "captions"
    TRANSITION = "transition"
    SOUND = "sound"


class Execution(StrEnum):
    """How an item is carried out. ``RENDERED``: a library asset holds the pixels / samples."""

    RENDERED = "rendered"
    BAKED = "baked"  # an effect already inside the clip's derived asset
    NATIVE = "native"  # the timeline application performs it (dissolve, fade, ...)
    FALLBACK = "fallback"  # the plan's own safe fallback is used
    UNAVAILABLE = "unavailable"  # nothing can carry it out; reported


class IssueClass(StrEnum):
    CRITICAL = "critical"  # the timeline cannot be executed safely: nothing is stored
    WARNING = "warning"  # executable, but something needs attention
    CREATIVE_CONFLICT = "creative_conflict"  # two instructions contradict; both are kept
    AUTO_RESOLVED = "auto_resolved"  # a deterministic technical fix; the intent is unchanged


class DependencyKind(StrEnum):
    SOURCE_MEDIA = "source_media"
    DERIVATIVE = "derivative"
    RENDERED_GRAPHIC = "rendered_graphic"
    RENDERED_CAPTION = "rendered_caption"
    RENDERED_OVERLAY = "rendered_overlay"
    RENDERED_CLIP = "rendered_clip"
    SOUND = "sound"
    FONT = "font"
    BRAND_ASSET = "brand_asset"
    IMAGE = "image"
    IDENTITY = "identity"


class DependencyState(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    FALLBACK = "fallback"  # missing, a defined fallback stands in
    REQUESTED = "requested"  # missing; the named owner can produce it (a pipeline request)


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"  # something fell back, or a creative conflict is left open


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"  # read from the stored plan
    NOT_AVAILABLE = "not_available"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"
