"""Versions and vocabulary of the built timeline.

``DOCUMENT_TYPE`` / ``RESULT_OPERATION`` name the stored document and the library fingerprint
operation. A vocabulary addition bumps ``VOCABULARY_VERSION`` and is additive; a change of shape
bumps ``SCHEMA_VERSION`` (older documents are then refused, never reinterpreted).
"""

from enum import StrEnum

SCHEMA_VERSION = 1
BUILDER_VERSION = 3
VOCABULARY_VERSION = 1
PROFILE_VERSION = 1
DOCUMENT_TYPE = "built_timeline"
RESULT_OPERATION = "built_timeline"

#: The specification versions this builder reads (anything else is refused, never guessed).
SUPPORTED_SPEC_SCHEMAS = frozenset({1})
SUPPORTED_SPEC_VOCABULARIES = frozenset({1})


class TrackKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class Role(StrEnum):
    """What a track carries (the specification's roles; the stacking order is the track index)."""

    PRIMARY = "primary"
    COVER = "cover"
    GRAPHICS = "graphics"
    CAPTIONS = "captions"
    EFFECTS = "effects"
    DIALOGUE = "dialogue"
    PRODUCTION = "production"
    MUSIC = "music"
    AMBIENCE = "ambience"
    SFX = "sfx"


class ClipKind(StrEnum):
    """What a picture clip is. ``VFX`` is a primary clip whose effects are baked into its file."""

    PRIMARY = "primary"
    VFX = "vfx"
    COVER = "cover"
    GRAPHIC = "graphic"
    CAPTION = "caption"
    TRANSITION_OVERLAY = "transition_overlay"
    CUSTOM = "custom"  # made by a person


class BlendMode(StrEnum):
    NORMAL = "normal"  # the opaque picture replaces what is below
    ALPHA_OVER = "alpha_over"  # straight alpha over what is below


class FitMode(StrEnum):
    """How a source whose shape differs from the timeline's meets the frame."""

    CONTAIN = "contain"  # all of the picture, bars where the shapes differ (nothing is lost)
    COVER = "cover"  # the frame is filled, the overflow is cut off
    STRETCH = "stretch"  # both axes scaled independently
    NONE = "none"  # pixels 1:1


class Provenance(StrEnum):
    """Who made a timeline element. A person's work is never overwritten without a policy."""

    AI = "ai"  # carried out from the specification
    SYSTEM = "system"  # made by the builder (markers, groups, labels)
    HUMAN_MODIFIED = "human_modified"
    HUMAN_CREATED = "human_created"


class ClipState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"  # a dependency is missing: kept, reported, never faked


class Execution(StrEnum):
    """How an effect or transition is carried out (the specification's words)."""

    RENDERED = "rendered"
    BAKED = "baked"
    NATIVE = "native"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


class TransitionMode(StrEnum):
    """The builder's own vocabulary of transitions, independent of any application."""

    CUT = "cut"  # no overlap: hard / match / action / jump cut
    DISSOLVE = "dissolve"
    FADE = "fade"  # fade in / out
    DIP = "dip"  # dip to black / white
    OVERLAY = "overlay"  # a rendered clip over the cut
    UNSUPPORTED = "unsupported"  # outside the vocabulary: kept and reported, never replaced


class ChainStage(StrEnum):
    """Fixed processing order of one picture clip; nothing downstream may reorder it."""

    TIME_MAP = "time_map"
    FIT = "fit"
    CROP = "crop"
    EFFECT = "effect"
    COMPOSITE = "composite"


class Severity(StrEnum):
    ERROR = "error"  # the timeline cannot be built safely: nothing is stored
    UNSUPPORTED = "unsupported"  # an instruction the model cannot carry out: kept and reported
    WARNING = "warning"
    REVIEW = "review"  # a decision for a person (a creative conflict, a human-change conflict)
    INFO = "info"  # something deterministic that was done or noted


class BuildStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"  # warnings, unresolved dependencies, unsupported items or open reviews


class BuildMode(StrEnum):
    NEW = "new"
    REUSED = "reused"  # an identical build was already stored
    INCREMENTAL = "incremental"
    FULL = "full"


class ConflictPolicy(StrEnum):
    """What happens when the specification changes an element a person changed."""

    PRESERVE = "preserve"  # keep the person's version and say so
    REPLACE = "replace"  # use the specification's version and say what was replaced
    REVIEW = "review"  # keep the person's version, hold the new one, ask (default)


class EditKind(StrEnum):
    MODIFY = "modify"
    DELETE = "delete"
    CREATE = "create"


class CheckKind(StrEnum):
    """Checks that need a rendered preview or a media analysis, not timeline metadata."""

    BLACK_FRAMES = "black_frames"
    MISSING_FRAMES = "missing_frames"
    VISUAL_GAP = "visual_gap"
    BLANK_SECTION = "blank_section"
    MISSING_AUDIO = "missing_audio"
    AV_SYNC = "av_sync"


class CheckState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"  # the honest default: nothing rendered, nothing claimed


class DependencyKind(StrEnum):
    SOURCE_MEDIA = "source_media"
    DERIVATIVE = "derivative"
    RENDERED = "rendered"
    SOUND = "sound"
    FONT = "font"
    BRAND_ASSET = "brand_asset"
    IDENTITY = "identity"
    TRACKING = "tracking"
    TRANSITION_HANDLE = "transition_handle"


class DependencyState(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    REQUESTED = "requested"  # missing; the named owner can produce it
    UNVERIFIED = "unverified"  # named, but nothing here can check it
