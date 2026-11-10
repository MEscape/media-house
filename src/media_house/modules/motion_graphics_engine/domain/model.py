"""The one shared graphic model: request -> composition (elements) -> animation -> plan item.

Times: ``local_*`` = seconds from the start of one graphic, ``timeline_*`` = seconds from the
start of the edit. All exact ``Rational``; frames exist only in ``FrameSpan`` (computed once).
Pixels are output pixels of the style's output size.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.values import (
    Anchor,
    ChartKind,
    EditRef,
    ElementType,
    EventKind,
    GraphicKind,
    InputRecord,
    ItemStatus,
    Origin,
    OverlayDirection,
    OverlayKind,
    Prop,
    QcIssue,
    RenderQuality,
    ResultState,
    StageReport,
    StyleBinding,
)

# --- the request vocabulary (what callers send) -------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class TimedWord:
    """One caption word as caption_engine timed it (local to the caption group)."""

    text: str
    local_start: Rational
    local_end: Rational
    emphasis: bool = False
    #: caption_engine's emphasis level (0 normal, 1 medium, 2 high, 3 critical); it sets the
    #: strength of the word's ``emphasis`` event. ``None`` = derived from ``emphasis``.
    emphasis_level: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TimedEmoji:
    """An emoji punchline drawn right after one caption word, with its own entry time (local to
    the caption group). Drawn only with the style's bundled emoji face (identity v2+)."""

    emoji: str
    #: Index into ``GraphicContent.words`` of the word it follows.
    after_word: int
    local_start: Rational
    #: ``pop_in`` | ``fade_in``; empty = ``pop_in``.
    entry: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SpecLine:
    """One spec-card row, from a real fact: ``label`` ("Schuss") and the value text ("25")."""

    label: str
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DataPoint:
    """A supplied number (charts, counters). Never invented, never altered."""

    label: str
    value: Rational
    unit: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackPoint:
    """A supplied position of a tracked subject: fractions of the output frame."""

    local_at: Rational
    x: float
    y: float


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicContent:
    """What a graphic shows. Every field is optional; each kind reads the ones it needs."""

    headline: str = ""
    body: tuple[str, ...] = ()
    #: Words of ``headline``/``body`` drawn in the highlight colour.
    highlight: tuple[str, ...] = ()
    rank: int | None = None
    #: Exact price in cents; the text is formatted by the style (``price_text``).
    price_cents: int | None = None
    price_text: str = ""
    specs: tuple[SpecLine, ...] = ()
    data: tuple[DataPoint, ...] = ()
    chart: ChartKind | None = None
    #: Library image assets (sticker / silhouette cut-outs, logos, plates). Never cut out here.
    image_asset_ids: tuple[str, ...] = ()
    words: tuple[TimedWord, ...] = ()
    #: Caption look name of the style (``word_group`` | ``line``); empty = the style default.
    look: str = ""
    #: Emoji punchlines of a caption group (identity v2+ styles).
    emojis: tuple[TimedEmoji, ...] = ()
    overlay: OverlayKind | None = None
    #: Travel direction of a ``WIPE`` overlay; ``None`` = ``RIGHT``.
    direction: OverlayDirection | None = None
    #: Where the facts came from (verbatim provenance, e.g. "commerce_facts unit u12").
    source: str = ""
    #: Small context label above the main text ("PREIS", "NUR BIS", "ALLE LINKS").
    eyebrow: str = ""
    #: Preset variant of the kind (``presets.PRESETS[kind].variants``); empty = the default.
    variant: str = ""
    #: Preset emphasis 0 subtle, 1 standard, 2 hero; ``None`` = the preset's default.
    emphasis: int | None = None
    #: Table cells: matrix rows (first row = column heads) or timeline events (lane, date, text).
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Placement:
    """Zone name (empty = the kind's default zone), anchor in the zone, optional track."""

    zone: str = ""
    anchor: Anchor | None = None
    track: tuple[TrackPoint, ...] = ()
    track_ref: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    local_duration: Rational
    #: Where the graphic starts on the edit; ``None`` = not placed yet (render-only use).
    timeline_start: Rational | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AnimationRequest:
    """Entry/exit pattern names from the style vocabulary (``None`` = the style's choice),
    emphasis moments (local) and an intensity 0-4 (bounded by the style's ceiling)."""

    entry: str | None = None
    exit: str | None = None
    emphasis_at: tuple[Rational, ...] = ()
    intensity: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncPoint:
    """A stable reference to an upstream event (word id, beat id) at a local time."""

    ref: str
    local_at: Rational
    label: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicRequest:
    request_id: str
    kind: GraphicKind
    content: GraphicContent
    timing: Timing
    placement: Placement = field(default_factory=Placement)
    animation: AnimationRequest = field(default_factory=AnimationRequest)
    sync: tuple[SyncPoint, ...] = ()
    priority: float = 0.5
    reason: str = ""
    origin: Origin = Origin.EDITORIAL
    section_id: str | None = None


# --- the style as the engine reads it (filled from visual_style_engine by the application) ------


@dataclass(frozen=True, slots=True, kw_only=True)
class FaceFacts:
    key: str
    family: str
    weight: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TypeFacts:
    role: str
    face: str
    size_px: int
    tracking_em: float
    leading: float
    case: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LookFacts:
    """A title/caption look (``TextStyle`` / ``CaptionLook``) with colours as HEX and px sizes."""

    name: str
    role: str
    size_px: int
    case: str
    fill: str
    stroke: str
    stroke_px: int
    background: str
    panel: str
    glow: bool
    zone: str
    max_lines: int
    max_chars: int
    entry: str
    highlight: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneFacts:
    name: str
    kind: str
    accepts: tuple[str, ...]
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EasingFacts:
    name: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionFacts:
    character: str
    entry_easing: str
    exit_easing: str
    move_easing: str
    entry_pattern: str
    exit_pattern: str
    entry_min: Rational
    entry_max: Rational
    exit_min: Rational
    exit_max: Rational
    emphasis_min: Rational
    emphasis_max: Rational
    stagger: Rational
    overshoot: float
    min_hold: Rational
    intensity_ceiling: int
    motion_blur: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicsFacts:
    text_stroke_px: int
    graphic_stroke_px: int
    rank_stroke_px: int
    radius_px: int
    panel_opacity: float
    padding_px: int
    spacing_px: int
    hard_shadow_px: int
    hard_shadow_opacity: float
    soft_shadow_px: int
    soft_shadow_opacity: float
    sticker_glow_px: int
    sticker_glow_opacity: float
    tilt_min: float
    tilt_max: float
    title_glow_px: int
    title_glow_opacity: float
    pip_border_px: int
    max_simultaneous: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EndScreenFacts:
    seconds: Rational
    subscribe_label: str
    watch_next_label: str
    background_slot: str
    membership_label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleFacts:
    """Everything the engine reads from the resolved style; no colour, font, easing or zone of
    its own. ``colors`` holds palette names and semantic meanings, both resolved to HEX."""

    fingerprint: str
    width: int
    height: int
    colors: tuple[tuple[str, str], ...]
    faces: tuple[FaceFacts, ...]
    types: tuple[TypeFacts, ...]
    looks: tuple[LookFacts, ...]
    zones: tuple[ZoneFacts, ...]
    easings: tuple[EasingFacts, ...]
    motion: MotionFacts
    graphics: GraphicsFacts
    end_screen: EndScreenFacts
    min_text_px: int
    default_caption_look: str
    #: (slot, library asset id or None when the brand asset does not exist yet)
    brand_assets: tuple[tuple[str, str | None], ...] = ()
    #: The bundled emoji face key (identity v2+); empty = the style cannot draw emoji.
    emoji_face: str = ""

    def hex(self, name: str) -> str:
        for key, value in self.colors:
            if key == name:
                return value
        raise KeyError(f"unknown colour {name!r}")

    def type(self, role: str) -> TypeFacts:
        found = next((t for t in self.types if t.role == role), None)
        if found is None:
            raise KeyError(f"unknown type role {role!r}")
        return found

    def look(self, name: str) -> LookFacts:
        found = next((lk for lk in self.looks if lk.name == name), None)
        if found is None:
            raise KeyError(f"unknown look {name!r}")
        return found

    def zone(self, name: str) -> ZoneFacts | None:
        return next((z for z in self.zones if z.name == name), None)

    def face(self, key: str) -> FaceFacts:
        found = next((f for f in self.faces if f.key == key), None)
        if found is None:
            raise KeyError(f"unknown face {key!r}")
        return found

    def brand_asset(self, slot: str) -> str | None:
        return next((a for s, a in self.brand_assets if s == slot), None)


@dataclass(frozen=True, slots=True, kw_only=True)
class FaceMetrics:
    """Advance widths of one font face (font units), read from the font file."""

    key: str
    units_per_em: int
    ascender: int
    descender: int
    advances: tuple[tuple[int, int], ...]  # (code point, advance)
    default_advance: int

    def advance(self, char: str) -> int:
        found = self._find(ord(char))
        return self.default_advance if found is None else found

    def has(self, char: str) -> bool:
        """The face draws ``char`` itself (its advance is measured, not the default)."""
        return self._find(ord(char)) is not None

    def _find(self, code: int) -> int | None:
        lo, hi = 0, len(self.advances) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            point, width = self.advances[mid]
            if point == code:
                return width
            if point < code:
                lo = mid + 1
            else:
                hi = mid - 1
        return None


# --- the composition (layout result: layers of elements) --------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def overlaps(self, other: "Rect") -> bool:
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )

    def inside(self, other: "Rect") -> bool:
        return (
            self.left >= other.left
            and self.top >= other.top
            and self.right <= other.right
            and self.bottom <= other.bottom
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TextRun:
    """A word (or group of words) of a line; ``id`` lets animation address it."""

    id: str
    text: str
    emphasis: bool = False
    #: Only emoji: drawn with the emoji face and without the text outline.
    emoji: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TextLine:
    runs: tuple[TextRun, ...]
    width_px: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TextBlock:
    role: str
    face: str
    family: str
    weight: int
    size_px: int
    line_px: int
    tracking_em: float
    align: str  # left | center | right
    fill: str
    highlight: str
    stroke: str | None
    stroke_px: int
    lines: tuple[TextLine, ...]
    look: str = ""
    #: The style's emoji face key when the text contains emoji (else empty).
    emoji_face: str = ""

    @property
    def text(self) -> str:
        return "\n".join(" ".join(r.text for r in line.runs) for line in self.lines)


@dataclass(frozen=True, slots=True, kw_only=True)
class Element:
    """One layer. ``rect`` is absolute (output px); transforms pivot on ``origin``."""

    id: str
    type: ElementType
    rect: Rect
    z: int
    fill: str | None = None
    colour_name: str = ""
    opacity: float = 1.0
    radius_px: int = 0
    stroke: str | None = None
    stroke_px: int = 0
    glow: str | None = None
    glow_px: int = 0
    glow_opacity: float = 0.0
    shadow_px: int = 0  # hard comic shadow offset
    shadow_blur_px: int = 0
    shadow_opacity: float = 0.0
    #: Colour of the hard/soft shadow (the style's black).
    shadow_color: str | None = None
    rotation: float = 0.0
    origin_x: float = 0.5
    origin_y: float = 0.5
    text: TextBlock | None = None
    image_asset_id: str | None = None
    #: SVG path data in element-local px (shapes: arrows, wedges, lines).
    path: str = ""
    #: Reveal edge for ``Prop.REVEAL``: left | right | top | bottom | center.
    reveal_from: str = "left"


@dataclass(frozen=True, slots=True, kw_only=True)
class Composition:
    """The laid-out graphic: zone, its bounding rect and every layer."""

    request_id: str
    kind: GraphicKind
    zone: str | None
    element_kind: str | None  # the style's ElementKind the zone must accept
    rect: Rect
    elements: tuple[Element, ...]
    width: int
    height: int
    full_frame: bool = False


# --- animation (backend-neutral keyframes) ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Keyframe:
    local_at: Rational
    value: float
    #: The style easing name used to arrive at this keyframe from the previous one.
    easing: str = "linear"


@dataclass(frozen=True, slots=True, kw_only=True)
class Track:
    target: str  # element id or run id
    prop: Prop
    keyframes: tuple[Keyframe, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AnimationEvent:
    """A moment music/SFX may sync to. ``timeline_at`` is set when the graphic is placed."""

    graphic_id: str
    kind: EventKind
    local_at: Rational
    timeline_at: Rational | None
    strength: float
    ref: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionSpec:
    entry: str
    exit: str
    intensity: int
    entry_seconds: Rational
    exit_seconds: Rational
    entry_easing: str
    exit_easing: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CompiledAnimation:
    duration: Rational
    motion: MotionSpec
    tracks: tuple[Track, ...]
    events: tuple[AnimationEvent, ...]


# --- the plan item and the plan ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameSpan:
    """Frames computed once at the edge: ``frames = end_frame - start_frame``. For a placed
    graphic ``start_frame``/``end_frame`` are timeline frames and ``timeline_start`` is the
    frame-snapped start; otherwise ``start_frame`` is 0 (local frames)."""

    fps: Rational
    start_frame: int
    end_frame: int
    frames: int
    timeline_start: Rational | None
    duration: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderRecord:
    asset_id: str | None
    created: bool
    quality: RenderQuality
    renderer: str
    renderer_version: str
    composition_digest: str
    render_fingerprint: str
    width: int
    height: int
    frames: int
    pix_fmt: str
    codec: str
    alpha_min: int
    alpha_max: int
    opaque_fraction: float
    #: The item's first frame inside the rendered file (0, or its place in a caption batch).
    asset_start_frame: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedGraphic:
    graphic_id: str
    request: GraphicRequest
    status: ItemStatus
    frames: FrameSpan | None
    composition: Composition | None
    animation: CompiledAnimation | None
    render: RenderRecord | None
    checks: tuple[QcIssue, ...]
    #: sha256 of what produced the item (request + style + compiler versions).
    fingerprint: str
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    planner_version: int
    compiler_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    configuration_fingerprint: str
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicsPlan:
    plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    edit: EditRef | None
    style: StyleBinding | None
    created_at: datetime
    state: ResultState
    anchor_asset_id: str
    width: int
    height: int
    fps: Rational
    graphics: tuple[PlannedGraphic, ...]
    events: tuple[AnimationEvent, ...]
    checks: tuple[QcIssue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int
    #: The timeline end including the appended end screen (after the content); ``None`` = none.
    timeline_end_with_bookends: Rational | None = None
