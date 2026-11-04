"""Versions, document names and the closed vocabularies of the VFX engine."""

from enum import StrEnum
from typing import Final

#: Shape of the stored ``vfx_plan`` document.
SCHEMA_VERSION: Final = 1
#: How effects are chosen (bump when the same inputs would give another plan).
PLANNER_VERSION: Final = 2  # 2: stored audio bangs mark bursts (peak refined by luma)
#: The effect vocabulary below (additions bump it).
VOCABULARY_VERSION: Final = 1
#: Shape of the configuration knobs (``profile.py``).
PROFILE_VERSION: Final = 1
#: How a treatment is turned into a filter graph (bump when the same spec renders differently).
RENDER_VERSION: Final = 1

DOCUMENT_TYPE: Final = "vfx_plan"
RESULT_OPERATION: Final = "vfx_plan"
RENDER_OPERATION: Final = "vfx_render"

#: Rendered clips: ProRes 422 HQ, 10-bit 4:2:2 (contracts sec. 5); never 8-bit mid-pipeline.
RENDER_CODEC: Final = "prores_ks"
RENDER_PROFILE: Final = "hq"
RENDER_PIX_FMT: Final = "yuv422p10le"
#: The working space every pixel effect runs in (32-bit float RGB).
WORKING_PIX_FMT: Final = "gbrpf32le"


class EffectType(StrEnum):
    #: The explicit decision to use the footage as it is.
    NO_EFFECT = "no_effect"
    PUNCH_IN = "punch_in"
    SLOW_MOTION = "slow_motion"
    SPEED_RAMP = "speed_ramp"
    FREEZE_FRAME = "freeze_frame"
    REVERSE = "reverse"
    BLUR = "blur"
    BACKGROUND_BLUR = "background_blur"
    GLOW = "glow"
    LIGHT_RAYS = "light_rays"
    VIGNETTE = "vignette"
    FILM_GRAIN = "film_grain"
    FILM_DAMAGE = "film_damage"
    CHROMATIC_ABERRATION = "chromatic_aberration"
    LENS_DISTORTION = "lens_distortion"
    LENS_FLARE = "lens_flare"
    MOTION_BLUR = "motion_blur"
    STYLIZE = "stylize"
    PRIVACY_BLUR = "privacy_blur"
    COMPOSITE = "composite"
    SCREEN_REPLACEMENT = "screen_replacement"
    BACKGROUND_REPLACEMENT = "background_replacement"
    SUBJECT_ISOLATION = "subject_isolation"
    OBJECT_REMOVAL = "object_removal"


#: Effects that change which source frame is shown when (they produce a ``TimeMap``).
TIME_EFFECTS: Final = frozenset(
    {
        EffectType.SLOW_MOTION,
        EffectType.SPEED_RAMP,
        EffectType.FREEZE_FRAME,
        EffectType.REVERSE,
    }
)

#: Visual weight of each effect on the 0-4 scale the style's ``motion.intensity_ceiling`` uses.
INTENSITY: Final[dict[EffectType, int]] = {
    EffectType.NO_EFFECT: 0,
    EffectType.PUNCH_IN: 1,
    EffectType.SLOW_MOTION: 2,
    EffectType.SPEED_RAMP: 3,
    EffectType.FREEZE_FRAME: 2,
    EffectType.REVERSE: 3,
    EffectType.BLUR: 1,
    EffectType.BACKGROUND_BLUR: 1,
    EffectType.GLOW: 2,
    EffectType.LIGHT_RAYS: 3,
    EffectType.VIGNETTE: 1,
    EffectType.FILM_GRAIN: 1,
    EffectType.FILM_DAMAGE: 3,
    EffectType.CHROMATIC_ABERRATION: 3,
    EffectType.LENS_DISTORTION: 3,
    EffectType.LENS_FLARE: 3,
    EffectType.MOTION_BLUR: 2,
    EffectType.STYLIZE: 3,
    EffectType.PRIVACY_BLUR: 0,  # protection, not decoration: never limited by a look ceiling
    EffectType.COMPOSITE: 2,
    EffectType.SCREEN_REPLACEMENT: 2,
    EffectType.BACKGROUND_REPLACEMENT: 3,
    EffectType.SUBJECT_ISOLATION: 2,
    EffectType.OBJECT_REMOVAL: 1,
}


class EffectStatus(StrEnum):
    #: Chosen and ready to render (``render=False`` plans stop here).
    PLANNED = "planned"
    #: Rendered into the treatment's output asset in this run.
    RENDERED = "rendered"
    #: The identical render already existed in the library and was reused.
    REUSED = "reused"
    #: Considered and deliberately not applied (restraint, "do not destroy good media").
    BYPASSED = "bypassed"
    #: Asked for but refused (ceiling, profile, conflict, repetition, person's decision).
    REJECTED = "rejected"
    #: Needs a capability this machine does not have (model, planar track).
    NOT_AVAILABLE = "not_available"
    #: Planned but unusable (failed render or render QC); its fallback is used instead.
    ERROR = "error"


class TreatmentStatus(StrEnum):
    #: No effect: the edit uses the original footage.
    ORIGINAL = "original"
    PLANNED = "planned"
    RENDERED = "rendered"
    REUSED = "reused"
    #: The render failed QC; the recorded fallback applies.
    ERROR = "error"


class RequestKind(StrEnum):
    """Why a request exists (editorial requests are mapped by the pipeline)."""

    #: Editing wants weight on a moment (``EffectRequest`` REFRAME / EMPHASIS).
    EMPHASIS = "emphasis"
    #: A payoff / reveal moment.
    REVEAL = "reveal"
    #: Conceal people or things (track ids, or every track of the configured kinds).
    PRIVACY = "privacy"
    #: A person asked for one named effect.
    EXPLICIT = "explicit"


class ClipAudio(StrEnum):
    """What the edit does with the clip's own sound (the placement's audio behaviour)."""

    SOURCE_SYNC = "source_sync"
    VOICEOVER = "voiceover"
    MUTED = "muted"


class AudioIntent(StrEnum):
    """What should happen to the clip's audio under a time map (never processed here)."""

    #: Speed 1: the source audio of the same span plays in sync.
    SOURCE_SYNC = "source_sync"
    #: The picture is retimed, the placement's original real-time audio keeps playing.
    KEEP_ORIGINAL = "keep_original"
    #: Silence under the retimed span.
    MUTE = "mute"
    #: The audio of the shown source span is time-stretched (pitch kept) to the picture.
    PITCH_PRESERVING_RETIME = "pitch_preserving_retime"


class SegmentKind(StrEnum):
    NORMAL = "normal"
    SLOW = "slow"
    FAST = "fast"
    FREEZE = "freeze"
    REVERSE = "reverse"


class Interpolation(StrEnum):
    """How missing frames of a slow segment are made."""

    #: Repeat the nearest source frame (exact pixels, visible stutter below ~0.5x).
    DUPLICATE = "duplicate"
    #: Blend neighbouring frames (FFmpeg ``framerate``, 12-bit).
    BLEND = "blend"
    #: Motion-compensated (FFmpeg ``minterpolate``): 8-bit only, slow; needs explicit consent.
    MOTION = "motion"


class MaskKind(StrEnum):
    FULL_FRAME = "full_frame"
    RECT = "rect"
    ELLIPSE = "ellipse"
    TRACKED = "tracked"


class Backend(StrEnum):
    FFMPEG = "ffmpeg"
    SUBJECT_MATTER = "subject_matter"
    INPAINTER = "inpainter"
    PLANAR_TRACKER = "planar_tracker"
    NONE = "none"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
