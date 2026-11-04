"""Transition profiles: every creative number of the module lives here (contracts sec. 7).

A profile is data: nested frozen dataclasses whose user-facing knobs carry ``field`` metadata
(``label``, ``help``, ``unit``, ``min``, ``max``, ``step``, ``choices``, ``kind``) that
``problems()`` enforces, so a future settings UI and the checks cannot drift apart.

Precedence (each step touches only the keys it names): profile defaults -> channel identity
(the style's intensity ceiling and brand bookend transitions) -> format profile -> project
``overrides`` (dotted paths) -> ``section_overrides`` (the incoming clip's section) -> a
person's ``decisions`` per boundary.

Context lists (``contexts.*``) name the transitions a context may receive, in order of
preference; an empty list means a hard cut. Hard cuts stay the default everywhere.
"""

from collections.abc import Mapping
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass, replace
from typing import Any, Final

from media_house.modules.transition_engine.domain.errors import InvalidTransitionConfig
from media_house.modules.transition_engine.domain.values import (
    KIND_NAMES,
    PROFILE_VERSION,
    DecisionAction,
    TransitionKind,
)

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

CURVES: Final = ("equal_power", "linear")
ALIGNMENTS: Final = ("center", "start", "end")


def _k(default: Any, label: str, **meta: Any) -> Any:
    return field(default=default, metadata={"label": label, **meta})


def _s(default: float, label: str, low: float, high: float, step: float = 0.05, **m: Any) -> Any:
    return _k(default, label, unit="seconds", min=low, max=high, step=step, kind="seconds", **m)


def _ms(default: int, label: str, high: int, **meta: Any) -> Any:
    return _k(default, label, unit="ms", min=0, max=high, step=1, **meta)


def _kinds(default: tuple[str, ...], label: str, hint: str = "") -> Any:
    return _k(default, label, choices=KIND_NAMES, kind="kinds", help=hint)


@dataclass(frozen=True, slots=True, kw_only=True)
class Vocabulary:
    allowed: tuple[str, ...] = _kinds(KIND_NAMES, "Approved transitions")
    prohibited: tuple[str, ...] = _kinds(
        (), "Prohibited transitions", "Never used, even when a context asks for them."
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Budget:
    max_intensity: int = _k(
        1, "Maximum intensity", min=0, max=4, step=1, help="Also bounded by the style's ceiling."
    )
    per_minute: float = _k(
        1.0,
        "Transitions per minute",
        unit="per_minute",
        min=0.0,
        max=30.0,
        step=0.5,
        help="Non-cut transitions per minute of the edit (whole-edit cap).",
    )
    min_spacing: float = _s(8.0, "Shortest gap between transitions", 0.0, 120.0, 0.5)
    expressive_spacing: float = _s(
        30.0, "Shortest gap between expressive transitions", 0.0, 600.0, 1.0
    )
    max_expressive: int = _k(
        2, "Expressive transitions per edit", min=0, max=50, step=1, help="Intensity 3 and 4."
    )
    max_repeats: int = _k(
        3,
        "Same expressive kind in a row",
        min=1,
        max=20,
        step=1,
        help="More in a row is reported as a repetitive pattern.",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Contexts:
    requested: tuple[str, ...] = _kinds(("cross_dissolve",), "Editorial request")
    section_change: tuple[str, ...] = _kinds((), "Section change")
    scene_change: tuple[str, ...] = _kinds((), "Scene / location change")
    time_change: tuple[str, ...] = _kinds((), "Time change (same recording)")
    reveal: tuple[str, ...] = _kinds((), "Reveal (payoff / turning point)")
    jump_cut: tuple[str, ...] = _kinds((), "Jump cut")
    montage: tuple[str, ...] = _kinds((), "Montage (short clips)")
    sequence_start: tuple[str, ...] = _kinds((), "Start of the edit")
    sequence_end: tuple[str, ...] = _kinds((), "End of the edit")


@dataclass(frozen=True, slots=True, kw_only=True)
class Bookends:
    follow_style: bool = _k(
        True, "Use the style's brand transitions", help="The style's tokens win when present."
    )
    membership_plug: str = _k(
        "zoom_tunnel", "Into the membership plug", choices=("zoom_tunnel", "hard_cut")
    )
    end_screen: str = _k(
        "pixel_dissolve", "Into the end screen", choices=("pixel_dissolve", "hard_cut")
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Durations:
    dissolve: float = _s(0.5, "Dissolve", 0.04, 3.0)
    dip: float = _s(0.6, "Dip to colour", 0.04, 3.0)
    fade: float = _s(0.5, "Fade in / out", 0.04, 5.0)
    movement: float = _s(0.4, "Movement", 0.04, 2.0)
    stylised: float = _s(0.3, "Stylised", 0.04, 2.0)
    overlay: float = _s(0.6, "Graphic overlay", 0.1, 3.0)
    min_frames: int = _k(2, "Shortest transition", unit="frames", min=1, max=30, step=1)
    breath_factor: float = _k(
        1.5, "Section breath factor", unit="factor", min=1.0, max=3.0, step=0.1
    )
    fast_factor: float = _k(0.6, "Montage factor", unit="factor", min=0.25, max=1.0, step=0.05)
    alignment: str = _k("center", "Alignment on the cut", choices=ALIGNMENTS)


@dataclass(frozen=True, slots=True, kw_only=True)
class Compatibility:
    direction_tolerance: float = _k(
        45.0, "Direction tolerance", unit="degrees", min=0.0, max=180.0, step=5.0
    )
    ghosting_speed: float = _k(
        0.15,
        "Ghosting speed",
        unit="frame_per_second",
        min=0.0,
        max=2.0,
        step=0.01,
        help="Subjects faster than this moving apart make a dissolve ghost.",
        advanced=True,
    )
    composition_shift: float = _k(
        0.25,
        "Composition shift",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.01,
        help="A zoom needs the subjects closer than this on both sides.",
        advanced=True,
    )
    protect_text: bool = _k(True, "Do not move or cover on-screen text")


@dataclass(frozen=True, slots=True, kw_only=True)
class Detection:
    request_tolerance: float = _s(1.0, "Request tolerance", 0.0, 10.0, 0.1, advanced=True)
    montage_max_clip: float = _s(1.5, "Montage clip length", 0.2, 10.0, 0.1)
    time_jump: float = _s(
        30.0, "Jump cut vs. time change", 0.5, 3600.0, 0.5, help="Same-take skips up to this."
    )
    vfr_safety_frames: int = _k(
        1, "VFR handle safety", unit="frames", min=0, max=10, step=1, advanced=True
    )
    speech_reach: float = _s(
        0.3,
        "Speech across a cut",
        0.0,
        2.0,
        0.05,
        help="A word this close to both sides of a cut means the sentence runs across it.",
        advanced=True,
    )
    match_compatibility: float = _k(
        0.75,
        "Match-cut compatibility",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
        help="continuity's shot compatibility from which a cut is labelled a match cut.",
        advanced=True,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Audio:
    cut_crossfade_ms: int = _ms(10, "Click guard at a cut", 200)
    repair_crossfade_ms: int = _ms(
        300, "Crossfade for a continuity repair", 2000, help="Smooths a level/ambience step."
    )
    bridge_crossfade_ms: int = _ms(80, "Crossfade at a J/L cut", 1000)
    follow_picture: bool = _k(True, "Crossfade with an overlapping picture transition")
    max_follow_ms: int = _ms(500, "Longest picture-following crossfade", 3000)
    min_ms: int = _ms(4, "Shortest crossfade", 50)
    speech_guard_ms: int = _ms(20, "Distance kept from a word", 200)
    head_fade_ms: int = _ms(10, "Fade in at the start", 3000)
    tail_fade_ms: int = _ms(500, "Fade out at the end", 5000)
    curve: str = _k("equal_power", "Crossfade curve", choices=CURVES)


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionProfile:
    name: str
    description: str
    evidence: str = ""
    version: int = PROFILE_VERSION
    vocabulary: Vocabulary = field(default_factory=Vocabulary)
    budget: Budget = field(default_factory=Budget)
    contexts: Contexts = field(default_factory=Contexts)
    bookends: Bookends = field(default_factory=Bookends)
    durations: Durations = field(default_factory=Durations)
    compatibility: Compatibility = field(default_factory=Compatibility)
    detection: Detection = field(default_factory=Detection)
    audio: Audio = field(default_factory=Audio)

    def problems(self) -> list[str]:
        found = knob_problems(self)
        if not self.name:
            found.append("name: empty")
        return found


def knob_problems(group: object, prefix: str = "") -> list[str]:
    """Range and choice checks taken from the field metadata (the one source of truth)."""
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
        if "choices" in meta:
            items = value if isinstance(value, tuple) else (value,)
            found += [
                f"{path}: {item!r} is not one of {', '.join(meta['choices'])}"
                for item in items
                if item not in meta["choices"]
            ]
    return found


def knob_fields(cls: type, prefix: str = "") -> list[tuple[str, Mapping[str, Any], Any]]:
    """Every user-facing knob as ``(dotted path, metadata, default)`` (for a settings UI)."""
    out: list[tuple[str, Mapping[str, Any], Any]] = []
    for f in fields(cls):
        if f.default_factory is not MISSING:
            out += knob_fields(f.default_factory().__class__, f"{prefix}{f.name}.")
        elif "label" in f.metadata:
            out.append((f"{prefix}{f.name}", f.metadata, f.default))
    return out


# --- overrides by dotted path (audio_improvement's resolver, adopted per contracts sec. 7) ----
def apply_overrides(
    profile: TransitionProfile, overrides: Mapping[str, JsonValue]
) -> TransitionProfile:
    for path, value in sorted(overrides.items()):
        profile = _override(profile, path.split("."), value, path)
    return profile


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidTransitionConfig(f"unknown setting {path!r}")
    if head in {"name", "version", "description", "evidence"}:
        raise InvalidTransitionConfig(f"{head!r} cannot be overridden")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    return replace(instance, **{head: new})


def _coerce(current: object, value: JsonValue, path: str) -> object:
    """``value`` as the type of the setting it replaces (int is accepted for float settings)."""
    if is_dataclass(current):
        raise InvalidTransitionConfig(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, tuple):
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise InvalidTransitionConfig(f"{path}: expected a list of names, got {value!r}")
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
        raise InvalidTransitionConfig(f"{path}: expected {type(current).__name__}, got {value!r}")
    return float(value) if isinstance(current, float) else value  # type: ignore[arg-type]


# --- built-in profiles ------------------------------------------------------------------------
_BASE: Final = TransitionProfile(
    name="clean_default",
    description="Mostly hard cuts; an occasional short dissolve for a change of time.",
    evidence="spec 4-4 sec. 9 (clean_default)",
    contexts=Contexts(time_change=("cross_dissolve",)),
)


def _p(name: str, description: str, evidence: str, changes: Mapping[str, JsonValue]) -> Any:
    return apply_overrides(
        replace(_BASE, name=name, description=description, evidence=evidence), changes
    )


_GENERIC: Final = (
    _BASE,
    _p(
        "modern_youtube",
        "Clean cuts, subtle movement at section changes, rare expressive moments.",
        "spec 4-4 sec. 9 (modern_youtube)",
        {
            "budget.max_intensity": 3,
            "budget.per_minute": 2.0,
            "contexts.section_change": ["push", "cross_dissolve"],
            "contexts.requested": ["push", "cross_dissolve"],
            "contexts.reveal": ["zoom"],
        },
    ),
    _p(
        "shorts_energetic",
        "Fast cuts with selective zooms, slides and movement in montages.",
        "spec 4-4 sec. 9 (shorts_energetic)",
        {
            "budget.max_intensity": 3,
            "budget.per_minute": 6.0,
            "budget.min_spacing": 3.0,
            "budget.expressive_spacing": 10.0,
            "budget.max_expressive": 3,
            "contexts.montage": ["zoom", "slide", "whip_pan"],
            "contexts.section_change": ["slide"],
            "contexts.reveal": ["zoom"],
            "durations.movement": 0.25,
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "product_review",
        "Clean cuts, smooth reveals, a comparison slide on request; the product stays visible.",
        "spec 4-4 sec. 9 (product_review)",
        {
            "budget.max_intensity": 2,
            "contexts.section_change": ["cross_dissolve"],
            "contexts.requested": ["slide", "cross_dissolve"],
            "contexts.reveal": ["zoom"],
        },
    ),
    _p(
        "tutorial",
        "Clean cuts and screen changes; minimal section transitions for a clear flow.",
        "spec 4-4 sec. 9 (tutorial)",
        {"contexts.section_change": ["cross_dissolve"], "contexts.time_change": []},
    ),
    _p(
        "documentary",
        "Restrained dissolves at scene changes, dips at chapters, fades at both ends.",
        "spec 4-4 sec. 9 (documentary)",
        {
            "contexts.scene_change": ["cross_dissolve"],
            "contexts.time_change": ["dip_to_black", "cross_dissolve"],
            "contexts.section_change": ["dip_to_black", "cross_dissolve"],
            "contexts.sequence_start": ["fade_in"],
            "contexts.sequence_end": ["fade_out"],
            "durations.dissolve": 1.0,
            "durations.dip": 1.2,
            "durations.fade": 1.5,
            "budget.min_spacing": 15.0,
        },
    ),
    _p(
        "cinematic",
        "Carefully chosen dissolves and fades; motion-matched cuts where the camera moves.",
        "spec 4-4 sec. 9 (cinematic)",
        {
            "budget.max_intensity": 2,
            "contexts.scene_change": ["cross_dissolve"],
            "contexts.time_change": ["dip_to_black", "cross_dissolve"],
            "contexts.section_change": ["dip_to_black", "cross_dissolve"],
            "contexts.montage": ["motion_match"],
            "contexts.sequence_start": ["fade_in"],
            "contexts.sequence_end": ["fade_out"],
            "durations.dissolve": 1.2,
            "durations.dip": 1.5,
            "durations.fade": 2.0,
            "budget.min_spacing": 12.0,
        },
    ),
)

#: Channel formats (docs/layer4/channel-discovery.md sec. 4-6; names follow the style profiles).
_CHANNEL: Final = (
    _p(
        "ranking",
        "Top-N countdown: hard cuts inside a rank, a carousel slide (or whip) between ranks.",
        "t_BTkcPy5sw, d2ChnPz3FRg, bw7fkdSK1Vk: 4.2-5.6 cut events/min; carousel slide between"
        " ranks, whip/motion blur, zoom tunnel into the plug, pixel dissolve into the end screen",
        {
            "budget.max_intensity": 3,
            "budget.per_minute": 1.5,
            "budget.min_spacing": 20.0,
            "budget.max_expressive": 3,
            "contexts.section_change": ["slide", "whip_pan"],
            "contexts.requested": ["slide", "whip_pan"],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "brochure_review",
        "Brochure walkthrough: page changes are hard cuts; only the brand bookends.",
        "9etWVDZnyOs: static layout, about 6 cut events/min, no effects during the walkthrough",
        {
            "budget.per_minute": 0.5,
            "contexts.requested": [],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "shopping_tour",
        "Store tour: natural hard and jump cuts; a whip between stores when the camera pans.",
        "dru73YpbQ4Y: 7.0 cut events/min, hard cuts, one TV-glitch title card",
        {
            "budget.max_intensity": 3,
            "contexts.section_change": ["whip_pan"],
            "contexts.requested": ["whip_pan", "cross_dissolve"],
            "contexts.time_change": [],
            "audio.repair_crossfade_ms": 400,
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "unboxing",
        "Desk unboxing: natural and jump cuts, a subtle dissolve into a reveal.",
        "OkjnKFjRFt4: 4.1 cut events/min (plus jump cuts), zoom tunnel into the plug",
        {
            "budget.max_intensity": 2,
            "contexts.reveal": ["cross_dissolve"],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "compilation",
        "Product-video compilation: hard cuts; the manufacturers' own transitions are footage.",
        "_Ka5c6jMYRg, ZUSKEXTAuKI, tDi6cJFBP0s: about 3 cut events/min; flashes inside the"
        " compiled clips are source content",
        {
            "budget.max_intensity": 2,
            "budget.per_minute": 0.5,
            "contexts.requested": ["cross_dissolve"],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "nye_uncut",
        "Midnight single take: no transitions inside the take; the fireworks are the show.",
        "y_mj09m0XB4: one continuous take, 0 cut events/min, no speech",
        {
            "budget.per_minute": 0.0,
            "contexts.requested": [],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "shorts_meme",
        "7 s loopable meme: hard cuts only, no fade at either end (the Short loops).",
        "zIxejdVAa0E, P_L7KHcB-_Y: 0-1 cut events/min",
        {
            "budget.per_minute": 0.0,
            "contexts.requested": [],
            "contexts.time_change": [],
            "audio.head_fade_ms": 0,
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "shorts_haul",
        "Rapid haul montage: mostly hard cuts, selective zooms and slides between items.",
        "nsIVE20ixMM: 31.5 cut events/min, VO 220 wpm",
        {
            "budget.max_intensity": 4,
            "budget.per_minute": 6.0,
            "budget.min_spacing": 3.0,
            "budget.expressive_spacing": 8.0,
            "budget.max_expressive": 4,
            "contexts.montage": ["zoom", "slide", "whip_pan"],
            "contexts.section_change": ["slide"],
            "contexts.time_change": [],
            "durations.movement": 0.2,
            "durations.stylised": 0.2,
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "shorts_product_test",
        "Title card, then the raw night test: hard cuts.",
        "xCoOYkqzgDo, y1q4lpEEFcI: 2.9-3.7 cut events/min",
        {
            "budget.per_minute": 0.5,
            "contexts.requested": [],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
    _p(
        "shorts_versus",
        "A vs B poll: hard cuts; the poll itself is a motion graphic.",
        "r8A57tnbyKA",
        {
            "budget.per_minute": 0.5,
            "contexts.requested": [],
            "contexts.time_change": [],
            "audio.tail_fade_ms": 0,
        },
    ),
)

PROFILES: Final[dict[str, TransitionProfile]] = {p.name: p for p in (*_GENERIC, *_CHANNEL)}
DEFAULT_PROFILE: Final = "clean_default"
#: Story formats and editing profiles (they share names) -> transition profile.
STORY_FORMAT_PROFILE: Final[dict[str, str]] = {
    "generic": "clean_default",
    "top_n": "ranking",
    "product_introduction": "compilation",
    "review": "brochure_review",
    "unboxing": "unboxing",
    "talking_head": "clean_default",
    "tutorial": "tutorial",
    "interview": "clean_default",
    "podcast": "clean_default",
    "documentary": "documentary",
    "montage": "compilation",
    "short": "shorts_haul",
}
#: visual_style_engine format profiles -> transition profile (the same channel taxonomy).
STYLE_PROFILE: Final[dict[str, str]] = {
    "generic": "clean_default",
    **{name: name for name in (p.name for p in _CHANNEL)},
}


@dataclass(frozen=True, slots=True)
class SectionOverride:
    """Overrides for boundaries INTO one section (e.g. ``{"budget.max_intensity": 0}``)."""

    section_id: str
    overrides: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    """A person's choice for one boundary (``transition_id``): use ``kind`` (optionally with a
    duration in frames) or keep a hard cut. Survives replans (ids are stable)."""

    transition_id: str
    action: DecisionAction
    kind: TransitionKind | None = None
    duration_frames: int | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class TransitionConfig:
    #: A built-in profile name; ``None`` = by the story format, then the style's format profile.
    profile: str | None = None
    #: A profile built in code wins over ``profile``.
    custom: TransitionProfile | None = None
    #: Project overrides by dotted path (``{"budget.max_intensity": 2}``).
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)
    section_overrides: tuple[SectionOverride, ...] = ()

    def profile_name(self, story_format: str | None, style_profile: str | None) -> str:
        return (
            self.profile
            or STORY_FORMAT_PROFILE.get(story_format or "")
            or STYLE_PROFILE.get(style_profile or "")
            or DEFAULT_PROFILE
        )

    def resolve(
        self, story_format: str | None = None, style_profile: str | None = None
    ) -> TransitionProfile:
        """The profile with the project overrides applied. Raises ``InvalidTransitionConfig``."""
        if self.custom is not None:
            base = self.custom
        else:
            name = self.profile_name(story_format, style_profile)
            if name not in PROFILES:
                raise InvalidTransitionConfig(
                    f"unknown profile {name!r}; choose one of {', '.join(sorted(PROFILES))}"
                )
            base = PROFILES[name]
        profile = apply_overrides(base, self.overrides)
        problems = profile.problems()
        if problems:
            raise InvalidTransitionConfig("; ".join(problems))
        for section in self.section_overrides:
            self.for_section(profile, section.section_id)  # validated up front
        return profile

    def for_section(self, profile: TransitionProfile, section_id: str | None) -> TransitionProfile:
        for section in self.section_overrides:
            if section.section_id == section_id:
                found = apply_overrides(profile, section.overrides)
                problems = found.problems()
                if problems:
                    raise InvalidTransitionConfig(f"section {section_id}: " + "; ".join(problems))
                return found
        return profile

    def fingerprint(self, profile: TransitionProfile) -> dict[str, object]:
        return {
            "profile": asdict(profile),
            "sections": sorted(
                [s.section_id, sorted((k, repr(v)) for k, v in s.overrides.items())]
                for s in self.section_overrides
            ),
        }
