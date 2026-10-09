"""Vocabulary of video intelligence: versions, states and the three-tier rule.

The module OBSERVES (measured or detected facts) and ASSESSES (rubric-based judgments with
reasons and confidence). It never RECOMMENDS: no name in this module may express an action.
Names describe a property (``stability``), never an instruction.
"""

from enum import StrEnum

from media_house.shared.errors import InvariantViolation

#: Layout of every stored document. Readers refuse other schemas instead of guessing.
SCHEMA_VERSION = 2
#: Bump when how the signals are turned into the result changes (the derivations in ``derive``).
PROCESSING_VERSION = 2
#: Operation name of the final result document in the Media Library.
RESULT_OPERATION = "video_intelligence"

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class AnalyzerId(StrEnum):
    """The analyzers that look at pixels. Each is versioned and cached on its own."""

    SHOTS = "shots"
    MOTION = "motion"
    QUALITY = "quality"
    SALIENCY = "saliency"
    GEOMETRY = "geometry"
    ENTITIES = "entities"
    FACES = "faces"
    BODY = "body"
    TEXT = "text"
    EMBEDDINGS = "embeddings"
    APPEARANCE = "appearance"
    DESCRIPTIONS = "descriptions"


class CostTier(StrEnum):
    CHEAP = "cheap_deterministic"
    LOCAL_MODEL = "local_model"
    VLM = "vlm"


class AnalyzerState(StrEnum):
    """Explicit outcome of anything measured. Silent omission is forbidden."""

    OK = "ok"
    #: Measured but the evidence was too weak to say (e.g. a textureless picture for motion).
    UNKNOWN = "unknown"
    #: Not requested by the profile or no input to analyse (e.g. a shot with no sampled frame).
    NOT_ANALYZED = "not_analyzed"
    #: The question does not apply to this footage (e.g. exposure of an HDR transfer).
    NOT_APPLICABLE = "not_applicable"
    #: A needed dependency or input is missing.
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


class InputSource(StrEnum):
    """How an upstream input was obtained."""

    REUSED = "reused"  # found stored, nothing recomputed
    REQUESTED = "requested"  # the owner was asked for it
    NOT_AVAILABLE = "not_available"
    NOT_APPLICABLE = "not_applicable"


class CacheOutcome(StrEnum):
    REUSED = "reused"
    COMPUTED = "computed"
    NONE = "none"  # nothing was stored or looked up (e.g. the analyzer did not run)


class MeasuredOn(StrEnum):
    """Which version of the footage a measurement was taken on."""

    ORIGINAL = "original"
    IMPROVED = "improved"
    UNKNOWN = "unknown"


class Stabilization(StrEnum):
    """Whether the measured footage had its camera motion stabilized."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class BoundaryKind(StrEnum):
    START = "start"  # first shot of the video
    END = "end"  # end of the last shot
    HARD_CUT = "hard_cut"
    DISSOLVE = "dissolve"
    FADE_THROUGH_BLACK = "fade_through_black"


class CameraMovement(StrEnum):
    STATIC = "static"
    HANDHELD = "handheld"
    PAN = "pan"
    TILT = "tilt"
    PUSH_IN = "push_in"
    PULL_OUT = "pull_out"
    MIXED = "mixed"


class DeviceKind(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    GPU = "gpu"


class EntityKind(StrEnum):
    """What a detected thing is, at the coarse level the downstream stages need."""

    PERSON = "person"
    FACE = "face"
    ANIMAL = "animal"
    VEHICLE = "vehicle"
    OBJECT = "object"


class FramingType(StrEnum):
    WIDE = "wide"
    MEDIUM = "medium"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"


class OverlayKind(StrEnum):
    WATERMARK = "watermark"
    LOWER_THIRD = "lower_third"
    BURNED_IN_CAPTION = "burned_in_caption"
    SPLIT_SCREEN = "split_screen"


class GestureKind(StrEnum):
    HAND_RAISED = "hand_raised"
    POINTING = "pointing"
    OPEN_HAND = "open_hand"
    FIST = "fist"


class EventKind(StrEnum):
    """Visual events on the unified timeline. All are observations of something that changed."""

    SHOT_BOUNDARY = "shot_boundary"
    MOTION_PEAK = "motion_peak"
    ATTENTION_PEAK = "attention_peak"
    TRACK_ENTERED = "track_entered"
    TRACK_EXITED = "track_exited"
    TEXT_APPEARED = "text_appeared"
    TEXT_DISAPPEARED = "text_disappeared"
    SCREEN_CONTENT_CHANGE = "screen_content_change"
    SCROLLING = "scrolling"
    GESTURE = "gesture"
    FLASH = "flash"


class ScoreName(StrEnum):
    """Scored properties. Each names a property of the footage, never an instruction."""

    COMPOSITION = "composition"
    FRAMING = "framing"
    VISUAL_QUALITY = "visual_quality"
    SUBJECT_VISIBILITY = "subject_visibility"
    STABILITY = "stability"
    VISUAL_INTEREST = "visual_interest"
    TECHNICAL_USABILITY = "technical_usability"


class RelationKind(StrEnum):
    RETAKE = "retake"
    NEAR_DUPLICATE = "near_duplicate"


def unit_interval(value: float, name: str) -> float:
    """``value`` if it lies in [0, 1], else an invariant violation."""
    if not 0.0 <= value <= 1.0:
        raise InvariantViolation(f"{name} must be within [0, 1]", details={"value": value})
    return value
