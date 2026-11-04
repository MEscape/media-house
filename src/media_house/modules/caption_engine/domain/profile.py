"""The configurable knobs of the caption engine (format profiles are data).

Three independent decision areas, each its own group of knobs:

* selection   (``selection``, ``transcript``): which mode and which spoken words are shown;
* presentation (``segmentation``, ``layout``): how shown words are grouped, timed and placed;
* expression  (``emphasis``, ``emoji``, ``animation``): what is highlighted, decorated, moved.

``subtitles`` configures the closed-caption export (always the full transcript, independent of
the burned-in mode). Every user-facing knob carries ``dataclasses.field`` metadata for a future
settings UI (``label``, ``help``, ``unit``, ``min``, ``max``, ``step``, ``choices``, ``kind``,
``advanced``); ``problems()`` is derived from that metadata, so UI ranges and engine checks
cannot drift. Colours, fonts, zones and motion durations are NOT here: they come from the
resolved visual style. Precedence: profile defaults -> channel identity (style look fills the
``0 = style`` knobs) -> format profile -> project ``overrides`` -> ``section_overrides`` ->
per-item person decisions.
"""

from collections.abc import Mapping
from dataclasses import MISSING, Field, asdict, dataclass, field, fields, is_dataclass, replace
from typing import Any, Final

from media_house.modules.caption_engine.domain.errors import InvalidCaptionConfig
from media_house.modules.caption_engine.domain.values import CaptionMode, Cue

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

MODES: Final = tuple(m.value for m in CaptionMode)
LEVELS: Final = ("medium", "high", "critical")


def _k[T](default: T, label: str, **meta: object) -> T:
    found: T = field(default=default, metadata={"label": label, **meta})
    return found


def _num[T: (int, float)](
    default: T,
    label: str,
    lo: T,
    hi: T,
    step: T,
    unit: str = "",
    help: str = "",  # noqa: A002
    advanced: bool = False,
) -> T:
    return _k(default, label, min=lo, max=hi, step=step, unit=unit, help=help, advanced=advanced)


# --- selection -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Selection:
    mode: str = _k(
        CaptionMode.KEY_INFORMATION.value,
        "Caption mode",
        choices=MODES,
        help="full = all speech; selective = important sentences; emphasis_only = strong"
        " phrases; key_information = prices, ranks, specs, names; disabled = none burned in.",
    )
    selective_share: float = _num(
        0.35, "Selective share", 0.05, 0.9, 0.05, "fraction", "Most speech time captioned."
    )
    selective_min_score: float = _num(
        0.45, "Selective threshold", 0.0, 1.0, 0.05, "score", "Weaker sentences stay uncaptioned."
    )
    key_context_words: int = _num(
        1, "Key info context", 0, 3, 1, "words", "Words kept around a fact ('nur 19,99 €')."
    )
    already_captioned_share: float = _num(
        0.3, "Already captioned from", 0.1, 1.0, 0.05, "fraction",
        "Share of the heard words the picture already shows as burned-in captions (video"
        " analysis) from which no captions are added on top.", advanced=True,
    )  # fmt: skip
    already_captioned_window: float = _num(
        1.0, "On-screen text window", 0.0, 3.0, 0.1, "s",
        "How far a spoken word may be from a sampled frame showing the same word.", advanced=True,
    )  # fmt: skip
    #: Story beat functions whose sentences are always candidates in selective mode.
    beat_functions: tuple[str, ...] = ("hook", "payoff", "turning_point", "call_to_action")


@dataclass(frozen=True, slots=True, kw_only=True)
class Transcript:
    language: str = _k(
        "de",
        "Caption language",
        help="Transcripts in another language are not burned in (re-transcription is asked).",
    )
    min_confidence: float = _num(
        0.3, "Confidence floor", 0.0, 0.9, 0.05, "probability", "Less certain words are dropped."
    )
    cut_word_min_share: float = _num(
        0.6, "Cut word kept from", 0.3, 1.0, 0.05, "fraction",
        "A word a cut splits is shown only when this share of it is heard.", advanced=True,
    )  # fmt: skip
    unreliable_share: float = _num(
        0.3, "Re-transcribe above", 0.05, 1.0, 0.05, "fraction",
        "Share of unusable words that asks audio_intelligence to re-transcribe.", advanced=True,
    )  # fmt: skip


# --- presentation ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Segmentation:
    max_words: int = _num(7, "Words per caption", 1, 20, 1, "words")
    max_chars_per_line: int = _num(
        0, "Characters per line", 0, 60, 1, "chars", "0 = the style's caption look."
    )
    max_lines: int = _num(0, "Lines", 0, 3, 1, "lines", "0 = the style's caption look.")
    min_duration: float = _num(1.0, "Shortest caption", 0.2, 3.0, 0.1, "seconds")
    max_duration: float = _num(5.0, "Longest caption", 1.0, 10.0, 0.5, "seconds")
    reading_cps: float = _num(
        17.0, "Reading speed", 8.0, 30.0, 1.0, "chars/s", "German adult reading speed is ~17."
    )
    pause_split: float = _num(
        0.45, "Pause split", 0.1, 2.0, 0.05, "seconds", "A pause this long starts a new caption."
    )
    sentence_boundaries: bool = _k(True, "Break at sentence ends")
    phrase_boundaries: bool = _k(True, "Prefer phrase breaks", help="Commas, conjunctions.")
    lead_in: float = _num(0.0, "Lead-in", 0.0, 0.5, 0.02, "seconds", advanced=True)
    linger: float = _num(
        0.3, "Linger", 0.0, 1.5, 0.05, "seconds", "Stays after the last word.", advanced=True
    )
    min_gap: float = _num(0.08, "Gap between captions", 0.0, 0.5, 0.02, "seconds", advanced=True)
    tolerance: float = _num(
        0.04, "Timing tolerance", 0.0, 0.5, 0.01, "seconds",
        "Allowed drift between a caption and its words.", advanced=True,
    )  # fmt: skip
    overlap: str = _k(
        "trim",
        "Overlapping speech",
        choices=("trim", "drop_later"),
        help="trim = the earlier caption ends early; drop_later = the later one is not shown.",
    )
    break_on_speaker: bool = _k(True, "New caption per speaker")


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    look: str = _k(
        "style", "Caption look", choices=("style", "word_group", "line"),
        help="style = the format profile's default look.",
    )  # fmt: skip
    avoid_subjects: bool = _k(True, "Avoid faces and subjects")
    subject_margin: float = _num(0.02, "Subject margin", 0.0, 0.1, 0.01, "fraction", advanced=True)
    char_width_em: float = _num(
        0.52, "Average glyph width", 0.3, 0.8, 0.02, "em",
        "Width estimate for layout intent (motion measures the real font).", advanced=True,
    )  # fmt: skip


# --- expression ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class LexiconEntry:
    """A lower-case word stem that signals a cue (German channel vocabulary)."""

    stem: str
    cue: Cue


_LEXICON: Final = tuple(
    LexiconEntry(stem=s, cue=c)
    for c, stems in (
        (Cue.WARNING, ("achtung", "vorsicht", "gefährlich", "gefahr", "verboten", "illegal",
                       "niemals", "warnung", "aufpassen")),
        (Cue.CONTRAST, ("aber", "dagegen", "stattdessen", "besser", "schlechter", "billiger",
                        "teurer", "günstiger", "mehr", "weniger", "doppelt", "halb")),
        (Cue.STRONG_CLAIM, ("krass", "brutal", "unglaublich", "wahnsinn", "mega", "absolut",
                            "bester", "beste", "besten", "lautest", "stärkst", "größt", "geil",
                            "hammer", "irre", "heftig", "sensation", "neu", "exklusiv")),
    )
    for s in stems
)  # fmt: skip


@dataclass(frozen=True, slots=True, kw_only=True)
class Emphasis:
    enabled: bool = _k(True, "Emphasis")
    max_per_caption: int = _num(1, "Per caption", 0, 3, 1, "words")
    max_share: float = _num(
        0.15, "Emphasis density", 0.0, 0.5, 0.01, "fraction", "Most shown words emphasised."
    )
    min_word_gap: int = _num(
        3, "Words between", 0, 10, 1, "words", "Emphasised words never sit closer.", advanced=True
    )
    shown_from: str = _k("medium", "Highlight from", choices=LEVELS)
    vocal_weight: float = _num(
        0.3, "Vocal weight", 0.0, 1.0, 0.05, "fraction",
        "Share of the score from delivery (the loudest word is not always the key word).",
        advanced=True,
    )  # fmt: skip
    #: Words that signal a warning, a contrast or a strong claim.
    lexicon: tuple[LexiconEntry, ...] = _LEXICON


@dataclass(frozen=True, slots=True, kw_only=True)
class EmojiRule:
    """A lower-case stem (or ``fact:<kind>``) -> one emoji with its meaning."""

    trigger: str
    emoji: str
    meaning: str


_EMOJIS: Final = tuple(
    EmojiRule(trigger=t, emoji=e, meaning=m)
    for t, e, m in (
        ("fact:price", "💶", "price"),
        ("fact:rank1", "🏆", "first place"),
        ("krass", "🤯", "amazement"),
        ("unglaublich", "🤯", "amazement"),
        ("wahnsinn", "🤯", "amazement"),
        ("explod", "💥", "explosion"),
        ("knall", "💥", "bang"),
        ("feuer", "🔥", "fire"),
        ("heftig", "🔥", "intensity"),
        ("schnell", "⚡", "speed"),
        ("laut", "🔊", "loudness"),
        ("teuer", "💸", "expensive"),
        ("achtung", "⚠️", "warning"),
        ("vorsicht", "⚠️", "warning"),
        ("fehler", "❌", "mistake"),
        ("liebe", "❤️", "love"),
    )
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Emoji:
    enabled: bool = _k(False, "Emojis", help="Off for serious and technical formats.")
    max_per_minute: float = _num(2.0, "Emojis per minute", 0.0, 6.0, 0.5, "per minute")
    min_spacing: float = _num(8.0, "Emoji spacing", 2.0, 60.0, 1.0, "seconds")
    repeat_spacing: float = _num(
        45.0, "Same emoji again after", 5.0, 300.0, 5.0, "seconds", advanced=True
    )
    #: The controlled vocabulary (never free emoji: meaning must not drift).
    vocabulary: tuple[EmojiRule, ...] = _EMOJIS


@dataclass(frozen=True, slots=True, kw_only=True)
class Animation:
    reveal: str = _k(
        "group",
        "Reveal",
        choices=("word_by_word", "group", "static"),
        help="word_by_word = each word appears when spoken; static = no motion.",
    )
    active_word: bool = _k(False, "Active-word highlight", help="The spoken word lights up.")
    intensity: int = _num(
        2, "Intensity", 0, 4, 1, "0-4", "Bounded by the style's intensity ceiling."
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Subtitles:
    """Closed captions (SRT / WebVTT): the full transcript, never shortened by the mode."""

    enabled: bool = _k(True, "Subtitle export")
    speaker_labels: bool = _k(False, "Speaker labels (WebVTT)")
    segmentation: Segmentation = field(
        default_factory=lambda: Segmentation(
            max_words=16,
            max_chars_per_line=42,
            max_lines=2,
            min_duration=1.0,
            max_duration=7.0,
            reading_cps=17.0,
            pause_split=0.7,
            linger=0.5,
        )
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionProfile:
    name: str
    description: str
    #: Channel evidence the defaults follow (recorded in the plan).
    evidence: str = ""
    version: int = 1
    selection: Selection = field(default_factory=Selection)
    transcript: Transcript = field(default_factory=Transcript)
    segmentation: Segmentation = field(default_factory=Segmentation)
    layout: Layout = field(default_factory=Layout)
    emphasis: Emphasis = field(default_factory=Emphasis)
    emoji: Emoji = field(default_factory=Emoji)
    animation: Animation = field(default_factory=Animation)
    subtitles: Subtitles = field(default_factory=Subtitles)

    @property
    def mode(self) -> CaptionMode:
        return CaptionMode(self.selection.mode)

    def problems(self) -> list[str]:
        found = knob_problems(self)
        s = self.segmentation
        if s.min_duration >= s.max_duration:
            found.append("segmentation: the shortest caption must be shorter than the longest")
        sub = self.subtitles.segmentation
        if sub.max_chars_per_line < 20 or sub.max_lines < 1:
            found.append("subtitles.segmentation: lines need a length (>= 20) and a count")
        if sub.min_duration >= sub.max_duration:
            found.append("subtitles.segmentation: shortest must be shorter than longest")
        return found


def knob_problems(group: object, prefix: str = "") -> list[str]:
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
        if "choices" in meta and value not in meta["choices"]:
            found.append(f"{path}: {value!r} is not one of {', '.join(meta['choices'])}")
        if isinstance(value, str) and "label" in meta and not value.strip():
            found.append(f"{path}: must not be empty")
    return found


def knob_fields(group: type[Any], prefix: str = "") -> list[tuple[str, Field[Any]]]:
    """Every leaf knob as (dotted path, field): what a settings UI lists."""
    found: list[tuple[str, Field[Any]]] = []
    for f in fields(group):
        if f.name in ("name", "description", "evidence", "version"):
            continue
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default):
            found += knob_fields(type(default), f"{prefix}{f.name}.")
        else:
            found.append((f"{prefix}{f.name}", f))
    return found


# --- built-in format profiles (channel discovery, docs/layer4/channel-discovery.md) ----------

_NO_BURN_IN = "6 of 6 recent long videos and both owner favourites have no burned-in captions"
_BASE: Final = CaptionProfile(
    name="generic",
    description="Long-form fallback: only spoken key facts are shown.",
    evidence="2023 assortment videos condense captions to key words; recent long videos show"
    " none, so the fallback stays sparse",
)
_DISABLED: Final = replace(_BASE.selection, mode=CaptionMode.DISABLED.value)
_SHORT_SEGMENTS: Final = Segmentation(
    max_words=4,
    min_duration=0.6,
    max_duration=3.0,
    reading_cps=22.0,
    pause_split=0.3,
    linger=0.15,
)
_FULL: Final = replace(_BASE.selection, mode=CaptionMode.FULL_CAPTIONS.value)
_DYNAMIC: Final = Animation(reveal="word_by_word", active_word=True, intensity=3)

PROFILES: Final[dict[str, CaptionProfile]] = {
    p.name: p
    for p in (
        _BASE,
        replace(
            _BASE,
            name="ranking",
            description="Top-N: rank and spec cards (motion graphics) carry the facts.",
            evidence=f"{_NO_BURN_IN} (t_BTkcPy5sw, d2ChnPz3FRg, bw7fkdSK1Vk)",
            selection=_DISABLED,
        ),
        replace(
            _BASE,
            name="brochure_review",
            description="Calm brochure review: prices are price tags, no burned-in captions.",
            evidence=f"{_NO_BURN_IN} (9etWVDZnyOs)",
            selection=_DISABLED,
        ),
        replace(
            _BASE,
            name="shopping_tour",
            description="Real-world tour (owner favourite): no burned-in captions.",
            evidence=f"{_NO_BURN_IN} (dru73YpbQ4Y)",
            selection=_DISABLED,
        ),
        replace(
            _BASE,
            name="unboxing",
            description="Unboxing: no burned-in captions (subtitles exported).",
            evidence=f"{_NO_BURN_IN} (OkjnKFjRFt4)",
            selection=_DISABLED,
        ),
        replace(
            _BASE,
            name="compilation",
            description="Assortment / product-video compilation: spoken facts only.",
            evidence="2023 assortment video (ZUSKEXTAuKI) shows condensed caps word captions;"
            " compilations are mostly manufacturer audio",
            segmentation=replace(_BASE.segmentation, max_words=5),
        ),
        replace(
            _BASE,
            name="nye_uncut",
            description="Midnight uncut: nothing burned in.",
            evidence="y_mj09m0XB4: uncut ambience, no speech captions",
            selection=_DISABLED,
        ),
        replace(
            _BASE,
            name="shorts_haul",
            description="Haul Short: dynamic 2-4 word groups, yellow keyword, emoji punchlines.",
            evidence="nsIVE20ixMM (591k): word-group caps captions, VO 220 wpm; emojis as"
            " punchlines are a recurring pattern",
            selection=_FULL,
            segmentation=_SHORT_SEGMENTS,
            emoji=Emoji(enabled=True, max_per_minute=4.0, min_spacing=5.0),
            animation=_DYNAMIC,
        ),
        replace(
            _BASE,
            name="shorts_product_test",
            description="Sponsored product test Short: full word groups, no emoji.",
            evidence="xCoOYkqzgDo, y1q4lpEEFcI: factual sponsored tests (80 ref px captions)",
            selection=_FULL,
            segmentation=_SHORT_SEGMENTS,
            animation=replace(_DYNAMIC, intensity=2),
        ),
        replace(
            _BASE,
            name="shorts_versus",
            description="Versus poll Short: full word groups when someone speaks.",
            evidence="r8A57tnbyKA: mostly graphic; NEfxufh6YLw caps captions with yellow keyword",
            selection=_FULL,
            segmentation=_SHORT_SEGMENTS,
            emoji=Emoji(enabled=True, max_per_minute=3.0, min_spacing=5.0),
            animation=_DYNAMIC,
        ),
        replace(
            _BASE,
            name="shorts_meme",
            description="Meme Short: one static sentence-case line in the upper third.",
            evidence="zIxejdVAa0E (986k), P_L7KHcB-_Y: one static line from frame 1",
            selection=_FULL,
            segmentation=Segmentation(
                max_words=10, min_duration=1.0, max_duration=6.0, reading_cps=20.0
            ),
            layout=Layout(look="line"),
            emoji=Emoji(enabled=True, max_per_minute=3.0, min_spacing=5.0),
            animation=Animation(reveal="static", active_word=False, intensity=1),
        ),
    )
}
DEFAULT_PROFILE: Final = "generic"
#: Story format -> caption profile (the visual style's mapping, so both modules agree).
STORY_FORMAT_PROFILE: Final[dict[str, str]] = {
    "top_n": "ranking",
    "review": "brochure_review",
    "talking_head": "brochure_review",
    "unboxing": "unboxing",
    "product_introduction": "compilation",
    "montage": "compilation",
    "short": "shorts_haul",
}
#: Without a story format (the no-speech path) a 9:16 output gets the meme profile.
ASPECT_PROFILE: Final[dict[str, str]] = {"9:16": "shorts_meme"}


# --- configuration -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionOverride:
    section_id: str
    overrides: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionConfig:
    """``profile``: a built-in name, a custom profile, or ``None`` (the style's format profile,
    else by story format, else by aspect, else ``generic``). ``overrides`` are project-level
    dotted paths (``{"selection.mode": "full_captions"}``)."""

    profile: str | CaptionProfile | None = None
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)
    section_overrides: tuple[SectionOverride, ...] = ()

    def resolve(
        self,
        *,
        style_profile: str | None = None,
        story_format: str | None = None,
        aspect: str | None = None,
    ) -> CaptionProfile:
        if isinstance(self.profile, CaptionProfile):
            profile = self.profile
        else:
            name = (
                self.profile
                or (style_profile if style_profile in PROFILES else None)
                or STORY_FORMAT_PROFILE.get(story_format or "")
                or ASPECT_PROFILE.get(aspect or "", DEFAULT_PROFILE)
            )
            if name not in PROFILES:
                raise InvalidCaptionConfig(
                    f"unknown profile {name!r}; choose one of {', '.join(sorted(PROFILES))}"
                )
            profile = PROFILES[name]
        profile = apply_overrides(profile, self.overrides)
        problems = profile.problems()
        for section in self.section_overrides:
            problems += [
                f"section {section.section_id}: {p}"
                for p in apply_overrides(profile, section.overrides).problems()
            ]
        if problems:
            raise InvalidCaptionConfig("; ".join(problems))
        return profile

    def for_section(self, profile: CaptionProfile, section_id: str | None) -> CaptionProfile:
        for section in self.section_overrides:
            if section.section_id == section_id:
                return apply_overrides(profile, section.overrides)
        return profile

    def fingerprint(self, profile: CaptionProfile) -> dict[str, object]:
        return {
            "profile": asdict(profile),
            "sections": [
                {"section_id": s.section_id, "overrides": dict(sorted(s.overrides.items()))}
                for s in sorted(self.section_overrides, key=lambda s: s.section_id)
            ],
        }


def apply_overrides(profile: CaptionProfile, overrides: Mapping[str, JsonValue]) -> CaptionProfile:
    """``profile`` with dotted-path overrides applied (``{"emoji.enabled": True}``)."""
    for path, value in sorted(overrides.items()):
        profile = _override(profile, path.split("."), value, path)
    return profile


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidCaptionConfig(f"unknown setting {path!r}")
    if head in ("name", "version", "lexicon", "vocabulary", "beat_functions"):
        raise InvalidCaptionConfig(f"{path!r} cannot be overridden by path")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    return replace(instance, **{head: new})


def _coerce(current: object, value: JsonValue, path: str) -> object:
    if is_dataclass(current):
        raise InvalidCaptionConfig(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        if ok:
            return float(value)  # type: ignore[arg-type]
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidCaptionConfig(f"{path!r} expects {type(current).__name__}, got {value!r}")
    return value
