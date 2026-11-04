"""The configurable design tokens (every knob of the visual identity) and the format profiles.

The token tree ``StyleTokens`` is plain data: nested frozen dataclasses whose user-facing fields
carry ``dataclasses.field`` metadata for a future settings UI (``label``, ``help``, ``unit``,
``min``, ``max``, ``step``, ``choices``, ``kind``, ``advanced``). ``problems()`` is DERIVED from
that metadata (plus a few cross-field checks), so the ranges a UI shows and the ranges the engine
enforces cannot drift apart.

Units: sizes, strokes, radii, glows, shadows and paddings are reference pixels (``ref_px``):
pixels on an output whose short side is 1080 px (``values.REFERENCE_SHORT_SIDE``). Colours in the
palette are ``#RRGGBB``; everywhere else a colour is the NAME of a palette entry, so no module can
introduce a colour the identity does not own. Durations are seconds (a design duration, never a
time address).

The CHANNEL values live in ``identity.py`` (``MESC_IDENTITY_V1.tokens``); the defaults here are
the same values, so a fresh ``StyleTokens()`` already is the MEsc look. Format profiles inherit
everything and override only the dotted paths they name, each with a reason.
"""

from dataclasses import MISSING, Field, dataclass, field, fields, is_dataclass, replace
from types import MappingProxyType
from typing import Any, Final

from media_house.modules.visual_style_engine.domain.color import is_hex
from media_house.modules.visual_style_engine.domain.errors import InvalidStyleConfig
from media_house.modules.visual_style_engine.domain.values import ADDED_IN, Confidence

#: Same shape as the media library's ``JsonValue`` (aliases are structural).
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

FACE_KEYS: Final = ("bangers_400", "poppins_400", "poppins_700", "poppins_800", "anton_400")
EMOJI_FACE_KEYS: Final = ("noto_color_emoji",)
#: The transition vocabulary the identity speaks about (transition_engine's kind names).
TRANSITION_NAMES: Final = (
    "hard_cut",
    "match_cut",
    "action_cut",
    "jump_cut",
    "cross_dissolve",
    "fade_in",
    "fade_out",
    "dip_to_black",
    "dip_to_white",
    "motion_match",
    "slide",
    "push",
    "zoom",
    "whip_pan",
    "blur",
    "flash",
    "glitch",
    "wipe",
    "shape",
    "mask_reveal",
    "zoom_tunnel",
    "pixel_dissolve",
)
TYPE_ROLES: Final = ("display", "headline", "body", "caption", "number", "label")
EASING_NAMES: Final = ("pop", "enter", "exit", "move", "linear")
#: Every layout (16:9, 9:16, 1:1) defines exactly these zones (plus its keep-outs).
ZONE_NAMES: Final = (
    "title_band",
    "corner_badge",
    "sponsor_label",
    "credit_label",
    "callout_left",
    "callout_right",
    "lower_third_band",
    "caption_band",
    "card_safe",
)
CASES: Final = ("upper", "sentence", "as_written")
ENTRY_PATTERNS: Final = ("pop_in", "slide_in", "fade_in", "wipe_in", "cut")
EXIT_PATTERNS: Final = ("pop_out", "slide_out", "fade_out", "cut")
BACKGROUNDS: Final = ("none", "panel", "plate")
PRICE_FORMATS: Final = ("comma_euro_symbol", "comma_eur")


def _k[T](default: T, label: str, **meta: object) -> T:
    """A user-facing knob: its default and its settings-UI metadata."""
    found: T = field(default=default, metadata={"label": label, **meta})
    return found


def _color(default: str, label: str, help: str = "") -> str:  # noqa: A002
    return _k(default, label, help=help, kind="color")


def _ref(default: str, label: str, help: str = "") -> str:  # noqa: A002
    return _k(default, label, help=help, kind="color_ref", choices=PALETTE_NAMES)


def _px(default: float, label: str, lo: float, hi: float, help: str = "") -> float:  # noqa: A002
    return _k(default, label, help=help, unit="ref_px", min=lo, max=hi, step=1.0)


def _share(default: float, label: str, help: str = "") -> float:  # noqa: A002
    return _k(default, label, help=help, unit="fraction", min=0.0, max=1.0, step=0.05)


@dataclass(frozen=True, slots=True, kw_only=True)
class Palette:
    """The only colours graphics may use (HEX, measured on channel material where possible)."""

    brand_red: str = _color("#C4131A", "Brand red", "Primary accent (rank-label stroke).")
    brand_red_neon: str = _color("#DE243C", "Neon red", "Studio neon sign 'MEsc'.")
    fire_dark: str = _color("#3F0807", "Fire dark")
    fire_mid: str = _color("#7C2A0F", "Fire mid")
    fire_hot: str = _color("#D0410A", "Fire hot")
    fire_glow: str = _color("#F3BC19", "Fire glow")
    black: str = _color("#000000", "Black", "Outlines, hard shadows, spec cards.")
    white: str = _color("#FDFEFE", "White", "Text.")
    silver_light: str = _color("#ECECEC", "Silver light", "Wordmark gradient top.")
    silver_mid: str = _color("#C1C1C1", "Silver mid")
    silver_dark: str = _color("#9B9B9B", "Silver dark", "Wordmark gradient bottom.")
    accent_yellow: str = _color("#F4DE19", "Highlight yellow", "Caption keywords.")
    accent_gold: str = _color("#D5A50A", "Title gold")
    charcoal: str = _color("#302F2F", "Charcoal", "Panels and grid plates.")
    thumbnail_blue: str = _color("#245FA7", "Thumbnail blue", "Thumbnails only.")
    thumbnail_blue_light: str = _color("#3F9AE3", "Thumbnail blue light", "Thumbnails only.")
    mascot_cyan: str = _color("#3ADCEE", "Mascot cyan", "Wolf eyes; tiny accents only.")
    positive_green: str = _color("#01D402", "Positive green")
    versus_red: str = _color("#B60F00", "Versus red")
    versus_blue: str = _color("#055183", "Versus blue")


PALETTE_NAMES: Final = tuple(f.name for f in fields(Palette))


@dataclass(frozen=True, slots=True, kw_only=True)
class Semantic:
    """Meaning -> palette entry. Consumers ask for a meaning, never for a HEX."""

    highlight: str = _ref("accent_yellow", "Keyword highlight")
    accent: str = _ref("brand_red", "Accent")
    title_accent: str = _ref("accent_gold", "Title accent")
    panel: str = _ref("charcoal", "Panel")
    glow: str = _ref("white", "Glow")
    price_fill: str = _ref("accent_yellow", "Price tag fill")
    price_text: str = _ref("black", "Price tag text")
    positive: str = _ref("positive_green", "Positive")
    negative: str = _ref("versus_red", "Negative")
    warning: str = _ref("fire_glow", "Warning")
    chart_a: str = _ref("versus_red", "Chart series A")
    chart_b: str = _ref("versus_blue", "Chart series B")
    chart_c: str = _ref("accent_yellow", "Chart series C")
    chart_d: str = _ref("silver_mid", "Chart series D")


@dataclass(frozen=True, slots=True, kw_only=True)
class TypeRole:
    face: str = _k("poppins_700", "Font", kind="font", choices=FACE_KEYS)
    size: float = _px(48.0, "Size", 16.0, 240.0)
    tracking: float = _k(0.0, "Letter spacing", unit="em", min=-0.05, max=0.2, step=0.01)
    leading: float = _k(1.15, "Line spacing", unit="ratio", min=0.9, max=1.6, step=0.05)
    case: str = _k("as_written", "Capitalisation", choices=CASES)


@dataclass(frozen=True, slots=True, kw_only=True)
class Typography:
    display: TypeRole = field(
        default_factory=lambda: TypeRole(
            face="bangers_400", size=120.0, tracking=0.02, leading=1.0, case="upper"
        )
    )
    headline: TypeRole = field(
        default_factory=lambda: TypeRole(face="poppins_800", size=88.0, leading=1.05)
    )
    body: TypeRole = field(default_factory=lambda: TypeRole(face="poppins_700", size=44.0))
    caption: TypeRole = field(
        default_factory=lambda: TypeRole(
            face="anton_400", size=72.0, tracking=0.05, leading=1.05, case="upper"
        )
    )
    number: TypeRole = field(
        default_factory=lambda: TypeRole(face="poppins_800", size=96.0, leading=1.0)
    )
    label: TypeRole = field(
        default_factory=lambda: TypeRole(face="poppins_700", size=30.0, tracking=0.04, case="upper")
    )
    min_size: float = _px(
        28.0, "Smallest text", 16.0, 60.0, "Readability floor on a phone (ref px)."
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Graphics:
    text_stroke: float = _px(6.0, "Text outline", 0.0, 20.0, "Thick black outline on text.")
    graphic_stroke: float = _px(4.0, "Graphic outline", 0.0, 20.0)
    rank_stroke: float = _px(10.0, "Rank label stroke", 0.0, 30.0)
    corner_radius: float = _px(12.0, "Corner radius", 0.0, 60.0)
    panel_opacity: float = _share(0.9, "Panel opacity")
    panel_padding: float = _px(28.0, "Panel padding", 0.0, 120.0)
    spacing: float = _px(24.0, "Spacing unit", 4.0, 96.0, "Gap between graphic elements.")
    hard_shadow_offset: float = _px(6.0, "Hard shadow offset", 0.0, 30.0)
    hard_shadow_opacity: float = _share(0.85, "Hard shadow opacity")
    soft_shadow_blur: float = _px(24.0, "Soft shadow blur", 0.0, 120.0)
    soft_shadow_opacity: float = _share(0.55, "Soft shadow opacity")
    sticker_glow: float = _px(8.0, "Sticker glow", 0.0, 40.0, "White outer glow of cut-outs.")
    sticker_glow_opacity: float = _share(0.9, "Sticker glow opacity")
    sticker_tilt_min: float = _k(
        5.0, "Sticker tilt min", unit="degrees", min=0.0, max=20.0, step=1.0
    )
    sticker_tilt_max: float = _k(
        10.0, "Sticker tilt max", unit="degrees", min=0.0, max=20.0, step=1.0
    )
    title_glow: float = _px(28.0, "Title glow", 0.0, 120.0)
    title_glow_opacity: float = _share(0.6, "Title glow opacity")
    pip_border: float = _px(4.0, "Picture-in-picture border", 0.0, 20.0)
    max_simultaneous: int = _k(
        2,
        "Graphics at once",
        min=1,
        max=5,
        step=1,
        help="Overlays visible together (captions and labels excluded).",
    )
    price_format: str = _k(
        "comma_euro_symbol",
        "Price format",
        choices=PRICE_FORMATS,
        help="comma_euro_symbol = 15,99 €; comma_eur = 15,99 EUR.",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionLook:
    role: str = _k("caption", "Type role", choices=TYPE_ROLES)
    size: float = _px(72.0, "Size", 28.0, 160.0)
    case: str = _k("upper", "Capitalisation", choices=CASES)
    fill: str = _ref("white", "Text colour")
    stroke: str = _ref("black", "Outline colour")
    stroke_width: float = _px(3.0, "Outline width", 0.0, 20.0)
    shadow: bool = _k(True, "Soft shadow")
    highlight: str = _ref("accent_yellow", "Keyword colour")
    max_chars_per_line: int = _k(22, "Characters per line", min=8, max=42, step=1)
    max_lines: int = _k(2, "Lines", min=1, max=3, step=1)
    zone: str = _k("caption_band", "Zone", kind="zone", choices=ZONE_NAMES)
    strip_trailing_period: bool = _k(True, "Drop the final full stop")


@dataclass(frozen=True, slots=True, kw_only=True)
class Captions:
    word_group: CaptionLook = field(default_factory=CaptionLook)
    line: CaptionLook = field(
        default_factory=lambda: CaptionLook(
            role="headline",
            size=56.0,
            case="sentence",
            stroke_width=5.0,
            max_chars_per_line=32,
            zone="title_band",
        )
    )
    default_look: str = _k("word_group", "Default caption look", choices=("word_group", "line"))


@dataclass(frozen=True, slots=True, kw_only=True)
class TextStyle:
    role: str = _k("body", "Type role", choices=TYPE_ROLES)
    size: float = _px(44.0, "Size", 16.0, 240.0)
    case: str = _k("as_written", "Capitalisation", choices=CASES)
    fill: str = _ref("white", "Text colour")
    stroke: str = _ref("black", "Outline colour")
    stroke_width: float = _px(6.0, "Outline width", 0.0, 30.0)
    background: str = _k("none", "Background", choices=BACKGROUNDS)
    panel: str = _ref("charcoal", "Panel colour")
    glow: bool = _k(False, "Glow")
    zone: str = _k("lower_third_band", "Zone", kind="zone", choices=ZONE_NAMES)
    max_lines: int = _k(2, "Lines", min=1, max=6, step=1)
    max_chars_per_line: int = _k(28, "Characters per line", min=4, max=60, step=1)
    entry: str = _k("pop_in", "Entry animation", choices=ENTRY_PATTERNS)


@dataclass(frozen=True, slots=True, kw_only=True)
class Titles:
    opening_title: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="headline",
            size=110.0,
            fill="accent_gold",
            stroke_width=4.0,
            glow=True,
            zone="card_safe",
            max_chars_per_line=18,
            entry="fade_in",
        )
    )
    chapter_title: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="headline",
            size=96.0,
            case="upper",
            stroke="brand_red",
            stroke_width=10.0,
            max_lines=1,
            max_chars_per_line=14,
        )
    )
    lower_third: TextStyle = field(
        default_factory=lambda: TextStyle(stroke_width=0.0, background="panel")
    )
    callout: TextStyle = field(
        default_factory=lambda: TextStyle(
            size=40.0, stroke_width=4.0, zone="callout_left", max_lines=3, max_chars_per_line=24
        )
    )
    price_tag: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="number",
            size=64.0,
            fill="black",
            stroke_width=0.0,
            background="panel",
            panel="accent_yellow",
            zone="callout_right",
            max_lines=1,
            max_chars_per_line=12,
        )
    )
    spec_card: TextStyle = field(
        default_factory=lambda: TextStyle(
            size=40.0,
            stroke_width=0.0,
            background="panel",
            panel="black",
            zone="card_safe",
            max_lines=4,
            max_chars_per_line=30,
            entry="slide_in",
        )
    )
    cta: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="display", size=72.0, case="upper", max_lines=1, max_chars_per_line=20
        )
    )
    sponsor_label: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="label",
            size=30.0,
            case="upper",
            stroke_width=3.0,
            zone="sponsor_label",
            max_lines=1,
            max_chars_per_line=12,
            entry="cut",
        )
    )
    credit: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="label",
            size=28.0,
            case="upper",
            stroke_width=3.0,
            zone="credit_label",
            max_lines=1,
            max_chars_per_line=30,
            entry="cut",
        )
    )
    end_screen_label: TextStyle = field(
        default_factory=lambda: TextStyle(
            role="display",
            size=80.0,
            case="upper",
            zone="card_safe",
            max_lines=1,
            max_chars_per_line=16,
            entry="cut",
        )
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Motion:
    character: str = _k(
        "punchy",
        "Motion character",
        choices=("punchy", "snappy", "calm"),
        help="punchy = comic pop-ins with overshoot; calm = fades only.",
    )
    entry_easing: str = _k("pop", "Entry easing", kind="easing", choices=EASING_NAMES)
    exit_easing: str = _k("exit", "Exit easing", kind="easing", choices=EASING_NAMES)
    move_easing: str = _k("move", "Move easing", kind="easing", choices=EASING_NAMES)
    entry_pattern: str = _k("pop_in", "Entry pattern", choices=ENTRY_PATTERNS)
    exit_pattern: str = _k("pop_out", "Exit pattern", choices=EXIT_PATTERNS)
    entry_min: float = _k(0.2, "Entry min", unit="seconds", min=0.0, max=2.0, step=0.05)
    entry_max: float = _k(0.45, "Entry max", unit="seconds", min=0.0, max=2.0, step=0.05)
    exit_min: float = _k(0.15, "Exit min", unit="seconds", min=0.0, max=2.0, step=0.05)
    exit_max: float = _k(0.35, "Exit max", unit="seconds", min=0.0, max=2.0, step=0.05)
    emphasis_min: float = _k(0.1, "Emphasis min", unit="seconds", min=0.0, max=2.0, step=0.05)
    emphasis_max: float = _k(0.3, "Emphasis max", unit="seconds", min=0.0, max=2.0, step=0.05)
    stagger: float = _k(0.06, "Stagger", unit="seconds", min=0.0, max=0.5, step=0.01)
    overshoot: float = _k(0.12, "Overshoot", unit="fraction", min=0.0, max=0.4, step=0.01)
    min_hold: float = _k(
        1.2,
        "Shortest hold",
        unit="seconds",
        min=0.3,
        max=5.0,
        step=0.1,
        help="Shortest time a text graphic stays fully visible.",
    )
    intensity_ceiling: int = _k(
        2,
        "Intensity ceiling",
        min=0,
        max=4,
        step=1,
        help="0 = none ... 4 = maximal (same scale as transitions).",
    )
    motion_blur: bool = _k(True, "Motion blur on fast moves")


@dataclass(frozen=True, slots=True, kw_only=True)
class Branding:
    logo_zone: str = _k("corner_badge", "Logo zone", kind="zone", choices=("corner_badge",))
    logo_variant: str = _k("full_color", "Logo variant", choices=("full_color", "white", "black"))
    logo_min_height: float = _px(56.0, "Logo min height", 24.0, 200.0)
    logo_safe_margin: float = _px(16.0, "Logo clear space", 0.0, 80.0)
    logo_opacity: float = _share(0.9, "Logo opacity")
    watermark: bool = _k(False, "Persistent watermark", help="Not observed on the channel.")


@dataclass(frozen=True, slots=True, kw_only=True)
class EndScreen:
    seconds: float = _k(20.0, "End screen length", unit="seconds", min=10.0, max=25.0, step=1.0)
    subscribe_label: str = _k("ABONNIEREN", "Subscribe label")
    watch_next_label: str = _k("WEITER SCHAUEN", "Watch-next label")
    background: str = _k(
        "fire_plate", "Background asset slot", kind="asset_slot", choices=("fire_plate",)
    )
    entry_transition: str = _k(
        "pixel_dissolve", "Entry transition", choices=("pixel_dissolve", "hard_cut")
    )
    membership_label: str = _k("Kanalmitglied werden", "Membership plug label")
    membership_transition: str = _k(
        "zoom_tunnel", "Membership plug entry", choices=("zoom_tunnel", "hard_cut")
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Emoji:
    """Emoji punchlines (identity v2+): drawn ONLY with the bundled emoji face."""

    face: str = _k("noto_color_emoji", "Emoji font", kind="font", choices=EMOJI_FACE_KEYS)


def _names(default: tuple[str, ...], label: str, help: str) -> tuple[str, ...]:  # noqa: A002
    return _k(default, label, help=help, kind="transitions", choices=TRANSITION_NAMES)


@dataclass(frozen=True, slots=True, kw_only=True)
class Transitions:
    """The channel's transition language (identity v2+), read by transition_engine."""

    default: str = _k(
        "hard_cut", "Default transition", choices=TRANSITION_NAMES, help="Hard cuts dominate."
    )
    approved: tuple[str, ...] = _names(
        (
            "hard_cut",
            "match_cut",
            "action_cut",
            "jump_cut",
            "cross_dissolve",
            "fade_in",
            "fade_out",
            "flash",
            "zoom_tunnel",
            "pixel_dissolve",
        ),
        "Approved transitions",
        "Anything else is not used; formats widen it (rankings, haul Shorts: whip, slide).",
    )
    prohibited: tuple[str, ...] = _names(
        ("dip_to_white", "glitch", "wipe", "shape", "mask_reveal"),
        "Prohibited transitions",
        "Never used, not even on request.",
    )
    bookend_only: tuple[str, ...] = _names(
        ("zoom_tunnel", "pixel_dissolve"),
        "Brand bookends only",
        "Only into the membership plug (zoom tunnel) / the end screen (pixel dissolve).",
    )
    duration_min: float = _k(
        0.1, "Transition min", unit="seconds", min=0.0, max=3.0, step=0.05, kind="seconds"
    )
    duration_max: float = _k(
        1.2, "Transition max", unit="seconds", min=0.0, max=3.0, step=0.05, kind="seconds"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleTokens:
    """Every resolved design token. ``problems()`` lists what is out of range (never raises).

    ``emoji`` and ``transitions`` exist from identity v2 on; in v1 they are ``None``."""

    palette: Palette = field(default_factory=Palette)
    semantic: Semantic = field(default_factory=Semantic)
    typography: Typography = field(default_factory=Typography)
    graphics: Graphics = field(default_factory=Graphics)
    captions: Captions = field(default_factory=Captions)
    titles: Titles = field(default_factory=Titles)
    motion: Motion = field(default_factory=Motion)
    branding: Branding = field(default_factory=Branding)
    end_screen: EndScreen = field(default_factory=EndScreen)
    # groups a later identity version added: ``None`` (and not stored) in older versions
    emoji: Emoji | None = field(
        default=None, metadata={"label": "Emoji", ADDED_IN: 2, "group": Emoji}
    )
    transitions: Transitions | None = field(
        default=None, metadata={"label": "Transitions", ADDED_IN: 2, "group": Transitions}
    )

    def problems(self) -> list[str]:
        found = knob_problems(self)
        g, m, t = self.graphics, self.motion, self.transitions
        pairs = [
            (g.sticker_tilt_min, g.sticker_tilt_max, "graphics.sticker_tilt"),
            (m.entry_min, m.entry_max, "motion.entry"),
            (m.exit_min, m.exit_max, "motion.exit"),
            (m.emphasis_min, m.emphasis_max, "motion.emphasis"),
        ]
        if t is not None:
            pairs.append((t.duration_min, t.duration_max, "transitions.duration"))
            both = sorted(set(t.approved) & set(t.prohibited))
            if both:
                found.append(f"transitions: {', '.join(both)} both approved and prohibited")
            if t.default not in t.approved:
                found.append(f"transitions.default: {t.default} is not approved")
        for low, high, what in pairs:
            if low > high:
                found.append(f"{what}: min {low} is above max {high}")
        return found


def knob_problems(group: object, prefix: str = "") -> list[str]:
    """Range, choice and colour checks taken from the field metadata (one source of truth)."""
    found: list[str] = []
    for f in fields(group):  # type: ignore[arg-type]
        value = getattr(group, f.name)
        path = f"{prefix}{f.name}"
        if is_dataclass(value):
            found += knob_problems(value, f"{path}.")
            continue
        meta = f.metadata
        if isinstance(value, int | float) and not isinstance(value, bool):
            if "min" in meta and value < meta["min"]:
                found.append(f"{path}: {value} is below {meta['min']}")
            if "max" in meta and value > meta["max"]:
                found.append(f"{path}: {value} is above {meta['max']}")
        items = value if isinstance(value, tuple) else (value,)
        if "choices" in meta and any(item not in meta["choices"] for item in items):
            found.append(f"{path}: {value!r} is not one of {', '.join(meta['choices'])}")
        if meta.get("kind") == "color" and not (isinstance(value, str) and is_hex(value)):
            found.append(f"{path}: {value!r} is not an upper-case #RRGGBB colour")
        if isinstance(value, str) and not value.strip():
            found.append(f"{path}: must not be empty")
    return found


def knob_fields(group: type[Any], prefix: str = "") -> list[tuple[str, Field[Any]]]:
    """Every leaf knob as (dotted path, field): what a settings UI lists."""
    found: list[tuple[str, Field[Any]]] = []
    for f in fields(group):
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default):
            found += knob_fields(type(default), f"{prefix}{f.name}.")
        elif "group" in f.metadata:  # a group added by a later identity version
            found += knob_fields(f.metadata["group"], f"{prefix}{f.name}.")
        else:
            found.append((f"{prefix}{f.name}", f))
    return found


def leaf_paths(tokens: object, prefix: str = "") -> tuple[str, ...]:
    """The leaf token paths THIS token tree has (a group its identity lacks has none)."""
    found: list[str] = []
    for f in fields(tokens):  # type: ignore[arg-type]
        value = getattr(tokens, f.name)
        if is_dataclass(value):
            found += leaf_paths(value, f"{prefix}{f.name}.")
        elif not (value is None and "group" in f.metadata):
            found.append(f"{prefix}{f.name}")
    return tuple(found)


def present(tokens: StyleTokens, path: str) -> bool:
    """False when ``path`` lies in a token group this identity version does not have (a format
    profile written for a newer identity then skips that override)."""
    value: object = tokens
    for part in path.split("."):
        if value is None:
            return False
        if not is_dataclass(value) or part not in {f.name for f in fields(value)}:
            return True  # unknown paths are reported by ``value_at``
        value = getattr(value, part)
    return value is not None


def value_at(tokens: object, path: str) -> object:
    value = tokens
    for part in path.split("."):
        if not is_dataclass(value) or part not in {f.name for f in fields(value)}:
            raise InvalidStyleConfig(f"unknown token {path!r}")
        value = getattr(value, part)
    return value


def with_value(tokens: StyleTokens, path: str, value: JsonValue) -> StyleTokens:
    """``tokens`` with one leaf replaced (dotted path), the value checked against its type."""
    updated: StyleTokens = _set(tokens, path.split("."), value, path)
    return updated


def _set(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidStyleConfig(f"unknown token {path!r}")
    current = getattr(target, head)
    new = _set(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    return replace(instance, **{head: new})


def _coerce(current: object, value: JsonValue, path: str) -> object:
    if is_dataclass(current):
        raise InvalidStyleConfig(f"{path!r} is a group of tokens, not a single value")
    if isinstance(current, tuple):
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise InvalidStyleConfig(f"{path}: expected a list of names, got {value!r}")
        return tuple(value)
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidStyleConfig(f"{path}: expected {type(current).__name__}, got {value!r}")
    if isinstance(current, float) and isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and path.startswith("palette."):
        return value.upper()
    return value


# --- overrides, format profiles, configuration ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Override:
    """One token changed on purpose: dotted path, new value and WHY (a reason is required)."""

    path: str
    value: JsonValue
    reason: str


@dataclass(frozen=True, slots=True)
class SectionOverride:
    """Overrides that apply to one section of the edit only (``section_id`` from the caller)."""

    section_id: str
    overrides: tuple[Override, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FormatProfile:
    """A format inherits the whole identity and overrides only the preference tokens it names."""

    name: str
    description: str
    native_aspect: str
    evidence: str
    confidence: Confidence
    overrides: tuple[Override, ...] = ()
    version: int = 1


def _o(path: str, value: JsonValue, reason: str) -> Override:
    return Override(path, value, reason)


#: The identity's base approved set (``Transitions().approved``) as override data.
_CHANNEL_TRANSITIONS: Final[list[JsonValue]] = list(Transitions().approved)


_PROFILES: Final = (
    FormatProfile(
        name="generic",
        description="Long-form default: the channel identity without format specifics.",
        native_aspect="16:9",
        evidence="channel-discovery 6, 8 (recurring patterns)",
        confidence=Confidence.HIGH,
    ),
    FormatProfile(
        name="ranking",
        description="Top-N countdown: PLATZ-N rank cards, product stickers, price and specs.",
        native_aspect="16:9",
        evidence="t_BTkcPy5sw, d2ChnPz3FRg, bw7fkdSK1Vk (4.2-5.6 cuts/min)",
        confidence=Confidence.HIGH,
        overrides=(
            _o("motion.intensity_ceiling", 3, "rank changes are marked (carousel, whip, pop-ins)"),
            _o("graphics.max_simultaneous", 3, "rank card, sticker and price tag appear together"),
            _o(
                "transitions.approved",
                [*_CHANNEL_TRANSITIONS, "slide", "whip_pan"],
                "carousel slide between ranks (d2ChnPz3FRg), whip/motion blur (t_BTkcPy5sw)",
            ),
        ),
        version=2,
    ),
    FormatProfile(
        name="brochure_review",
        description="Retail brochure walkthrough: face-cam PiP, page on the right, static.",
        native_aspect="16:9",
        evidence="9etWVDZnyOs (static layout, no effects during the walkthrough)",
        confidence=Confidence.MEDIUM,
        overrides=(
            _o("motion.intensity_ceiling", 1, "high restraint: static layout, page changes only"),
            _o("motion.character", "calm", "the page is the content; graphics must not distract"),
        ),
    ),
    FormatProfile(
        name="shopping_tour",
        description="Chronological store tour with friends, wide-angle POV, title card.",
        native_aspect="16:9",
        evidence="dru73YpbQ4Y (7.0 cuts/min, gold title card, glitch)",
        confidence=Confidence.MEDIUM,
        overrides=(_o("motion.intensity_ceiling", 3, "energetic group footage, 7 cuts/min"),),
    ),
    FormatProfile(
        name="unboxing",
        description="Studio desk unboxing, item by item, sponsor thanks.",
        native_aspect="16:9",
        evidence="OkjnKFjRFt4 (4.1 cuts/min, WERBUNG top right)",
        confidence=Confidence.MEDIUM,
    ),
    FormatProfile(
        name="compilation",
        description="Assortment / product-video showcase, where-to-buy guides.",
        native_aspect="16:9",
        evidence="_Ka5c6jMYRg, ZUSKEXTAuKI, tDi6cJFBP0s (credits top right)",
        confidence=Confidence.MEDIUM,
    ),
    FormatProfile(
        name="nye_uncut",
        description="Midnight single take: no story intervention, minimal graphics.",
        native_aspect="16:9",
        evidence="y_mj09m0XB4 (one continuous take, no speech)",
        confidence=Confidence.MEDIUM,
        overrides=(
            _o("motion.intensity_ceiling", 1, "a single uncut take; the fireworks are the show"),
            _o("graphics.max_simultaneous", 1, "nothing may cover the sky"),
            _o("motion.character", "calm", "no punchy graphics inside the take"),
        ),
    ),
    FormatProfile(
        name="shorts_meme",
        description="7 s loopable meme: one sentence-case line in the upper third from frame 1.",
        native_aspect="9:16",
        evidence="zIxejdVAa0E, P_L7KHcB-_Y",
        confidence=Confidence.HIGH,
        overrides=(_o("captions.default_look", "line", "memes use one static line, not words"),),
    ),
    FormatProfile(
        name="shorts_haul",
        description="Rapid haul montage with running VO and word-group captions.",
        native_aspect="9:16",
        evidence="nsIVE20ixMM (31.5 cuts/min), hPWgWyzYtq8",
        confidence=Confidence.MEDIUM,
        overrides=(
            _o("motion.intensity_ceiling", 4, "the fastest format of the channel"),
            _o("captions.word_group.size", 84.0, "vertical phone viewing needs larger captions"),
            _o(
                "transitions.approved",
                [*_CHANNEL_TRANSITIONS, "slide", "whip_pan"],
                "rapid haul montage (nsIVE20ixMM, 31.5 events/min) moves between items",
            ),
        ),
        version=2,
    ),
    FormatProfile(
        name="shorts_product_test",
        description="Product title card, then the raw night test (sponsored or Pyroland).",
        native_aspect="9:16",
        evidence="xCoOYkqzgDo, y1q4lpEEFcI (2.9-3.7 cuts/min)",
        confidence=Confidence.MEDIUM,
        overrides=(
            _o("captions.word_group.size", 80.0, "vertical phone viewing needs larger captions"),
        ),
    ),
    FormatProfile(
        name="shorts_versus",
        description="A vs B poll: grid plate, VS, clock, red/blue result split.",
        native_aspect="9:16",
        evidence="r8A57tnbyKA",
        confidence=Confidence.MEDIUM,
        overrides=(_o("motion.intensity_ceiling", 3, "the poll is a motion graphic"),),
    ),
)

PROFILES: Final = MappingProxyType({p.name: p for p in _PROFILES})
DEFAULT_PROFILE: Final = "generic"
#: Story formats AND editing profiles (they share names) -> format profile.
STORY_FORMAT_PROFILE: Final = MappingProxyType(
    {
        "generic": "generic",
        "top_n": "ranking",
        "product_introduction": "compilation",
        "review": "brochure_review",
        "unboxing": "unboxing",
        "talking_head": "brochure_review",
        "tutorial": "generic",
        "interview": "generic",
        "podcast": "generic",
        "documentary": "generic",
        "montage": "compilation",
        "short": "shorts_haul",
    }
)
#: No profile and no story format (the no-speech path): the output aspect decides.
ASPECT_PROFILE: Final = MappingProxyType({"9:16": "shorts_meme"})
#: Output size when the caller names none: the profile's native aspect at 1080 short side.
DEFAULT_OUTPUT: Final = MappingProxyType(
    {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleConfig:
    """What a project asks for. Precedence: identity -> format profile -> ``overrides`` (project)
    -> ``section_overrides`` (one section). Each step touches only the paths it names."""

    profile: str | None = None
    overrides: tuple[Override, ...] = ()
    section_overrides: tuple[SectionOverride, ...] = ()
    sponsored: bool = False

    def profile_name(self, story_format: str | None = None, aspect: str | None = None) -> str:
        """Explicit profile -> story format / editing profile -> output aspect -> ``generic``.

        The no-speech path has no story format: a vertical output then gets ``shorts_meme``
        (the most common speech-free Short), anything else ``generic``."""
        name = (
            self.profile
            or STORY_FORMAT_PROFILE.get(story_format or "")
            or ASPECT_PROFILE.get(aspect or "", DEFAULT_PROFILE)
        )
        if name not in PROFILES:
            raise InvalidStyleConfig(
                f"unknown format profile {name!r}; known: {', '.join(sorted(PROFILES))}"
            )
        return name
