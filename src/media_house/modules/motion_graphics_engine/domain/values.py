"""Versions, document identity and the vocabularies of the motion graphics engine."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from media_house.core.domain import Rational

#: Shape of a stored ``graphics_plan`` document. Bump on any field change.
SCHEMA_VERSION: Final = 1
#: Planning rules (brief -> requests). Bump when the same brief plans differently.
#: 2: cards at the spoken fact, every price once, not over on-screen text; 3: no label slivers;
#: 4: a spoken whole-euro price is the shown 'x,9y' one; 5: the end screen is appended
PLANNER_VERSION: Final = 5
#: Layout and animation compiler. Bump when the same request draws or moves differently: it
#: changes every render fingerprint, so old renders are not reused for a new look.
COMPILER_VERSION: Final = 1
#: Vocabulary of kinds, events and properties (additions are additive).
VOCABULARY_VERSION: Final = 1
#: Schema of the configurable knobs in ``profile.py``.
PROFILE_VERSION: Final = 1
DOCUMENT_TYPE: Final = "graphics_plan"
RESULT_OPERATION: Final = "graphics_plan"
RENDER_OPERATION: Final = "motion_graphic_render"
RENDER_VERSION: Final = 1
#: Library tag on every rendered graphic, so all renders can be listed.
RENDER_TAG: Final = "motion_graphic"


class GraphicKind(StrEnum):
    """Every graphic the engine builds. One shared model, one layout engine, one compiler."""

    TITLE = "title"
    SECTION_TITLE = "section_title"
    LOWER_THIRD = "lower_third"
    CALLOUT = "callout"
    TEXT_PANEL = "text_panel"
    HIGHLIGHT_PANEL = "highlight_panel"
    PRICE_TAG = "price_tag"
    RANK_CARD = "rank_card"
    SILHOUETTE_TEASER = "silhouette_teaser"
    SPEC_CARD = "spec_card"
    COMPARISON = "comparison"
    PRODUCT_STICKER = "product_sticker"
    FEATURE_LIST = "feature_list"
    STEP_LIST = "step_list"
    CHART = "chart"
    TIMELINE = "timeline"
    ARROW = "arrow"
    TRACKING_LABEL = "tracking_label"
    CTA = "cta"
    SPONSOR_LABEL = "sponsor_label"
    INTRO = "intro"
    END_SCREEN = "end_screen"
    LOGO_STING = "logo_sting"
    MEMBERSHIP_PLUG = "membership_plug"
    PIP_FRAME = "pip_frame"
    #: Executor only: caption_engine decides every word and its timing.
    CAPTION = "caption"
    #: Executor only: transition_engine decides kind, duration and alignment.
    TRANSITION_OVERLAY = "transition_overlay"


class Origin(StrEnum):
    """Who asked for the graphic (motion decides HOW, never WHETHER for editorial items)."""

    EDITORIAL = "editorial"
    CAPTION = "caption"
    TRANSITION = "transition"
    BRAND = "brand"


class Intent(StrEnum):
    """What an editorial GRAPHIC request wants (editing's ``EffectIntent``)."""

    EXPLAIN = "explain"
    SHOW_TEXT = "show_text"


class ChartKind(StrEnum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    PROGRESS = "progress"
    COUNTER = "counter"


class OverlayKind(StrEnum):
    """Graphic transition overlays transition_engine may ask for."""

    WIPE = "wipe"
    SHAPE = "shape"
    MASK = "mask"
    ZOOM_TUNNEL = "zoom_tunnel"
    PIXEL_DISSOLVE = "pixel_dissolve"


class OverlayDirection(StrEnum):
    """Where a WIPE overlay travels (``None`` on the request = ``RIGHT``, the original wipe)."""

    RIGHT = "right"  # in from the left edge, out at the right
    LEFT = "left"
    UP = "up"  # in from the bottom edge, out at the top
    DOWN = "down"


class Anchor(StrEnum):
    """Where a graphic sits inside its zone."""

    TOP_LEFT = "top_left"
    TOP = "top"
    TOP_RIGHT = "top_right"
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM = "bottom"
    BOTTOM_RIGHT = "bottom_right"


class ElementType(StrEnum):
    TEXT = "text"
    PANEL = "panel"
    IMAGE = "image"
    SHAPE = "shape"


class Prop(StrEnum):
    """Animatable properties (backend-neutral)."""

    X = "x"  # px offset from the laid-out position
    Y = "y"
    SCALE = "scale"
    ROTATION = "rotation"  # degrees, added to the element's own rotation
    OPACITY = "opacity"
    BLUR = "blur"  # px
    REVEAL = "reveal"  # 0 = clipped away (from the reveal edge), 1 = fully shown
    HIGHLIGHT = "highlight"  # 0 = text colour, 1 = highlight colour
    COUNT = "count"  # counter value shown by a text element


class EventKind(StrEnum):
    ENTER_START = "enter_start"
    ENTER_PEAK = "enter_peak"
    EMPHASIS = "emphasis"
    EXIT_START = "exit_start"
    EXIT_END = "exit_end"


class RenderQuality(StrEnum):
    """``preview``: one PNG still of the hold at reduced size; ``final``: the ProRes 4444 MOV."""

    PREVIEW = "preview"
    FINAL = "final"


class ItemStatus(StrEnum):
    OK = "ok"
    #: Not produced; the safe fallback (no graphic) is used and the reason recorded.
    FALLBACK = "fallback"
    #: A capability is missing (renderer, brand asset, cut-out); nothing faked.
    NOT_AVAILABLE = "not_available"
    #: A person rejected it (decision).
    REJECTED = "rejected"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue:
    """One finding (docs/layer4/contracts.md section 6) plus what the spec asks for: the
    property, the expected and the actual value and the recommended correction."""

    code: str
    severity: Severity
    category: QcCategory
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""
    property: str = ""
    expected: str = ""
    actual: str = ""
    fix: str = ""


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    REQUESTED = "requested"
    NOT_AVAILABLE = "not_available"


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    kind: str
    how: InputHow
    asset_id: str | None = None
    identity: str | None = None
    detail: str = ""


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    detail: str = ""


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    """The edit a plan was made for (the edit plan id + its plan version)."""

    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleBinding:
    """The style a plan used (visual_style_engine's ``StyleRef``; ``document_id`` is ``None``
    when the caller supplied an unstored ``ResolvedStyle``)."""

    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str
