"""Vocabulary and versions of the transition engine.

The vocabulary is the curated transition library. Each kind states its family, its intensity on
the 0-4 scale (shared with ``visual_style_engine``'s intensity ceiling), whether the two pictures
overlap (an overlap needs source handles beyond the cut), whether it is a graphic overlay that
``motion_graphics_engine`` renders over the cut, and whether it needs motion evidence.

Intensity scale (higher is never "better"):

== ===================== ===================================================
0  cut                   hard / match / action / jump cut (the default)
1  very subtle           short dissolve, fade, dip to black, motion match
2  noticeable but clean  slide, push, zoom, blur, wipe, pixel dissolve, white dip
3  expressive            whip pan, flash, shape, mask reveal, zoom tunnel
4  highly stylised       glitch
== ===================== ===================================================
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

PLANNER_VERSION: Final = 2  # 2: identity transition language, wipe direction, drift phase
SCHEMA_VERSION: Final = 1
VOCABULARY_VERSION: Final = 1
PROFILE_VERSION: Final = 1
DOCUMENT_TYPE: Final = "transition_plan"
RESULT_OPERATION: Final = "transition_plan"
#: Sample rate assumed for audio durations when the inspection names none.
DEFAULT_SAMPLE_RATE: Final = 48_000


class TransitionKind(StrEnum):
    HARD_CUT = "hard_cut"
    MATCH_CUT = "match_cut"
    ACTION_CUT = "action_cut"
    JUMP_CUT = "jump_cut"
    CROSS_DISSOLVE = "cross_dissolve"
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"
    DIP_TO_BLACK = "dip_to_black"
    DIP_TO_WHITE = "dip_to_white"
    MOTION_MATCH = "motion_match"
    SLIDE = "slide"
    PUSH = "push"
    ZOOM = "zoom"
    WHIP_PAN = "whip_pan"
    BLUR = "blur"
    FLASH = "flash"
    GLITCH = "glitch"
    WIPE = "wipe"
    SHAPE = "shape"
    MASK_REVEAL = "mask_reveal"
    ZOOM_TUNNEL = "zoom_tunnel"
    PIXEL_DISSOLVE = "pixel_dissolve"


class Family(StrEnum):
    CUT = "cut"
    BLEND = "blend"
    MOVEMENT = "movement"
    STYLISED = "stylised"
    GRAPHIC = "graphic"


class Alignment(StrEnum):
    """Where the transition sits on the cut: centred, starting at it, or ending at it."""

    CENTER = "center"
    START = "start"
    END = "end"


class BoundaryKind(StrEnum):
    CUT = "cut"
    SEQUENCE_START = "sequence_start"
    SEQUENCE_END = "sequence_end"


class Context(StrEnum):
    """What the boundary is, as read from the edit and the upstream analyses."""

    CONTINUOUS = "continuous"
    JUMP_CUT = "jump_cut"
    TIME_CHANGE = "time_change"
    ANGLE_CHANGE = "angle_change"
    SCENE_CHANGE = "scene_change"
    SOURCE_CHANGE = "source_change"
    SECTION_CHANGE = "section_change"
    REQUESTED = "requested"
    REVEAL = "reveal"
    MONTAGE = "montage"
    BREATH = "breath"
    MATCH = "match"
    ACTION = "action"
    SPEECH_ACROSS = "speech_across"
    AUDIO_BRIDGE = "audio_bridge"
    MEMBERSHIP_PLUG = "membership_plug"
    END_SCREEN = "end_screen"
    GAP = "gap"


class DecisionStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    RECOMMENDED = "recommended"
    REQUESTED = "requested"
    BRAND = "brand"
    FALLBACK = "fallback"
    OVERRIDDEN = "overridden"


class AudioKind(StrEnum):
    NONE = "none"  # continuous material: there is no audio edit
    CUT = "cut"  # a splice with a click guard
    CROSSFADE = "crossfade"
    J_CUT = "j_cut"
    L_CUT = "l_cut"
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"


class Curve(StrEnum):
    EQUAL_POWER = "equal_power"
    LINEAR = "linear"
    NONE = "none"


class RenderState(StrEnum):
    NOT_REQUESTED = "not_requested"
    RENDERED = "rendered"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


class BookendKind(StrEnum):
    MEMBERSHIP_PLUG = "membership_plug"
    END_SCREEN = "end_screen"


class DecisionAction(StrEnum):
    """A person's verdict on one boundary: use this kind, or keep a hard cut."""

    USE = "use"
    HARD_CUT = "hard_cut"


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


@dataclass(frozen=True, slots=True, kw_only=True)
class KindSpec:
    family: Family
    intensity: int
    #: Both pictures are visible at once: the transition needs source handles.
    overlap: bool
    #: ``motion_graphics_engine`` ``OverlayKind`` value for a graphic transition.
    overlay: str | None = None
    #: Needs camera-motion evidence on the boundary (whip pan, motion match).
    motion: bool = False
    #: Moves or covers the whole picture (checked against on-screen text).
    moves_frame: bool = False
    #: Darkens/whites out or covers the picture (never through a running sentence).
    interrupts: bool = False
    alignment: Alignment = Alignment.CENTER


K = TransitionKind
F = Family
LIBRARY: Final[dict[TransitionKind, KindSpec]] = {
    K.HARD_CUT: KindSpec(family=F.CUT, intensity=0, overlap=False),
    K.MATCH_CUT: KindSpec(family=F.CUT, intensity=0, overlap=False),
    K.ACTION_CUT: KindSpec(family=F.CUT, intensity=0, overlap=False),
    K.JUMP_CUT: KindSpec(family=F.CUT, intensity=0, overlap=False),
    K.CROSS_DISSOLVE: KindSpec(family=F.BLEND, intensity=1, overlap=True),
    K.FADE_IN: KindSpec(
        family=F.BLEND, intensity=1, overlap=False, interrupts=True, alignment=Alignment.START
    ),
    K.FADE_OUT: KindSpec(
        family=F.BLEND, intensity=1, overlap=False, interrupts=True, alignment=Alignment.END
    ),
    K.DIP_TO_BLACK: KindSpec(family=F.BLEND, intensity=1, overlap=True, interrupts=True),
    K.DIP_TO_WHITE: KindSpec(family=F.BLEND, intensity=2, overlap=True, interrupts=True),
    K.MOTION_MATCH: KindSpec(family=F.MOVEMENT, intensity=1, overlap=True, motion=True),
    K.SLIDE: KindSpec(family=F.MOVEMENT, intensity=2, overlap=True, moves_frame=True),
    K.PUSH: KindSpec(family=F.MOVEMENT, intensity=2, overlap=True, moves_frame=True),
    K.ZOOM: KindSpec(family=F.MOVEMENT, intensity=2, overlap=True),
    K.WHIP_PAN: KindSpec(
        family=F.MOVEMENT, intensity=3, overlap=True, motion=True, moves_frame=True
    ),
    K.BLUR: KindSpec(family=F.STYLISED, intensity=2, overlap=True),
    K.FLASH: KindSpec(family=F.STYLISED, intensity=3, overlap=True, interrupts=True),
    K.GLITCH: KindSpec(family=F.STYLISED, intensity=4, overlap=True, interrupts=True),
    K.WIPE: KindSpec(
        family=F.GRAPHIC,
        intensity=2,
        overlap=False,
        overlay="wipe",
        moves_frame=True,
        interrupts=True,
    ),
    K.SHAPE: KindSpec(
        family=F.GRAPHIC, intensity=3, overlap=False, overlay="shape", interrupts=True
    ),
    K.MASK_REVEAL: KindSpec(
        family=F.GRAPHIC, intensity=3, overlap=False, overlay="mask", interrupts=True
    ),
    K.ZOOM_TUNNEL: KindSpec(
        family=F.GRAPHIC, intensity=3, overlap=False, overlay="zoom_tunnel", interrupts=True
    ),
    K.PIXEL_DISSOLVE: KindSpec(
        family=F.GRAPHIC, intensity=2, overlap=False, overlay="pixel_dissolve", interrupts=True
    ),
}
KIND_NAMES: Final = tuple(k.value for k in TransitionKind)
#: Kinds that only label a straight cut (no effect is applied).
CUT_KINDS: Final = frozenset(k for k, s in LIBRARY.items() if s.family is Family.CUT)
#: Beat functions (story_architecture) that make the incoming clip a reveal.
REVEAL_FUNCTIONS: Final = frozenset({"payoff", "turning_point"})
